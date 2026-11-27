#!/usr/bin/env python3
"""Speak the demo lines through Bailian's WebSocket TTS with a cloned voice.

CosyVoice on Bailian has no HTTP synthesis route — the enrollment REST call
works, but generation is WebSocket only, which is why the HTTP attempts came
back with "url error" / "Model not exist". The SDK handles that protocol, so
this runs in the dedicated venv that has `dashscope` installed.

Per line it sends the same things the local run used: the text, a speech-rate
derived from the plan's `speed`, and an `instruction` built from the emotion
vector. Whether the model honours the instruction is exactly what the
comparison is meant to find out — the script prints the voice id and model on
every run, and the audio is measured afterwards.

Usage:
  ~/.cache/cloud-venv/bin/python tools/voice/cloud_ws.py \
    --creds "/path/apiKey.csv" --pack 01 --model cosyvoice-v3-flash \
    --plan assets/voice/demos/sucaiB/lines-v2.json \
    --out-dir assets/voice/demos/packs-cloud/01
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

STATE = Path.home() / ".cache" / "voice-cloud" / "voices.json"
EMO = ["喜", "怒", "哀", "惧", "厌", "郁", "惊", "静"]


def load_creds(csv_path: Path) -> tuple[str, str]:
    fields = {}
    for line in csv_path.read_text(encoding="utf-8").splitlines():
        if "," in line:
            k, v = line.split(",", 1)
            fields[k.strip()] = v.strip().strip('"')
    return fields["apiKey"], fields["apiHost"]


def instruct_for(item: dict) -> str:
    vec = item.get("emo") or []
    top = max(range(len(vec)), key=lambda i: vec[i]) if vec else None
    words = {0: "语气轻快一点", 1: "语气带一点急", 2: "声音低一点，透着心疼",
             3: "收着一点，安抚的语气", 4: "语气淡一点", 5: "放慢，声音低沉",
             6: "语气稍微提起来", 7: "安静温和地说"}
    bits = []
    if top is not None and vec[top] > 0.05:
        bits.append(words.get(top, "温和地说"))
    # Speed is NOT put in the instruction: measured, "语速稍慢" alone stretched a
    # 3.6 s line to 5.7 s. Rate belongs to speech_rate, which moves it by a few
    # percent instead of a third.
    return "，".join(bits) + "，像对朋友说话，不要念稿"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--creds", required=True, type=Path)
    ap.add_argument("--pack", required=True, help="pack name, e.g. 01")
    ap.add_argument("--model", default="cosyvoice-v3-flash")
    ap.add_argument("--plan", default="assets/voice/demos/sucaiB/lines-v2.json")
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--instruct", action="store_true", default=True)
    ap.add_argument("--no-instruct", dest="instruct", action="store_false")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    import dashscope
    from dashscope.audio.tts_v2 import AudioFormat, SpeechSynthesizer

    key, host = load_creds(args.creds)
    dashscope.api_key = key
    dashscope.base_websocket_api_url = f"wss://{host}/api-ws/v1/inference"

    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    voice = (state.get(args.pack) or {}).get("voice")
    if not voice or not voice.startswith(args.model):
        raise SystemExit(f"{args.pack} 没有绑定 {args.model} 的 voice id；先跑 enroll。")

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    items = list(plan.get("lines", [])) + list(plan.get("alts", []))
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)

    print(f">> model={args.model} voice={voice} packs={args.pack}")
    for i, item in enumerate(items, 1):
        dst = out / f"{item['id']}.wav"
        if dst.exists() and not args.force:
            print(f">> skip {dst.name}")
            continue
        rate = round(1.0 / float(item.get("speed", 1.0)), 3)  # plan speed >1 = slower
        kw = dict(format=AudioFormat.WAV_24000HZ_MONO_16BIT, speech_rate=rate)
        if args.instruct:
            kw["instruction"] = instruct_for(item)
        synth = SpeechSynthesizer(model=args.model, voice=voice, **kw)
        data = synth.call(item["text"])
        if not data:
            raise SystemExit(f"[{item['id']}] 没有音频：{synth.get_response()}")
        dst.write_bytes(data)
        print(f">> [{i}/{len(items)}] {item['id']} {len(data) // 1024} KB  {instruct_for(item) if args.instruct else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
