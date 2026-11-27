#!/usr/bin/env python3
"""Generate every line the demo actually speaks, with one fixed emotion setting.

The five-way emotion test settled on [happy 0.45, calm 0.35]; this bakes that
choice into the whole dialogue so the voice is consistent across the demo
instead of per-line guesswork. One model load, one reference clip, N lines.

Lines come from contracts/examples.v1.json — the same offline script the demo
plays back — so what you hear here is what the demo would say.

Usage:
  ~/.cache/indextts-venv/bin/python tools/voice/indextts_lines.py \
    --src ~/.cache/indextts-src/src --model-dir ~/.cache/modelscope/indextts2.5 \
    --ref assets/voice/refs/sucaiB/ref-B.wav \
    --lines assets/voice/demos/sucaiB/lines.txt \
    --out-dir assets/voice/demos/sucaiB/lines
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

EMO_HAPPY = 0.45
EMO_CALM = 0.35


def slug(text: str, i: int) -> str:
    keep = re.sub(r"[^一-鿿0-9a-zA-Z]", "", text)[:12]
    return f"{i:02d}-{keep}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", required=True, type=Path)
    ap.add_argument("--model-dir", required=True, type=Path)
    ap.add_argument("--ref", required=True, type=Path)
    ap.add_argument("--lines", required=True, type=Path, help="one line of dialogue per line")
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--lang", default="ZH")
    ap.add_argument("--speed", type=float, default=1.0)
    args = ap.parse_args()

    texts = [ln.strip() for ln in args.lines.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not texts:
        raise SystemExit("no lines to speak")

    sys.path.insert(0, str(args.src.expanduser().resolve()))
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    from indextts.infer_v2_5 import IndexTTS2  # noqa: E402

    model_dir = str(args.model_dir.expanduser().resolve())
    tts = IndexTTS2(cfg_path=os.path.join(model_dir, "config.yaml"), model_dir=model_dir,
                    use_bf16=False, use_cuda_kernel=False, use_deepspeed=False,
                    use_qwen_emo=False)
    print(f">> device={tts.device}, {len(texts)} 句", flush=True)

    out_dir = args.out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    ref = str(args.ref.expanduser().resolve())
    vec = [EMO_HAPPY, 0, 0, 0, 0, 0, 0, EMO_CALM]

    for i, text in enumerate(texts, 1):
        out = out_dir / f"{slug(text, i)}.wav"
        if out.exists():
            print(f">> skip {out.name}", flush=True)
            continue
        print(f">> [{i}/{len(texts)}] {text}", flush=True)
        tts.infer(spk_audio_prompt=ref, text=text, lang=args.lang, output_path=str(out),
                  emo_vector=vec, duration_factor=args.speed, verbose=False)
        print(f">> wrote {out.name}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
