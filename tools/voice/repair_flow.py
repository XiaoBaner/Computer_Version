#!/usr/bin/env python3
"""Tighten dead air between phrases, without touching a single sample of speech.

The clone puts a comma-level pause of roughly a full second between clauses.
Nobody talks like that — a comma is 0.2-0.4 s, and a full second is what a
speaker does when they are reading a list. Listeners hear it as "断开", not as a
pause, and no amount of pitch work hides it.

This cuts samples out of the silent stretch and nothing else: no resynthesis, no
filtering, no time-stretching, so the voice is bit-identical apart from being
shorter. Where it cuts, the signal is silence, so there is nothing to click.

Usage:
  python3 tools/voice/repair_flow.py in.wav --out out.wav --gap 0.34
"""

from __future__ import annotations

import argparse
from pathlib import Path


def tighten(y, sr: int, target_gap: float, min_gap: float = 0.45,
            head_max: float = 0.25, tail_max: float = 0.60, top_db: float = 35.0):
    """Return (signal, report). Deletes silence; never resamples or filters."""
    import librosa
    import numpy as np

    iv = librosa.effects.split(y, top_db=top_db, frame_length=1024, hop_length=256)
    if len(iv) == 0:
        return y, {"cuts": 0, "reason": "no speech"}

    keep = [0, len(y)]
    cuts = []
    # leading silence
    lead = iv[0][0]
    if lead / sr > head_max:
        keep[0] = lead - int(head_max * sr)
        cuts.append(("head", (0, keep[0])))
    # internal gaps
    for (a0, b0), (a1, b1) in zip(iv, iv[1:]):
        gap = (a1 - b0) / sr
        if gap >= min_gap:
            drop = int((gap - target_gap) * sr)
            if drop <= 0:
                continue
            # cut from the front of the gap, keeping the tail (the breath/attack
            # into the next clause stays intact)
            cuts.append(("gap", (b0, b0 + drop)))
    # trailing silence
    tail = len(y) - iv[-1][1]
    if tail / sr > tail_max:
        keep[1] = iv[-1][1] + int(tail_max * sr)
        cuts.append(("tail", (keep[1], len(y))))

    pieces = []
    cursor = 0
    for _, (a, b) in sorted(cuts, key=lambda c: c[1][0]):
        if a > cursor:
            pieces.append(y[cursor:a])
        cursor = max(cursor, b)
    if cursor < len(y):
        pieces.append(y[cursor:])
    out = np.concatenate(pieces).astype(np.float32)
    return out, {"cuts": len(cuts), "kinds": [c[0] for c in cuts],
                 "dur_in": round(len(y) / sr, 2), "dur_out": round(len(out) / sr, 2)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("src", type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--gap", type=float, default=0.34, help="what a comma should be (s)")
    ap.add_argument("--min-gap", type=float, default=0.45, help="only touch gaps longer than this")
    ap.add_argument("--head", type=float, default=0.25, help="cap leading silence (s)")
    ap.add_argument("--tail", type=float, default=0.60, help="cap trailing silence (s)")
    args = ap.parse_args()

    import librosa
    import soundfile as sf

    y, sr = librosa.load(str(args.src), sr=None, mono=True)
    out, info = tighten(y, sr, args.gap, args.min_gap, args.head, args.tail)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(args.out), out, sr)
    print(f"wrote {args.out}  {info}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
