#!/usr/bin/env python3
"""Run IndexTTS-2.5 (the local "TTS2") on one line, with emotion control.

IndexTTS-2.5 separates timbre from emotion: the reference clip fixes *who* is
speaking, and an 8-number emotion vector (or a short emotion description) sets
*how*. That is the thing Qwen3-TTS-Base could not do — it has no instruct path
at all — so this is the test of whether the "robot" feel is our model's ceiling
rather than our prompting.

The code is not installed as a package (its repo downloads fine but `pip
install -e` on external source is not something to run blindly), so the source
directory is added to sys.path here and passed in as an argument.

Emotion vector order: [happy, angry, sad, afraid, disgusted, melancholic,
surprised, calm]. Values 0..1.

Usage:
  ~/.cache/indextts-venv/bin/python tools/voice/indextts_run.py \
    --src ~/.cache/indextts-src/src \
    --model-dir ~/.cache/modelscope/indextts2.5 \
    --ref assets/voice/refs/sucaiB/ref-B.wav \
    --text "今天也辛苦了，要不要先休息一下？" \
    --emo-vector 0.15,0,0,0,0,0.1,0,0.6 \
    --out assets/voice/demos/sucaiB/it2/t1.wav
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def parse_vec(s: str) -> list[float]:
    vals = [float(x) for x in s.replace("，", ",").split(",") if x.strip() != ""]
    if len(vals) != 8:
        raise SystemExit(f"--emo-vector needs 8 numbers, got {len(vals)}")
    return vals


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", required=True, type=Path, help="index-tts source tree")
    ap.add_argument("--model-dir", required=True, type=Path)
    ap.add_argument("--ref", required=True, type=Path, help="speaker reference clip")
    ap.add_argument("--text", required=True)
    ap.add_argument("--lang", default="ZH")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--emo-vector", default=None, help="happy,angry,sad,afraid,disgusted,melancholic,surprised,calm")
    ap.add_argument("--emo-text", default=None, help="emotion description; needs the Qwen emotion model")
    ap.add_argument("--emo-audio", default=None, help="emotional reference clip")
    ap.add_argument("--emo-alpha", type=float, default=1.0)
    ap.add_argument("--speed", type=float, default=1.0, help="duration_factor; >1 = slower")
    ap.add_argument("--device", default=None, help="cuda:0 / mps / cpu; auto if omitted")
    args = ap.parse_args()

    sys.path.insert(0, str(args.src.expanduser().resolve()))
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    from indextts.infer_v2_5 import IndexTTS2  # noqa: E402

    model_dir = str(args.model_dir.expanduser().resolve())
    tts = IndexTTS2(
        cfg_path=os.path.join(model_dir, "config.yaml"),
        model_dir=model_dir,
        use_bf16=False,
        use_cuda_kernel=False,
        use_deepspeed=False,
        use_qwen_emo=bool(args.emo_text),
        device=args.device,
    )
    print(f">> device={tts.device}", flush=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    tts.infer(
        spk_audio_prompt=str(args.ref.expanduser().resolve()),
        text=args.text,
        lang=args.lang,
        output_path=str(args.out),
        emo_vector=parse_vec(args.emo_vector) if args.emo_vector else None,
        emo_audio_prompt=str(args.emo_audio) if args.emo_audio else None,
        emo_alpha=args.emo_alpha,
        use_emo_text=bool(args.emo_text),
        emo_text=args.emo_text,
        duration_factor=args.speed,
        verbose=True,
    )
    print(f">> wrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
