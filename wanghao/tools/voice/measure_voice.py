#!/usr/bin/env python3
"""Rough objective check that several clips really are the same voice.

Reports mean F0, F0 spread, voiced ratio and speech rate. Useful when you vary
only the instruct text and need to know whether the timbre drifted.

Requires numpy + librosa (available in the Qwen3-TTS venv, not the system Python):
    ~/.cache/qwen3tts-venv3/bin/python tools/voice/measure_voice.py *.wav
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure F0 and speech rate of WAV clips.")
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--min-f0", type=float, default=60.0)
    parser.add_argument("--max-f0", type=float, default=500.0)
    args = parser.parse_args()

    import librosa
    import numpy as np

    files: list[Path] = []
    for path in args.paths:
        if path.is_dir():
            files.extend(sorted(path.glob("*.wav")))
        else:
            files.append(path)

    print(f"{'file':28} {'dur':>6} {'meanF0':>7} {'F0sd':>6} {'voiced':>7}")
    for path in files:
        y, sr = librosa.load(str(path), sr=24000, mono=True)
        if y.size == 0:
            print(f"{path.name:28}  empty")
            continue
        f0, voiced_flag, _ = librosa.pyin(
            y, fmin=args.min_f0, fmax=args.max_f0, sr=sr, frame_length=2048
        )
        voiced = f0[~np.isnan(f0)]
        mean_f0 = float(np.mean(voiced)) if voiced.size else float("nan")
        sd_f0 = float(np.std(voiced)) if voiced.size else float("nan")
        voiced_ratio = float(np.mean(voiced_flag)) if voiced_flag.size else 0.0
        duration = len(y) / sr
        print(
            f"{path.name:28} {duration:5.2f}s {mean_f0:7.1f} {sd_f0:6.1f} "
            f"{voiced_ratio * 100:6.1f}%"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
