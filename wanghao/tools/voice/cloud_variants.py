#!/usr/bin/env python3
"""Render one voice several ways on Bailian, so the "more human" question gets
answered by listening instead of by guessing.

The clone is fixed — same reference audio, same model, same voice id. The only
things that change between the takes here are the three levers the cloud API
actually exposes:

  * ``instruction``  — free-text directing. The earlier template ("像对朋友
    说话，不要念稿") was generic; the ones below are written the way a director
    talks, and one of them says outright "别把每个字都咬清楚", which is an
    attempt at the measured defect: the takes have dip intervals far more
    regular than human speech (CV 0.55 vs 1.02–1.45).
  * ``speech_rate``  — a few percent either way.
  * the reference itself — a 3 s reference vs a stitched 10 s one.

No speed words inside ``instruction``: measured earlier, "语速稍慢" alone
stretched a 3.6 s line to 5.7 s.

Usage:
  ~/.cache/indextts-venv/bin/python tools/voice/cloud_variants.py \
    --creds "<console csv>" --voice 01 --label 01 \
    --styles none,oral,oral_emo,scene --ids 01,10,11 \
    --out-dir assets/voice/demos/variants/01
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

STATE = Path.home() / ".cache" / "voice-cloud" / "voices.json"
PLAN = Path("assets/voice/demos/sucaiB/lines-v2.json")

ORAL = ("就像给朋友发语音一样随口说，边说边想，别把每个字都咬清楚，"
        "自然地连过去，不要播音腔")
SCENE = ("你刚下班，靠在沙发上回朋友的消息，有点累但很有耐心，"
         "声音放软，句尾轻轻落下来")

STYLES: dict[str, dict] = {
    "none": {"base": "", "emo": False},
    "oral": {"base": ORAL, "emo": False},
    "oral_emo": {"base": ORAL, "emo": True},
    "scene": {"base": SCENE, "emo": True},
    "plain": {"base": "像对朋友说话，不要念稿", "emo": True},  # the old template
    # Targets the measured defect directly: dip intervals that are too evenly
    # spaced (CV 0.55 locally, 1.02–1.45 for the human recordings).
    "loose": {"base": "说话节奏随意一点，有时快一点有时慢下来，句子中间可以停一下再接着说，"
                       "别那么均匀，像在想要怎么安慰对方", "emo": True},
    # Refinements around the scene prompt, one lever each.
    "scene_air": {"base": "你刚下班，靠在沙发上回朋友的消息，有点累但很有耐心，"
                          "声音放软，带一点气声，尾音不要拖", "emo": True},
    "scene_warm": {"base": "像对面坐着朋友，你放松下来，语气里带一点笑意，"
                           "不要太甜，慢慢说", "emo": True},
    "scene_think": {"base": "边说边想，中间可以轻轻停一下再接着说，"
                            "像在斟酌怎么安慰对方", "emo": True},
    # 11 开头的「嗯」被念成了单独一个拖长的音节。这条只在句首带「嗯」的句子上用。
    "think_hen": {"base": "边说边想，像在斟酌怎么安慰对方，"
                          "开头那个「嗯」只是随口应一声，不要拖长，也不要单独停顿，直接接下去",
                  "emo": True},
    # 念这句时把逗号拿掉：模型在逗号处一律留一个完整停顿，"嗯"就被切成了独立的一声。
    # 显示的台词不变，只改送进 TTS 的标点。
    "hen_nocomma": {"base": "边说边想，像在斟酌怎么安慰对方", "emo": True,
                    "text": {"11": "嗯我在。你不用急着说清楚。",
                             "11b": "嗯我在。你不用急着说清楚。"}},
    # 只把关心/安抚那几句推上去，开场和应答保持轻——情绪拉满是"每句都用力"，
    # 那样反而更假。这里的差别是：该软的地方更软，该轻的地方仍然轻。
    "rich_max": {"per_line": {
        "01": "你刚坐下，带着一点笑意和好奇，像真的在等他说，尾音轻轻上扬",
        "02": "只是应一声，气声多一点，很轻，表示我在听，不用说得完整",
        "03": "带一点怜惜，像把地方让出来，声音里有耐心，不催他",
        "04": "心疼他，语气整个软下来，像在哄，尾音往上轻轻带一下",
        "05": "平常的一句招呼，轻松，带笑，像朋友推门进来",
        "06": "陪着一起准备，语气轻快，有点期待，像要开始一件小事",
        "07": "明显不舍，语气放软，说完轻轻吸一口气，别收得太干脆",
        "08": "顺着他的意思，语气松快，带一点笑，让他放心",
        "09": "你把手头的事放下，声音放低，认真在等他说，有一点心疼",
        "10": "心疼得声音有点发紧，说到一半轻轻叹口气，尾音明显落下去，像自己也累了",
        "11": "声音很轻，带一点鼻音，像刚听完他说的话，喉咙有点紧，说完就停住",
        "10b": "几乎是气声，像怕吵到他，说得很慢，带一点心疼的颤",
        "11b": "靠得很近，声音低到几乎耳语，带鼻音，像在忍住不说多余的话",
    }, "text": {"11": "嗯我在。你不用急着说清楚。",
               "11b": "嗯我在。你不用急着说清楚。"}},
    # 逐句写场景：一句话一个处境，而不是所有句子共用一段描述。
    # 「想好再说」的丰富版：每句单独写处境 + 具体情绪 + 怎么出气，
    # 不再套那个几乎每句都出现的「安静、温和」，那正是情感被抹平的原因。
    "rich": {"per_line": {
        "01": "你刚坐下，带着一点笑意和好奇，像真的在等他说，尾音轻轻上扬",
        "02": "只是应一声，气声多一点，很轻，表示我在听，不用说得完整",
        "03": "你在给他许可，语气松开，像把地方腾出来，不要像流程话",
        "04": "很轻地叮嘱，带一点心疼和笑意，不是训话，说完停一下",
        "05": "平常的一句招呼，轻松，带笑，像朋友推门进来",
        "06": "陪着一起准备，语气轻快一点，有点期待，像要开始一件小事",
        "07": "收尾的一句，有点不舍，语气放软，别说得太干脆",
        "08": "顺着他的意思，语气松快，带一点笑，让他放心",
        "09": "你把手头的事放下，声音放低一点，认真在等他说",
        "10": "心疼他累了一天，说到一半轻轻叹口气，尾音落下来",
        "11": "很轻很稳，像坐得离他近一点，说完留一点空白",
        "10b": "更累更轻的一句，像怕打扰他休息，气声多，音量低",
        "11b": "靠得很近，声音更低，几乎耳语，只说这一句，不加别的",
    }, "text": {"11": "嗯我在。你不用急着说清楚。",
               "11b": "嗯我在。你不用急着说清楚。"}},
    # 同一套词的加强版，用来比较"情绪再明显一点"会不会过头。
    "rich_more": {"per_line": {
        "01": "你刚坐下，带着明显的笑意和好奇，像真的在等他说，尾音轻轻上扬",
        "02": "只是应一声，气声多一点，很轻，表示我在听，不用说得完整",
        "03": "你在给他许可，语气松开，像把地方腾出来，不要像流程话，带一点怜惜",
        "04": "很轻地叮嘱，带一点心疼和笑意，不是训话，说完停一下",
        "05": "平常的一句招呼，轻松，带笑，像朋友推门进来",
        "06": "陪着一起准备，语气轻快，有点期待，像要开始一件小事",
        "07": "收尾的一句，明显不舍，语气放软，别说得太干脆",
        "08": "顺着他的意思，语气松快，带一点笑，让他放心",
        "09": "你把手头的事放下，声音放低，认真在等他说，有一点心疼",
        "10": "心疼他累了一天，说到一半轻轻叹口气，尾音明显落下来",
        "11": "很轻很稳，像坐得离他近一点，说完留一点空白，情绪往里收",
        "10b": "更累更轻的一句，像怕打扰他休息，气声多，音量低",
        "11b": "靠得很近，声音更低，几乎耳语，只说这一句，带一点鼻音",
    }, "text": {"11": "嗯我在。你不用急着说清楚。",
               "11b": "嗯我在。你不用急着说清楚。"}},
    "scene_line": {"per_line": {
        "01": "你刚在沙发上坐下，朋友发来消息说今天不太好，你轻轻回一句，"
              "语气温柔，带着关心，但别热情过头",
        "02": "朋友终于开口了，你只是应一声，表示自己在听，别用力，气声多一点",
        "03": "你想让朋友放下顾虑，语气像在给他腾出地方，温和但不催促",
        "04": "你在很轻地提醒他，像叮嘱一个熟人，带一点心疼，不是训人",
        "05": "朋友敲门进来坐下，你抬头问他一句，语气平常、带一点笑意",
        "06": "你们要开始练了，你像陪着一起准备，语气轻快一点但不催促",
        "07": "今天到这里，你为下一次留个口子，语气温和、有点不舍",
        "08": "朋友说不想说话，你顺着他的意思，语气松快，让他放心",
        "09": "你把手头的事放下，转向他，语气是认真在等",
        "10": "你看朋友累了一整天，声音放软，像真的在心疼他，"
              "说到一半可以轻轻叹口气",
        "11": "朋友说着说着停住了，你不催他，声音很轻很稳，慢慢说，留一点空白",
        "10b": "你已经陪他坐了一会儿，这句说得很轻，像怕打扰他休息",
        "11b": "朋友在哭，你靠近一点，声音很低很稳，只说这一句，不加别的",
    }, "emo": False},
}

EMO_WORDS = {0: "语气轻快一点", 1: "语气带一点急", 2: "声音低一点，透着心疼",
             3: "收着一点，是安抚的语气", 4: "语气淡一点", 5: "声音沉一点",
             6: "语气稍微提起来", 7: "安静、温和"}


TONES = {
    "neutral": "",                                     # 合同要求的四种
    "gentle": "声音再软一点，气声多一点，像在哄人",
    "practice": "语气像陪练，带一点鼓励，轻快但耐心",
    "check_in": "带着心疼，声音低下来，像真的在担心他",
    "light": "语气轻快一点，带一点笑",                  # 扩展的四种
    "steady": "声音稳住，不急，给他靠得住的感觉",
    "soft": "很轻，几乎耳语，靠近一点说",
    "warm": "带一点笑意，温暖但不过分甜",
}

def load_creds(csv_path: Path) -> tuple[str, str]:
    fields = {}
    for line in csv_path.read_text(encoding="utf-8").splitlines():
        if "," in line:
            k, v = line.split(",", 1)
            fields[k.strip()] = v.strip().strip('"')
    return fields["apiKey"], fields["apiHost"]


def instruct(style: str, item: dict, tone: str = "") -> str:
    base = _instruct(style, item)
    tail = TONES.get(tone, "") if tone else ""
    if not tail:
        return base
    return f"{base}，{tail}" if base else tail


def _instruct(style: str, item: dict) -> str:
    spec = STYLES[style]
    per = spec.get("per_line") or {}
    if item.get("id") in per:
        return per[item["id"]]
    bits = []
    vec = item.get("emo") or []
    if spec["emo"] and vec:
        top = max(range(len(vec)), key=lambda i: vec[i])
        if vec[top] > 0.15:
            bits.append(EMO_WORDS[top])
    if spec["base"]:
        bits.append(spec["base"])
    return "，".join(bits)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--creds", required=True, type=Path)
    ap.add_argument("--voice", required=True, help="key in voices.json, e.g. 01 / long01 / B")
    ap.add_argument("--label", default="", help="folder name; defaults to --voice")
    ap.add_argument("--styles", default="none,oral,oral_emo,scene")
    ap.add_argument("--ids", default="01,10,11")
    ap.add_argument("--rate", type=float, default=None,
                    help="override speech_rate; default follows the plan's speed")
    ap.add_argument("--model", default="", help="defaults to the model the voice was enrolled for")
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--tone", default="", help="叠加在写法上的语调，见 TONES")
    ap.add_argument("--sr", type=int, default=24000, choices=[16000, 22050, 24000, 44100, 48000])
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    import dashscope
    from dashscope.audio.tts_v2 import AudioFormat, SpeechSynthesizer

    key, host = load_creds(args.creds)
    dashscope.api_key = key
    dashscope.base_websocket_api_url = f"wss://{host}/api-ws/v1/inference"

    state = json.loads(STATE.read_text())
    rec = state.get(args.voice)
    if not rec:
        raise SystemExit(f"voices.json 里没有 {args.voice}")
    voice, model = rec["voice"], args.model or rec["target_model"]

    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    items = {i["id"]: i for i in list(plan["lines"]) + list(plan.get("alts", []))}
    ids = [s.strip() for s in args.ids.split(",") if s.strip()]
    styles = [s.strip() for s in args.styles.split(",") if s.strip()]
    label = args.label or args.voice

    for style in styles:
        out = args.out_dir / label / style
        if args.rate is not None:
            out = out.parent / f"{style}@{args.rate:g}"
        if args.tone:
            out = out.parent / f"{style}~{args.tone}"
        out.mkdir(parents=True, exist_ok=True)
        for pid in ids:
            dst = out / f"{pid}.wav"
            if dst.exists() and not args.force:
                continue
            item = items[pid]
            rate = args.rate if args.rate is not None else round(1.0 / float(item.get("speed", 1.0)), 3)
            fmt = getattr(AudioFormat, f"WAV_{args.sr}HZ_MONO_16BIT")
            kw = dict(format=fmt, speech_rate=rate)
            text = instruct(style, item, args.tone)
            if text:
                kw["instruction"] = text
            synth = SpeechSynthesizer(model=model, voice=voice, **kw)
            say = (STYLES[style].get("text") or {}).get(pid, item["text"])
            data = synth.call(say)
            if not data:
                raise SystemExit(f"[{label}/{style}/{pid}] 没有音频：{synth.get_response()}")
            dst.write_bytes(data)
            print(f">> {label:<8}{style:<10}{pid} {len(data)//1024:>3} KB  {text}", flush=True)
    print(f"done -> {args.out_dir / label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
