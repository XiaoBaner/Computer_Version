#!/usr/bin/env python3
"""Build a tighter copy of the reference clip by cutting only its long pauses.

Why: the zero-shot clone copies the *prosody* of the reference as much as the
timbre. ref-B.wav is 7.46 s of a solemn vow reading with 0.79 s and 0.96 s of
silence inside it, so the model has been taught that a sentence is a row of
separated syllables read slowly. Cutting the silences (samples only, no
resynthesis) gives the model the same voice with a continuous delivery.

Usage:
  python tools/voice/make_ref_tight.py ref-B.wav ref-B-tight.wav --gap 0.12
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path)
    ap.add_argument("--gap", type=float, default=0.12, help="pause length kept between phrases (s)")
    ap.add_argument("--min-gap", type=float, default=0.30, help="only touch pauses at least this long")
    ap.add_argument("--head", type=float, default=0.05)
    ap.add_argument("--tail", type=float, default=0.15)
    ap.add_argument("--top-db", type=float, default=30.0)
    args = ap.parse_args()

    import librosa
    import soundfile as sf

    from repair_flow import tighten

    y, sr = librosa.load(str(args.src), sr=None, mono=True)
    out, info = tighten(y, sr, args.gap, args.min_gap, args.head, args.tail, args.top_db)
    args.dst.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(args.dst), out, sr)
    print(f"{args.src.name}: {info['dur_in']}s -> {info['dur_out']}s, "
          f"{info['cuts']} cuts {info.get('kinds', [])} at {args.dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
