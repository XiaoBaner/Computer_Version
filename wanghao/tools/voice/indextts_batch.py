#!/usr/bin/env python3
"""Generate the same line several times with different emotion settings, one model load.

IndexTTS-2.5 keeps timbre and emotion apart, so the only thing that changes
between these files is the emotion vector — the reference clip is identical in
every one. That makes it a fair comparison: if one of them finally sounds like a
person, it is because the emotion control worked, not because the voice changed.

Emotion order: [happy, angry, sad, afraid, disgusted, melancholic, surprised, calm]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# label -> (emo_vector, duration_factor)
VARIANTS: list[tuple[str, list[float] | None, float]] = [
    ("1-不设情绪", None, 1.0),
    ("2-平静0.7", [0, 0, 0, 0, 0, 0, 0, 0.7], 1.0),
    ("3-温柔关切", [0.15, 0, 0, 0, 0, 0, 0, 0.55], 1.0),
    ("4-轻快一点", [0.45, 0, 0, 0, 0, 0, 0, 0.35], 1.0),
    ("5-放缓一点", [0.15, 0, 0, 0, 0, 0, 0, 0.55], 1.08),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", required=True, type=Path)
    ap.add_argument("--model-dir", required=True, type=Path)
    ap.add_argument("--ref", required=True, type=Path)
    ap.add_argument("--text", required=True)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--lang", default="ZH")
    ap.add_argument("--device", default=None)
    args = ap.parse_args()

    sys.path.insert(0, str(args.src.expanduser().resolve()))
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    from indextts.infer_v2_5 import IndexTTS2  # noqa: E402

    model_dir = str(args.model_dir.expanduser().resolve())
    tts = IndexTTS2(cfg_path=os.path.join(model_dir, "config.yaml"), model_dir=model_dir,
                    use_bf16=False, use_cuda_kernel=False, use_deepspeed=False,
                    use_qwen_emo=False, device=args.device)
    print(f">> device={tts.device}", flush=True)

    out_dir = args.out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    ref = str(args.ref.expanduser().resolve())

    for label, vec, speed in VARIANTS:
        out = out_dir / f"{label}.wav"
        print(f">> {label}  vector={vec} speed={speed}", flush=True)
        tts.infer(spk_audio_prompt=ref, text=args.text, lang=args.lang,
                  output_path=str(out), emo_vector=vec, duration_factor=speed,
                  verbose=False)
        print(f">> wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
