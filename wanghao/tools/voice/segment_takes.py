#!/usr/bin/env python3
"""Split one long recording into single-sentence takes by silence.

Record all lines in one file with a clear pause between them (count "one, two"
in your head), then run this. It trims silence, drops takes that are too short
(coughs, false starts) or too long (two sentences ran together), and writes
24 kHz mono 16-bit WAVs ready for normalize_wav.py and lock_voice.py.

Example:
    python3 tools/voice/segment_takes.py --audio takes.m4a --out assets/voice/refs/me
"""

from __future__ import annotations

import argparse
import wave
from pathlib import Path

import librosa
import numpy as np


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Cut a long recording into sentence takes.")
    p.add_argument("--audio", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--sr", type=int, default=24000, help="output sample rate")
    p.add_argument("--min-s", type=float, default=0.8, help="drop takes shorter than this")
    p.add_argument("--max-s", type=float, default=20.0, help="warn on takes longer than this")
    p.add_argument("--silence-db", type=float, default=-38.0,
                   help="threshold below the loudest part; lower = keeps more quiet speech")
    p.add_argument("--pad-s", type=float, default=0.12, help="padding kept around each take")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    y, sr = librosa.load(args.audio, sr=args.sr, mono=True)
    if y.size == 0:
        print("empty audio")
        return 2

    top_db = abs(args.silence_db)
    intervals = librosa.effects.split(y, top_db=top_db, frame_length=2048, hop_length=512)

    args.out.mkdir(parents=True, exist_ok=True)
    pad = int(args.pad_s * sr)
    kept = dropped = 0
    for idx, (start, end) in enumerate(intervals, start=1):
        s, e = max(0, start - pad), min(len(y), end + pad)
        clip = y[s:e]
        dur = len(clip) / sr
        if dur < args.min_s:
            dropped += 1
            continue
        if dur > args.max_s:
            print(f"  ! take {idx:02d}: {dur:.1f}s — probably two sentences, listen and split by hand")
        peak = float(np.max(np.abs(clip))) or 1.0
        pcm = np.clip(clip / peak * 0.95, -1.0, 1.0)
        path = args.out / f"take_{kept + 1:02d}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes((pcm * 32767).astype("<i2").tobytes())
        print(f"  take_{kept + 1:02d}.wav  {dur:5.2f}s")
        kept += 1

    print(f"\nkept {kept}, dropped {dropped} (< {args.min_s}s). Listen to every take before using them.")
    print("Next: python3 tools/voice/normalize_wav.py <out>/*.wav --out <out> --force")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
