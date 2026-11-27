#!/usr/bin/env python3
"""Generate tuned samples with a local Qwen3-TTS CustomVoice checkpoint.

This is deliberately a thin, single-process script: load once, synthesize one
(speaker, style) pair at a time, and write a metadata JSON next to the WAVs so a
blind test can be reproduced later.

Example:
    python3 tools/voice/synth_qwen_samples.py \
      --model-dir ~/.cache/modelscope/hub/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice \
      --out assets/voice/demos/qwen3tts \
      --speakers Serena,Vivian,Dylan,Uncle_Fu \
      --device mps

The output layout matches tools/voice/make_blind_test.py only if you keep one
folder per candidate; here everything goes into one flat folder plus meta.json.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

DEFAULT_TEXT = (
    "我们可以慢一点。你刚才说到导师，然后停了一下。"
    "听起来你今天挺累的，要不要先跟我说说，最让你烦的是哪一件事？"
)

# Project tones from docs/voice-tuning.md §6. `normal`/`slow` are kept as
# extra baselines for A/B listening.
STYLES: dict[str, str] = {
    "normal": "",
    "neutral": "自然中文对话，常速，句尾自然收住，不要播音腔。",
    "gentle": "音量平稳，语速比正常略慢，句间留白，声音贴近，像在安静的房间里认真听人说话。",
    "practice": "清楚地示范一遍，关键词轻微重读，像面对面和一个人说话。",
    "check_in": "平和，句子简短，保持自然的问句语调，问完停住。",
    "slow": "语速明显放慢，语气平稳，句子之间停顿稍长，像在慢慢陪着对方说话，不要播音腔。",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Synthesize Qwen3-TTS CustomVoice samples.")
    parser.add_argument("--model-dir", required=True, type=Path, help="local checkpoint directory")
    parser.add_argument("--out", required=True, type=Path, help="output directory")
    parser.add_argument("--speakers", default="Serena,Vivian", help="comma-separated speaker ids")
    parser.add_argument("--styles", default="normal,gentle,slow", help="comma-separated style keys")
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument(
        "--texts-json",
        type=Path,
        help='JSON object mapping style -> text, e.g. {"check_in": "你今天还好吗？"}',
    )
    parser.add_argument(
        "--plan-json",
        type=Path,
        help="JSON array of per-sample objects: name, speaker, text, instruct, optional sampling overrides",
    )
    parser.add_argument("--device", default="mps", help="mps, cpu, or cuda:0")
    parser.add_argument("--dtype", default="bfloat16", choices=["bfloat16", "float16", "float32"])
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--top-k", type=int, default=50)
    parser.add_argument("--top-p", type=float, default=0.90)
    parser.add_argument("--temperature", type=float, default=0.80)
    parser.add_argument("--repetition-penalty", type=float, default=1.05)
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel

    dtype = getattr(torch, args.dtype)
    device = args.device
    if device == "cpu" and args.dtype != "float32":
        dtype = torch.float32

    print(f"loading {args.model_dir} on {device} ({args.dtype})", flush=True)
    started = time.time()
    model = Qwen3TTSModel.from_pretrained(
        str(args.model_dir),
        dtype=dtype,
        low_cpu_mem_usage=True,
    )
    model.model.to(device)
    model.model.eval()
    # The wrapper cached model.device before .to(); keep it in sync or it will
    # hand CPU tensors to MPS ops.
    model.device = torch.device(device)
    print(f"loaded in {time.time() - started:.1f}s; speakers={model.model.get_supported_speakers()}", flush=True)

    args.out.mkdir(parents=True, exist_ok=True)
    speakers = [s.strip() for s in args.speakers.split(",") if s.strip()]
    styles = [s.strip() for s in args.styles.split(",") if s.strip()]
    per_style_text: dict[str, str] = {}
    if args.texts_json:
        per_style_text = json.loads(args.texts_json.read_text(encoding="utf-8"))

    if args.plan_json:
        # Full control: one object per sample, each may override text, instruct,
        # speaker and sampling. Used for A/B experiments where only one knob
        # should change at a time.
        items = []
        for index, raw in enumerate(json.loads(args.plan_json.read_text(encoding="utf-8"))):
            items.append(
                {
                    "name": raw.get("name") or f"item{index:02d}",
                    "style": raw.get("style", raw.get("name", f"item{index:02d}")),
                    "speaker": raw["speaker"],
                    "language": raw.get("language", "Chinese"),
                    "text": raw.get("text", args.text),
                    "instruct": raw.get("instruct", ""),
                    "top_k": raw.get("top_k", args.top_k),
                    "top_p": raw.get("top_p", args.top_p),
                    "temperature": raw.get("temperature", args.temperature),
                    "repetition_penalty": raw.get("repetition_penalty", args.repetition_penalty),
                    "seed": raw.get("seed", args.seed),
                }
            )
    else:
        items = [
            {
                "name": f"{speaker.lower()}-{style}",
                "style": style,
                "speaker": speaker,
                "language": "Chinese",
                "text": per_style_text.get(style, args.text),
                "instruct": STYLES.get(style, style),
                "top_k": args.top_k,
                "top_p": args.top_p,
                "temperature": args.temperature,
                "repetition_penalty": args.repetition_penalty,
                "seed": args.seed,
            }
            for speaker in speakers
            for style in styles
        ]

    records = []
    for item in items:
        torch.manual_seed(item["seed"])
        t0 = time.time()
        try:
            wavs, sr = model.generate_custom_voice(
                text=item["text"],
                language=item["language"],
                speaker=item["speaker"],
                instruct=item["instruct"],
                top_k=item["top_k"],
                top_p=item["top_p"],
                temperature=item["temperature"],
                repetition_penalty=item["repetition_penalty"],
            )
        except Exception as exc:  # keep going: one bad item should not kill the run
            print(f"FAIL {item['name']}: {type(exc).__name__}: {exc}", flush=True)
            records.append({"name": item["name"], "style": item["style"], "error": str(exc)})
            continue

        wav = wavs[0]
        name = f"{item['name']}.wav"
        path = args.out / name
        sf.write(str(path), wav, sr)
        elapsed = time.time() - t0
        duration = len(wav) / sr
        print(f"{name}: {duration:.2f}s audio in {elapsed:.1f}s (sr={sr})", flush=True)
        records.append(
            {
                "name": item["name"],
                "speaker": item["speaker"],
                "style": item["style"],
                "text": item["text"],
                "instruct": item["instruct"],
                "file": name,
                "sample_rate": sr,
                "duration": round(duration, 3),
                "gen_seconds": round(elapsed, 2),
                "sampling": {k: item[k] for k in ("top_k", "top_p", "temperature", "repetition_penalty", "seed")},
            }
        )

    meta = {
        "model": str(args.model_dir),
        "text": args.text,
        "texts_by_style": per_style_text,
        "device": device,
        "dtype": args.dtype,
        "sampling": {
            "top_k": args.top_k,
            "top_p": args.top_p,
            "temperature": args.temperature,
            "repetition_penalty": args.repetition_penalty,
            "seed": args.seed,
        },
        "records": records,
    }
    (args.out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    failures = sum(1 for r in records if "error" in r)
    print(f"done: {len(records) - failures}/{len(records)} ok -> {args.out}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
