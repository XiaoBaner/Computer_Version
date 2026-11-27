#!/usr/bin/env python3
"""Design specific voices with Qwen3-TTS-12Hz-1.7B-VoiceDesign.

Unlike CustomVoice (9 fixed speakers), VoiceDesign takes a natural-language
description of a person and invents a matching timbre. This is how to get away
from the "assistant voice" feel without cloning anyone.

Example:
    python3 tools/voice/synth_voicedesign.py \
      --model-dir ~/.cache/modelscope/models/Qwen--Qwen3-TTS-12Hz-1.7B-VoiceDesign/snapshots/master \
      --plan-json assets/voice/demos/voicedesign-plan.json \
      --out assets/voice/demos/voicedesign --device mps
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

DEFAULT_TEXT = (
    "嗯……我们可以慢一点。你刚才说到导师，停了一下。"
    "今天挺累的吧？最烦的是哪件事？"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Synthesize designed voices with Qwen3-TTS VoiceDesign.")
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--plan-json", required=True, type=Path,
                        help="JSON array: name, instruct, optional text/language/seed/sampling")
    parser.add_argument("--device", default="mps")
    parser.add_argument("--dtype", default="bfloat16", choices=["bfloat16", "float16", "float32"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel

    dtype = getattr(torch, args.dtype)
    if args.device == "cpu" and args.dtype != "float32":
        dtype = torch.float32

    print(f"loading {args.model_dir} on {args.device} ({args.dtype})", flush=True)
    started = time.time()
    model = Qwen3TTSModel.from_pretrained(str(args.model_dir), dtype=dtype, low_cpu_mem_usage=True)
    model.model.to(args.device)
    model.model.eval()
    model.device = torch.device(args.device)
    print(f"loaded in {time.time() - started:.1f}s", flush=True)

    args.out.mkdir(parents=True, exist_ok=True)
    plan = json.loads(args.plan_json.read_text(encoding="utf-8"))

    records = []
    for item in plan:
        name = item["name"]
        torch.manual_seed(item.get("seed", 7))
        t0 = time.time()
        try:
            wavs, sr = model.generate_voice_design(
                text=item.get("text", DEFAULT_TEXT),
                instruct=item["instruct"],
                language=item.get("language", "Chinese"),
                top_k=item.get("top_k", 50),
                top_p=item.get("top_p", 0.95),
                temperature=item.get("temperature", 0.9),
                repetition_penalty=item.get("repetition_penalty", 1.05),
            )
        except Exception as exc:
            print(f"FAIL {name}: {type(exc).__name__}: {exc}", flush=True)
            records.append({"name": name, "error": str(exc)})
            continue

        wav = wavs[0]
        path = args.out / f"{name}.wav"
        sf.write(str(path), wav, sr)
        seconds = len(wav) / sr
        elapsed = time.time() - t0
        print(f"{path.name}: {seconds:.2f}s audio in {elapsed:.1f}s (sr={sr})", flush=True)
        records.append(
            {
                "name": name,
                "instruct": item["instruct"],
                "text": item.get("text", DEFAULT_TEXT),
                "file": path.name,
                "sample_rate": sr,
                "duration": round(seconds, 3),
                "gen_seconds": round(elapsed, 2),
                "seed": item.get("seed", 7),
            }
        )

    (args.out / "meta.json").write_text(
        json.dumps({"model": str(args.model_dir), "records": records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    failures = sum(1 for r in records if "error" in r)
    print(f"done: {len(records) - failures}/{len(records)} ok -> {args.out}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
