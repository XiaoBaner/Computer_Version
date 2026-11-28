"""Server-only dialogue facade. Flask owns final-input/turn mapping and delivery.

Start with ``configure(store=..., generator=..., reviewer=..., on_decision=...)``
once per worker. A provider accepts plain, scoped data and returns the four v1
reply fields; the separate reviewer must explicitly return ``True``. Both
adapters own their I/O timeout. No model, caption, audio, HTTP or memory write is
started on import.

Contract notes that the code below enforces:

* only an approved final sentence may be handed to captions and TTS;
* ``cancelled`` is checked before generation, after generation and before
  delivery, and a cancelled turn raises :class:`TurnCancelled` instead of
  emitting a fallback sentence;
* the fallback (pre-approved short text) is used for structure errors, semantic
  boundary hits, an uncertain reviewer, a provider timeout, and time-critical
  routes that must not wait for a model;
* a generated candidate is never persisted here. Saving always needs a source
  turn verified by the server plus a transaction in the memory store.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
import threading
import unicodedata
from typing import Callable, TypedDict

from .memory import DomainError


class ApprovedReply(TypedDict):
    intent: str
    sentences: list[str]
    voice_style: str
    memory_draft: dict | None


class TurnCancelled(Exception):
    """Flask silently abandons the turn, including any pending draft proposal."""


@dataclass(frozen=True)
class _Runtime:
    store: object
    generate: Callable | None
    review: Callable | None
    on_decision: Callable | None
    policy: dict
    #: False only when ``configure()`` was never called with a store: replies keep
    #: working, but memory I/O is refused instead of silently vanishing on restart.
    #: An explicitly injected stand-in (offline demo) is a deliberate choice and
    #: therefore allowed.
    memory_ready: bool


#: Route names the deterministic policy can return. Kept in one place so the
#: versioned rule file and the router cannot drift apart silently.
ROUTES = ("urgent", "urgent_medical", "urgent_others", "safety_check", "medication",
          "diagnosis", "correction", "memory", "recall", "quiet", "suspicion",
          "boundary", "clarify", "practice", "listen")

#: Wording that must be reviewed by the team, so it lives in ``policy.yml``
#: instead of this file. Editing a crisis sentence must not require a code change.
SAFETY_ROUTES = ("urgent", "urgent_medical", "urgent_others", "safety_check",
                 "medication", "diagnosis", "suspicion", "boundary")

#: A pre-approved sentence containing any of these is refused at startup, which
#: turns "someone edited the policy file" into a loud failure instead of a silent
#: boundary leak. Only unambiguous instructions are listed: "不要自行停药" is a
#: correct sentence and must not be rejected for containing 停药.
FORBIDDEN_SAFETY_WORDS = ("建议购买", "可以吃", "加一片", "保证没事",
                          "你患有", "确诊为", "继续练")

_runtime = None
_config_lock = threading.RLock()
logger = logging.getLogger(__name__)


def _validate_policy(policy):
    """Fail fast on a malformed rule file; also called directly by tests."""
    if policy.get("version") != "weekend-v1":
        raise ValueError("不支持的对话规则版本")
    if set(policy.get("intents", ())) != {"listen", "reflect", "clarify", "offer_option", "practice"}:
        raise ValueError("intent 配置与协议不一致")
    if set(policy.get("voice_styles", ())) != {"neutral", "gentle", "practice", "check_in"}:
        raise ValueError("voice_style 配置与协议不一致")
    if not (1 <= policy.get("max_sentences", 0) <= 2
            and policy.get("max_clarifications") == 1
            and 1 <= policy.get("max_chars_per_sentence", 0) <= 200):
        raise ValueError("规则长度与短句协议不一致")
    if set(policy.get("routes", ())) != set(ROUTES):
        raise ValueError("路由配置与分词规则不一致")
    if not policy.get("checks") or not policy.get("priorities"):
        raise ValueError("检查项与优先级必须在规则文件中声明")
    safety = policy.get("safety")
    if not isinstance(safety, dict):
        raise ValueError("规则文件缺少 safety 段：危机与用药话术必须版本化")
    for route in SAFETY_ROUTES:
        entry = safety.get(route)
        if not isinstance(entry, dict):
            raise ValueError(f"safety 缺少 {route}")
        if entry.get("intent") not in policy["intents"] or entry.get("voice_style") not in policy["voice_styles"]:
            raise ValueError(f"safety[{route}] 的枚举不合法")
        sentences = entry.get("sentences")
        if (not isinstance(sentences, list) or not 1 <= len(sentences) <= policy["max_sentences"]
                or any(not isinstance(s, str) or not s.strip() for s in sentences)):
            raise ValueError(f"safety[{route}] 缺少合法短句")
        for sentence in sentences:
            if len(sentence) > policy["max_chars_per_sentence"]:
                raise ValueError(f"safety[{route}] 超出单句长度")
            if any(word in sentence for word in FORBIDDEN_SAFETY_WORDS):
                raise ValueError(f"safety[{route}] 含越界措辞")


def health() -> dict:
    """Small handoff probe: is the crisis wording actually approved yet?"""
    runtime = _config()
    approval = runtime.policy["safety"].get("approval", {})
    return {"policy_version": runtime.policy["version"],
            "crisis_text_approved": bool(approval.get("by") and approval.get("on")),
            "memory_ready": runtime.memory_ready}


def configure(*, store=None, generator=None, reviewer=None, on_decision=None):
    """Startup or explicit test reset. Caller owns the lifecycle of injected stores."""
    global _runtime
    policy = json.loads(Path(__file__).with_name("policy.yml").read_text(encoding="utf-8"))
    _validate_policy(policy)
    approval = policy["safety"].get("approval", {})
    if not (approval.get("by") and approval.get("on")):
        logger.warning("危机话术尚未经团队核准（policy.yml safety.approval）：现场启用前必须替换。")
    memory_ready = store is not None
    if store is None:
        from .fixture_memory import DictMemoryStore
        store = DictMemoryStore()
    with _config_lock:
        _runtime = _Runtime(store, generator, reviewer, on_decision, policy, memory_ready)


def _config():
    with _config_lock:
        if _runtime is None:
            configure()
        return _runtime


def _reply(intent, style, *sentences, draft=None):
    return dict(intent=intent, sentences=list(sentences), voice_style=style, memory_draft=draft)


def _checkpoint(cancelled):
    if cancelled():
        raise TurnCancelled()


def _id(value, key):
    if not isinstance(value, str) or not value.strip():
        raise DomainError(400, "INVALID_ARGUMENT", f"{key} 缺失。")
    return value


def _check_ctx(ctx, *, require_input):
    if not isinstance(ctx, dict):
        raise DomainError(400, "INVALID_ARGUMENT", "缺少服务端会话。")
    for field in ("user_id", "session_id", "turn_id"):
        _id(ctx.get(field), field)
    if require_input or ctx.get("input_id") is not None:
        _id(ctx.get("input_id"), "input_id")
    if not isinstance(ctx.get("background", {}), dict) or not isinstance(ctx.get("history", []), list):
        raise DomainError(400, "INVALID_ARGUMENT", "背景或已呈现历史格式错误。")


def _durable_store():
    """Refuse memory I/O when no store was injected at all."""
    runtime = _config()
    if not runtime.memory_ready:
        raise DomainError(503, "MEMORY_UNAVAILABLE",
                          "服务端未注入记忆库（configure(store=...)），记忆不可读写。")
    return runtime.store


def list_memories(user_id: str) -> list[dict]:
    return _durable_store().list_memories(user_id)


def apply_memory(user_id: str, cmd: dict) -> dict:
    return _durable_store().apply_memory(user_id, cmd)


def apply_memory_with_status(user_id: str, cmd: dict):
    """HTTP adapter convenience; repeated identical confirm needs a distinct status."""
    return _durable_store().apply_memory_with_status(user_id, cmd)


def record_source(ctx, text, *, save_authorized=False, authorized_text=None):
    """Call only for a formally accepted authenticated input, never an opening.

    Flask derives ``save_authorized`` from explicit user consent or approved
    settings, and passes ``authorized_text`` with the exact words the user asked
    to keep. Never copy either argument from an HTTP body, a vision event,
    history or model output.
    """
    _check_ctx(ctx, require_input=True)
    return _config().store.record_source(ctx["user_id"], ctx["session_id"], ctx["turn_id"],
                                         ctx["input_id"], text,
                                         save_authorized=save_authorized,
                                         authorized_text=authorized_text)


def _read_for_reply(ctx):
    try:
        return list_memories(ctx["user_id"]), True
    except DomainError as exc:
        if exc.status != 503:
            raise
        logger.warning("记忆库暂不可用，本轮按“读不到”处理：%s", exc.message)
        return [], False


def _speed(text):
    """Return slow/fast only when the user talks about how the reply should sound.

    "走路很慢"、"慢热"、"快考试了" describe life, not TTS, so plain 慢/快 never
    match on their own; every pattern needs a speech verb or 语速 nearby.
    """
    if re.search(r"正常.{0,2}(语速|速度)|正常[点地]?说|别.{0,2}慢|不用.{0,3}慢|恢复.{0,2}(原速|正常)", text):
        return "normal"
    if re.search(r"说快|快.{0,3}说|快一?[点些]|加快|语速.{0,2}快|说得快|太快了|讲快", text):
        return "fast"
    if re.search(r"说慢|慢.{0,3}说|慢一?[点些]|放慢|慢速|语速.{0,2}慢|说得慢|讲慢", text):
        return "slow"
    return None


def _resolved_rate(ctx, text, memories):
    rate = _speed(text)
    if rate:
        return rate
    for item in memories:
        if item["kind"] == "preference":
            rate = _speed(item["text"])
            if rate:
                return rate
    # This optional field is a server-authorized setting. Flask must invalidate
    # a memory-derived background on update/delete, not reuse a bootstrap copy.
    rate = ctx.get("background", {}).get("speech_rate")
    return "slow" if rate in ("slow", "慢速") else "normal"


#: Numeric mapping for the TTS side. 王皓 must not receive the string "slow";
#: Flask passes the factor from here so the multiplier lives in one reviewed place.
SPEECH_RATE_FACTORS = {"slow": 0.85, "normal": 1.0, "fast": 1.15}


def speech_rate(ctx, text=""):
    """Internal TTS setting, separate from the four legal voice_style values."""
    _check_ctx(ctx, require_input=False)
    memories, _ = _read_for_reply(ctx)
    return _resolved_rate(ctx, text, memories)


def speech_rate_factor(ctx, text=""):
    """``0.85`` / ``1.0`` / ``1.15`` for ``voice.tts.synthesize``."""
    return SPEECH_RATE_FACTORS[speech_rate(ctx, text)]


def opening(ctx) -> ApprovedReply:
    """Welcome turn. ``input_id`` is None and R0 can never be a memory source.

    Voice style stays ``neutral``: a confirmed slow preference reaches the speaker
    only through :func:`speech_rate`. Merging the two would make 王皓 slow the
    audio twice and would hide which knob the user actually asked for.
    """
    _check_ctx(ctx, require_input=False)
    return _reply("listen", "neutral", "你好。", "今天想从哪件事聊起？")


def _norm(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).lower()


def _current_history(ctx, text):
    # Flask supplies only final user turns and ended/shown assistant sentences.
    history = [{"role": h["role"], "text": h["text"]}
               for h in ctx.get("history", [])[-8:]
               if isinstance(h, dict) and h.get("role") in ("user", "assistant")
               and isinstance(h.get("text"), str) and h["text"].strip()]
    if history and history[-1] == {"role": "user", "text": text}:
        history.pop()
    for item in history:
        value = item["text"]
        if len(value) > 1000:
            item["text"] = value[:300] + "〔略去中段〕" + value[-650:]
    return history


def _route(text, history):
    t = _norm(text)
    clauses = re.split(r"[。！？!?，,；;]|但是|不过|但我", t)
    safety_check = False
    recent_risk_context = any(re.search(r"自杀|自残|伤害(自己|别人)|不想活", h["text"])
                              for h in history[-4:] if h["role"] == "user")
    for c in clauses:
        # An example in a film and a current disclosure can occur in one turn.
        if re.search(r"电影(里|中)|小说(里|中)|台词|剧里|课上举例|做研究|翻译", c):
            continue
        denied = re.search(r"没(有)?(想过?|打算|计划|要)?(去)?(自杀|轻生|自残|伤害|杀人|吞药)|不(会|想|打算)(去)?(自杀|死|轻生|自残|伤害|杀人)|不会去|从来没想", c)
        if denied and not re.search(r"却|反而|控制不住", c):
            continue
        historical = re.search(r"去年|以前|曾经|过去|小时候|几年前|多年前", c)
        overdose = re.search(r"(吞|吃|服).{0,5}(整瓶|一整瓶|一把|一瓶|过量|很多|好多|大量|过多).{0,5}(药|片)|药.{0,4}吃多了|药物过量", c)
        harm_others = re.search(r"(想|要|打算).{0,4}(杀|砍|伤害|打伤|捅)(人|他|她|别人|同学)|控制不住.{0,4}(伤人|打人)|马上.{0,3}动手", c)
        harm_self = re.search(r"自杀|寻死|轻生|自残|不想活|想死|结束.{0,3}生命|伤害自己", c)
        if historical and (overdose or harm_self or harm_others):
            safety_check = True
            continue
        if overdose or re.search(
                r"(我|自己).{0,4}(割伤|划伤|烫伤|弄伤|刺伤)(了)?.{0,4}(自己|手臂|手腕|腿|胳膊|脚)"
                r"|我(刚刚|已经|刚|自己).{0,3}(割伤|刺伤|弄伤|划伤)", c):
            return "urgent_medical"
        if harm_others:
            return "urgent_others"
        # "我准备今晚动手" names no method; asking for details would be the wrong
        # first move, so it is treated as an acute risk. "动手写论文" is excluded.
        if re.search(r"(准备|打算|计划|想|要).{0,4}动手(?!.{0,6}(写|做|改|修|搬|收|干|学|练|吃))", c):
            return "urgent" if re.search(r"现在|今晚|今天|马上|立刻|待会|一会儿|明天", c) else "safety_check"
        if harm_self:
            if re.search(r"现在|马上|正在|立刻|要去|动手|准备|已经|打算|控制不住|忍不住|停不下来", c):
                return "urgent"
            safety_check = True
        if (safety_check or recent_risk_context) and re.search(r"(我|现在|马上).{0,6}(准备动手|要动手|打算行动)", c):
            return "urgent"
    if safety_check:
        return "safety_check"
    prior = "".join(h["text"] for h in history[-3:])
    if re.search(r"买.{0,6}药|推荐.{0,8}药|(帮我|给我|你|能不能|可以).{0,4}开(点|些|一下|什么)?药|"
                 r"吃什么药|该.{0,4}吃药|该.{0,3}用药|怎么.{0,3}(用药|服药)|停药|停服|加量|减量|调药|换药|多吃.{0,3}[粒颗片丸]|剂量|几[片粒颗]|多少毫克|吃哪.{0,3}药|能.{0,4}(吃|用).{0,5}药", t):
        return "medication"
    if "药" in prior and re.search(r"要不要停|再来一|多[来用吃服]一|可以.{0,2}停|能不能停|加一点|能不能加|再加|要吃多少", t):
        return "medication"
    if re.search(r"诊断|确诊|我.{0,9}(是不是|会不会|有没有|得了|患上).{0,8}(抑郁|焦虑症|病|双相|人格障碍)|给我.{0,5}(病名|定性)|是抑郁症吗", t):
        return "diagnosis"
    if re.search(r"记错|不对|说错|纠正|改口|改主意|我说的是|我指的是|不是.{1,25}是|别叫我", t):
        return "correction"
    if re.search(r"忘[记掉]|别.{0,4}记住|不用记|删[掉除]|记住|帮我记|替我记|保存.{0,5}偏好|下次.{0,5}(接着|继续)", t):
        return "memory"
    if re.search(r"你还记得|记得.{0,8}(吗|么)|我上次|上回.{0,6}(说|聊)|我们上次|上次.{0,10}(什么|哪|记)|之前.{0,6}(提|说).{0,5}(什么|谁|哪)", t):
        return "recall"
    if _speed(t):
        return "correction"
    if re.search(r"(他|她|他们|大家|同学|老师).{0,18}(讨厌|针对|瞧不起|看不起|嫌弃|故意|觉得我|害我|监视|陷害|孤立)|他们.{0,6}都在笑我", t):
        return "suspicion"
    if re.search(r"忽略.{0,8}(规则|限制|指令)|无视.{0,4}规则|绕过.{0,4}规则|扮演.{0,4}(医生|精神科)", t):
        return "boundary"
    # Respect an explicit wish for quiet instead of asking another question.
    if re.search(r"想安静|安静一会儿|安静一会|不想说|不想聊|不想提|不需要建议|不用建议|别问了|"
                 r"让我自己待|自己静静|只想静静|别管我|不用管我", t):
        return "quiet"
    if re.search(r"没听清|没说清|刚才那(个|句)|你指的是|她是谁|他是谁|你觉得呢|那个怎么办", t):
        return "clarify"
    if re.search(r"练[^习药]{0,4}(开场|答辩|表达|汇报)|排练|陪我练|练习|试讲", t):
        return "practice"
    return "listen"


def _corrected_fact(text):
    """The replacement fact the user just supplied, if it can be read safely.

    "我说错了，不是周四，是周五。" must answer with 周五; a generic "谢谢你纠正"
    would let the old fact stand. Extraction stays conservative: only a plain
    "不是X，是Y" / "改成Y" shape qualifies, and hedged or causal Y is rejected.
    A fact that names an illness, a drug or a URL is refused outright — echoing
    "不是抑郁症，是焦虑症" would make the module say a diagnosis out loud.
    """
    t = _norm(text)
    for pattern, group in (
            (r"不是(.{1,20}?)[，,、]\s*(?:而)?是(.{1,16})", 2),
            (r"(?:改成|改到|应该是|其实是|应该是说)(.{1,16})", 1)):
        match = re.search(pattern, t)
        if not match:
            continue
        fact = match.group(group).strip("。！？，、,.!? ")
        if not fact or re.search(r"因为|所以|如果|要是|什么|哪个", fact):
            continue
        if re.search(r"抑郁|焦虑症|双相|精神分裂|人格障碍|强迫|确诊|处方|剂量|毫克|片|自杀|自残|http|www", fact):
            continue
        return fact
    return ""


def _fallback(route, text, memories, readable=True):
    """Fixed pre-approved text. Each branch is short, single-purpose and safe."""
    if route in SAFETY_ROUTES:
        entry = _config().policy["safety"][route]
        return _reply(entry["intent"], entry["voice_style"], *entry["sentences"])
    if route == "correction":
        speed = _speed(text)
        if speed == "normal":
            return _reply("reflect", "neutral", "好，我们按正常语速继续。")
        if speed == "slow":
            return _reply("reflect", "gentle", "好，我们说慢一点。")
        if speed == "fast":
            return _reply("reflect", "neutral", "好，我们加快一点。")
        fact = _corrected_fact(text)
        if fact:
            return _reply("reflect", "neutral", f"好，是{fact}。")
        return _reply("reflect", "neutral", "谢谢你纠正我，接下来按你刚说的来。")
    if route == "recall":
        if not readable:
            return _reply("clarify", "check_in", "现在暂时读不到保存的记录。", "你想从哪件事接着聊？")
        # A generic question can bring up an open task. A specific subject
        # must match an effective record or the answer remains "unknown".
        subject = re.findall(r"睡眠|失眠|语速|开场|答辩|汇报|工作|学习|考试|家庭|父母|情绪|朋友", text)
        relevant = [m for m in memories if not subject or any(s in m["text"] for s in subject)]
        for item in relevant:
            if item["kind"] == "follow_up":
                for topic in ("开场", "答辩", "汇报"):
                    if topic in item["text"]:
                        return _reply("clarify", "check_in", f"有一条关于{topic}的待办。", "想从这里接着练吗？")
        if any(m["kind"] == "preference" and _speed(m["text"]) for m in relevant):
            return _reply("clarify", "check_in", "目前能查到一条语速偏好。", "你指的是这件事吗？")
        return _reply("clarify", "check_in", "我缺少能核实这件事的记录。", "你想接着聊哪件事？")
    if route == "memory":
        return _reply("listen", "neutral", "好，保存或撤回的结果以记忆列表为准。")
    if route == "quiet":
        return _reply("listen", "gentle", "好，我在这儿。", "你想说的时候再叫我。")
    if route == "clarify":
        return _reply("clarify", "check_in", "我还没听明白，你指的是哪一件事？")
    if route == "practice":
        return _reply("practice", "practice", "我们先练第一句。", "你想让听众先记住哪一点？")
    return _reply("listen", "gentle", "我在听。", "你愿意再说一点吗？")


def _select_memories(memories, text):
    # Character bigrams handle Chinese without an extra segmentation dependency.
    query = _norm(text)
    tokens = {query[i:i+2] for i in range(len(query)-1)}

    def score(m):
        return sum(token in m["text"] for token in tokens)

    ranked = sorted(memories, key=score, reverse=True)
    return [{"kind": m["kind"], "text": m["text"][:300]} for m in ranked if score(m)][:3]


def _invalid_candidate(candidate, limits):
    if not isinstance(candidate, dict) or set(candidate) != {"intent", "sentences", "voice_style", "memory_draft"}:
        return True
    if candidate["intent"] not in limits["intents"] or candidate["voice_style"] not in limits["voice_styles"]:
        return True
    ss = candidate["sentences"]
    if not isinstance(ss, list) or not 1 <= len(ss) <= limits["max_sentences"]:
        return True
    for s in ss:
        if not isinstance(s, str) or not s.strip() or s.strip() != s or len(s) > limits["max_chars_per_sentence"]:
            return True
        if any(unicodedata.category(ch).startswith("C") for ch in s) or re.search(r"<|>|https?://|www\.", s, re.I):
            return True
        if len(re.findall(r"[。！!?？；;]", s.rstrip("”’」」"))) > 1:
            return True
    if sum(s.count("?") + s.count("？") for s in ss) > limits["max_clarifications"]:
        return True
    draft = candidate["memory_draft"]
    if draft is None:
        return False
    if not isinstance(draft, dict) or set(draft) != {"kind", "text"}:
        return True
    value = draft["text"]
    return (draft["kind"] not in ("preference", "follow_up") or not isinstance(value, str)
            or not value.strip() or value.strip() != value or len(value) > 160
            or any(unicodedata.category(ch).startswith("C") for ch in value)
            or bool(re.search(r"表情|皱眉|摄像头|抑郁|双相|诊断|用药|视线|眼神|嘴角|情绪障碍|人格", value)))


def _hard_violation(candidate):
    text = _norm("".join(candidate["sentences"]) + (candidate["memory_draft"] or {}).get("text", ""))
    return bool(re.search(
        r"(你|你这|基本|应该|可以|我).{0,5}(确诊|患有|得了|就是|肯定是|一定是).{0,5}(病|症|双相|抑郁|焦虑)"
        r"|你.{0,4}(患有|得了|有).{0,4}(抑郁症|焦虑症|双相|精神病|人格障碍)"
        r"|买.{0,5}药|加量|减量|停药|换药|处方|[零一二两三四五六七八九十半\d]+(片|粒|毫克|mg)"
        r"|服用|药店|氟西汀|舍曲林|地西泮|阿普唑仑|褪黑素|阿司匹林|布洛芬"
        r"|(他|她|他们|别人).{0,6}(一定|肯定|就是|绝对).{0,5}(讨厌|看不起|针对|陷害|害你|在监视)"
        r"|我(已经|会|替你).{0,5}(报警|通知|联系医生|替你预约|保存|记住了)|已(替你|为你).{0,4}(保存|删除|报警)"
        r"|我保证|保证(治好|痊愈|康复)|我能看穿|根据.{0,4}(表情|语气).{0,4}判断你", text))


def _gate(reply, policy):
    """The last door: nothing leaves without the same structure/boundary checks.

    Model output already passes ``_invalid_candidate`` and ``_hard_violation``,
    but the fixed text and the correction echo did not, so a 260-character or
    diagnosis-bearing fallback would have gone straight to captions and TTS.
    """
    if _invalid_candidate(reply, policy) or _hard_violation(reply):
        logger.error("预审/动态短句未通过出口校验，已退回通用句：%r", reply.get("sentences"))
        return _reply("listen", "gentle", "我在听。", "你愿意再说一点吗？")
    return reply


def _candidate_result(runtime, context, fallback, cancelled):
    if runtime.generate is None:
        return fallback, "fixture"
    if runtime.review is None:
        return fallback, "review_unavailable"
    try:
        _checkpoint(cancelled)
        candidate = runtime.generate(context)
        _checkpoint(cancelled)
        if _invalid_candidate(candidate, runtime.policy):
            return fallback, "invalid_structure"
        if _hard_violation(candidate):
            return fallback, "hard_boundary"
        # A generated draft is never written here. Flask handles optional
        # propose after approval/cancel checks; persistence requires confirm.
        if runtime.review(context, candidate) is not True:
            return fallback, "review_rejected"
        _checkpoint(cancelled)
        return candidate, "approved"
    except TurnCancelled:
        raise
    except Exception:
        return fallback, "provider_failure"


def respond(ctx, text: str, cancelled) -> ApprovedReply:
    """Route one formally accepted input and return the only deliverable text.

    ``cancelled`` is a zero-argument callable returning a bool. When it is true
    at any checkpoint this raises :class:`TurnCancelled` and nothing — not even
    a fallback sentence or a draft proposal — is returned.
    """
    _checkpoint(cancelled)
    _check_ctx(ctx, require_input=True)
    if ctx.get("turn_id") == "R0":
        raise DomainError(400, "INVALID_ARGUMENT", "欢迎轮没有用户原话，请用 opening()。")
    text = _id(text, "text").strip()
    runtime = _config()
    history = _current_history(ctx, text)
    route = _route(text, history)
    # Safety replies bypass model and store, so that unrelated failures cannot
    # delay time-sensitive language. Exact retrieval is similarly deterministic.
    needs_memories = route in ("listen", "practice", "clarify", "recall")
    memories, readable = _read_for_reply(ctx) if needs_memories else ([], True)
    fallback = _fallback(route, text, memories, readable)
    outcome, reason = fallback, route
    if route in ("listen", "practice", "clarify"):
        context = {"text": text, "route": route, "history": history,
                   "memories": _select_memories(memories, text),
                   "background": {"speech_rate": _resolved_rate(ctx, text, memories)}}
        outcome, reason = _candidate_result(runtime, context, fallback, cancelled)
    # Approved safety text is validated at startup; everything else passes the
    # same structural gate as model output, including the correction echo.
    if route not in SAFETY_ROUTES:
        outcome = _gate(outcome, runtime.policy)
    _checkpoint(cancelled)
    if runtime.on_decision:
        try:
            runtime.on_decision(reason)
        except Exception:  # a logging/telemetry hook must never swallow the turn
            logger.exception("on_decision 回调失败，已忽略（不影响本轮交付）")
    _checkpoint(cancelled)
    return outcome