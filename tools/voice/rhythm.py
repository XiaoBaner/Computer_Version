#!/usr/bin/env python3
"""Break the metronome: vary the spacing between syllables, nothing else.

Measured on our own takes against three lab recordings, the split is not pitch
(ours has 10.7–11.3 semitones of range, inside the human spread) and not speed
(6.2 characters per sounding second, also normal). It is **regularity**: the
time between energy dips between syllables has a coefficient of variation of
0.55 in our takes and 1.02–1.45 in the human recordings. Every syllable lands
on the same beat, which is exactly what "一个字一个字" describes.

So this script warps time only — Praat's duration tier, pitch grid at 2.5 ms,
no pitch change, no spectral touch — with per-syllable factors drawn from a
smooth irregular sequence, normalised so the overall tempo is unchanged.

Usage:
  python tools/voice/rhythm.py in.wav --out out.wav --spread 0.30
"""

from __future__ import annotations

import argparse
from pathlib import Path


def nuclei(sound, hop_s: float = 0.010):
    """Syllable-ish nuclei: peaks of the energy envelope, one per ~90 ms at most."""
    import numpy as np
    from scipy.signal import find_peaks

    y = np.asarray(sound.values[0] if sound.values.ndim > 1 else sound.values,
                   dtype=np.float64)
    sr = sound.sampling_frequency
    hop = max(1, int(hop_s * sr))
    frames = max(1, len(y) // hop)
    rms = np.array([np.sqrt(np.mean(y[i * hop:(i + 1) * hop] ** 2)) for i in range(frames)])
    db = 20 * np.log10(np.maximum(rms, 1e-8))
    # Smoothed contour: raw 10 ms RMS is too jagged to place nuclei on.
    k = np.ones(5) / 5
    sm = np.convolve(db, k, mode="same")
    peaks, _ = find_peaks(sm, distance=max(1, int(0.09 / hop_s)),
                          prominence=2.0, height=sm.max() - 28.0)
    return [float(p * hop / sr) for p in peaks]


def warp(src: Path, dst: Path, spread: float, fmin: float = 75.0,
         dt: float = 0.0025) -> dict:
    import numpy as np
    import parselmouth
    import soundfile as sf
    from parselmouth.praat import call

    snd = parselmouth.Sound(str(src))
    marks = nuclei(snd)
    if len(marks) < 3:
        raise SystemExit(f"{src}: found only {len(marks)} nuclei")
    bounds = [0.0] + [(a + b) / 2 for a, b in zip(marks, marks[1:])] + [snd.duration]

    # Smooth, incommensurate, deterministic: same input -> same output.
    fac = []
    for i, x in enumerate(bounds[:-1]):
        s = (np.sin(0.9 * i + 0.4) + 0.7 * np.sin(2.3 * i + 1.7)
             + 0.4 * np.sin(0.37 * i + 3.1)) / 2.1
        fac.append(1.0 + spread * float(s))
    fac = np.array(fac)
    fac = fac / fac.mean()  # keep the line's total duration

    manip = call(snd, "To Manipulation", dt, fmin, 600.0)
    dur = call("Create DurationTier", "rhythm", 0.0, snd.duration + 1.0)
    for i, (a, b) in enumerate(zip(bounds[:-1], bounds[1:])):
        # Hold the factor flat across the interval: two points, not one, or
        # Praat ramps linearly into the next interval and smears the rhythm.
        call(dur, "Add point", a + 1e-3, float(fac[i]))
        call(dur, "Add point", max(a + 1e-3, b - 1e-3), float(fac[i]))
    call([manip, dur], "Replace duration tier")
    res = call(manip, "Get resynthesis (overlap-add)")
    y = res.values[0] if res.values.ndim > 1 else res.values
    y = np.asarray(y, dtype=np.float32)
    peak = float(np.abs(y).max()) or 1.0
    if peak > 0.89:
        y = y * (0.89 / peak)
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(dst), y, int(res.sampling_frequency))
    return {"nuclei": len(marks), "spread": spread,
            "dur_in": round(snd.duration, 3),
            "dur_out": round(len(y) / res.sampling_frequency, 3),
            "fac_range": (round(float(fac.min()), 2), round(float(fac.max()), 2))}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("src", type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--spread", type=float, default=0.30,
                    help="how far each syllable's duration moves from the beat (0.3 = ±30%%)")
    ap.add_argument("--dt", type=float, default=0.0025)
    args = ap.parse_args()
    print(f"wrote {args.out}  {warp(args.src, args.out, args.spread, dt=args.dt)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
