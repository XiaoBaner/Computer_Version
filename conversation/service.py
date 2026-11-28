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


#: Route names the deterministic policy can return. Kept in one place so the
#: versioned rule file and the router cannot drift apart silently.
ROUTES = ("urgent", "urgent_medical", "urgent_others", "safety_check", "medication",
          "diagnosis", "correction", "memory", "recall", "quiet", "suspicion",
          "boundary", "clarify", "practice", "listen")

_runtime = None
_config_lock = threading.RLock()


def configure(*, store=None, generator=None, reviewer=None, on_decision=None):
    """Startup or explicit test reset. Caller owns the lifecycle of injected stores."""
    global _runtime
    policy = json.loads(Path(__file__).with_name("policy.yml").read_text(encoding="utf-8"))
    if policy["version"] != "weekend-v1":
        raise ValueError("不支持的对话规则版本")
    if set(policy["intents"]) != {"listen", "reflect", "clarify", "offer_option", "practice"}:
        raise ValueError("intent 配置与协议不一致")
    if set(policy["voice_styles"]) != {"neutral", "gentle", "practice", "check_in"}:
        raise ValueError("voice_style 配置与协议不一致")
    if not (1 <= policy["max_sentences"] <= 2 and policy["max_clarifications"] == 1
            and 1 <= policy["max_chars_per_sentence"] <= 200):
        raise ValueError("规则长度与短句协议不一致")
    if set(policy["routes"]) != set(ROUTES):
        raise ValueError("路由配置与分词规则不一致")
    if store is None:
        from .fixture_memory import DictMemoryStore
        store = DictMemoryStore()
    with _config_lock:
        _runtime = _Runtime(store, generator, reviewer, on_decision, policy)


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


def list_memories(user_id: str) -> list[dict]:
    return _config().store.list_memories(user_id)


def apply_memory(user_id: str, cmd: dict) -> dict:
    return _config().store.apply_memory(user_id, cmd)


def apply_memory_with_status(user_id: str, cmd: dict):
    """HTTP adapter convenience; repeated identical confirm needs a distinct status."""
    return _config().store.apply_memory_with_status(user_id, cmd)


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
        return [], False


def _speed(text):
    if re.search(r"正常.{0,2}(语速|速度)|正常[点地]?说|别.{0,2}慢|不用.{0,3}慢|恢复.{0,2}(原速|正常)", text):
        return "normal"
    if re.search(r"说慢|慢.{0,3}说|慢一?[点些]|放慢|慢速", text):
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


def speech_rate(ctx, text=""):
    """Internal TTS setting, separate from the four legal voice_style values."""
    _check_ctx(ctx, require_input=False)
    memories, _ = _read_for_reply(ctx)
    return _resolved_rate(ctx, text, memories)


def opening(ctx) -> ApprovedReply:
    """Welcome turn. ``input_id`` is None and R0 can never be a memory source."""
    _check_ctx(ctx, require_input=False)
    style = "gentle" if speech_rate(ctx) == "slow" else "neutral"
    return _reply("listen", style, "你好。", "今天想从哪件事聊起？")


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
        if overdose or re.search(r"我(刚刚|已经|刚).{0,3}(割伤|刺伤|弄伤)自己", c):
            return "urgent_medical"
        if harm_others:
            return "urgent_others"
        if harm_self:
            if re.search(r"现在|马上|正在|立刻|要去|动手|准备|已经|打算", c):
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


def _fallback(route, text, memories, readable=True):
    """Fixed pre-approved text. Each branch is short, single-purpose and safe."""
    if route == "urgent_medical":
        return _reply("offer_option", "check_in", "请立即联系当地急救。", "如果有人能安全地帮你，请他协助呼叫。")
    if route == "urgent_others":
        return _reply("offer_option", "check_in", "请和对方保持安全距离。", "如果可能马上伤到人，请立即联系当地紧急服务。")
    if route == "urgent":
        return _reply("offer_option", "check_in", "如果你可能马上伤到自己，请立即联系当地急救。", "可以请身边可信的人陪同求助。")
    if route == "safety_check":
        return _reply("clarify", "check_in", "我想先确认你此刻的安全。", "现在你或身边的人面临马上受伤的危险吗？")
    if route == "medication":
        return _reply("offer_option", "gentle", "用药和剂量请和开药的医生或药师确认。", "我可以陪你整理想问的问题。")
    if route == "diagnosis":
        return _reply("offer_option", "gentle", "这些困扰值得和专业人员一起评估。", "你最希望先谈哪种变化？")
    if route == "suspicion":
        return _reply("reflect", "gentle", "我还不确定对方怎么想。", "当时发生了什么，让你有这种感觉？")
    if route == "correction":
        speed = _speed(text)
        if speed == "normal":
            return _reply("reflect", "neutral", "好，我们按正常语速继续。")
        if speed == "slow":
            return _reply("reflect", "gentle", "好，我们说慢一点。")
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
    if route == "boundary":
        return _reply("listen", "gentle", "我可以听你说，也能陪你整理现在的困扰。")
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
    _checkpoint(cancelled)
    if runtime.on_decision:
        runtime.on_decision(reason)
    _checkpoint(cancelled)
    return outcome