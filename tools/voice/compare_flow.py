#!/usr/bin/env python3
"""Compare two takes of the same line on the things "一个字一个字" actually means.

Three numbers, in the order they matter:

- speech rate over *sounding* time (chars per second of non-silence). A clone that
  is slow inside words has a low number even when the total duration looks fine.
- the longest pause inside the line (a comma that is 0.5 s long sounds like a stop).
- onset density: how many separate voiced runs there are per second. "One syllable
  at a time" shows up here as many short runs.

Usage:
  python tools/voice/compare_flow.py "text" a.wav b.wav [--top-db 30]
"""

from __future__ import annotations

import argparse
import re


def voiced_runs(y, sr, top_db: float):
    import librosa

    return librosa.effects.split(y, top_db=top_db, frame_length=1024, hop_length=256)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("text")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--top-db", type=float, default=30.0)
    args = ap.parse_args()

    import librosa

    chars = len(re.sub(r"[，。？！、；：,\.\?!;:\s]", "", args.text))
    print(f"台词 {args.text}  （{chars} 个字符）\n")
    print(f"{'文件':<16}{'总长':>7}{'有声':>8}{'字/有声秒':>11}{'最长停顿':>10}{'段数':>6}")
    for path in args.files:
        y, sr = librosa.load(path, sr=None, mono=True)
        iv = voiced_runs(y, sr, args.top_db)
        if len(iv) == 0:
            print(f"{path:<16}  全静音")
            continue
        voiced = sum(b - a for a, b in iv) / sr
        gaps = [(b - a) / sr for (_, a), (b, _) in zip(iv, iv[1:])]
        longest = max(gaps) if gaps else 0.0
        print(f"{path.split('/')[-1]:<16}{len(y)/sr:>7.2f}{voiced:>8.2f}"
              f"{chars/voiced:>11.2f}{longest:>10.2f}{len(iv):>6}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
