#!/usr/bin/env python3
"""Speak a dialogue where every line has its own emotion, from one plan file.

A single global emotion vector makes all eleven lines feel the same — the machine
compliment you did not ask for. But "好，我听着。" and "今天也辛苦了" are not said
the same way, so the plan file carries a vector and a speed per line, plus the
reason for it, so the choice can be argued with instead of guessed at.

Plan format (JSON): {"lines": [{"id","text","emo":[8 floats],"speed","why"}], "alts": [...]}
Emotion order: [happy, angry, sad, afraid, disgusted, melancholic, surprised, calm]

Usage:
  ~/.cache/indextts-venv/bin/python tools/voice/indextts_plan.py \
    --src ~/.cache/indextts-src/src --model-dir ~/.cache/modelscope/indextts2.5 \
    --ref assets/voice/refs/sucaiB/ref-B.wav \
    --plan assets/voice/demos/sucaiB/lines-v2.json \
    --out-dir assets/voice/demos/sucaiB/lines-v2
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", required=True, type=Path)
    ap.add_argument("--model-dir", required=True, type=Path)
    ap.add_argument("--ref", required=True, type=Path)
    ap.add_argument("--plan", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--lang", default="ZH")
    args = ap.parse_args()

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    items = list(plan.get("lines", [])) + list(plan.get("alts", []))
    if not items:
        raise SystemExit("plan has no lines")

    sys.path.insert(0, str(args.src.expanduser().resolve()))
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    from indextts.infer_v2_5 import IndexTTS2  # noqa: E402

    model_dir = str(args.model_dir.expanduser().resolve())
    tts = IndexTTS2(cfg_path=os.path.join(model_dir, "config.yaml"), model_dir=model_dir,
                    use_bf16=False, use_cuda_kernel=False, use_deepspeed=False,
                    use_qwen_emo=False)
    print(f">> device={tts.device}, {len(items)} 句", flush=True)

    out_dir = args.out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    ref = str(args.ref.expanduser().resolve())

    for i, item in enumerate(items, 1):
        out = out_dir / f"{item['id']}.wav"
        if out.exists():
            print(f">> skip {out.name}", flush=True)
            continue
        print(f">> [{i}/{len(items)}] {item['id']} {item['text']}  emo={item['emo']} speed={item.get('speed', 1.0)}",
              flush=True)
        tts.infer(spk_audio_prompt=ref, text=item["text"], lang=args.lang, output_path=str(out),
                  emo_vector=[float(x) for x in item["emo"]],
                  duration_factor=float(item.get("speed", 1.0)),
                  verbose=False)
        print(f">> wrote {out.name}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
