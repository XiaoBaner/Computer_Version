#!/usr/bin/env python3
"""Two things a dry studio voice is missing that a room recording has.

Neither is a gimmick: both are differences between "a signal someone synthesised"
and "a person talking into a microphone in a room", and both are plain filtering
and convolution — no resynthesis, so the voice itself is untouched.

  --air    a gentle high-shelf cut. Generator output is often brittle above
           6-7 kHz, which reads as "crisp/machine" rather than "warm". -3 dB is
           enough to notice; 0 leaves it alone.
  --room   a very short synthetic room: a handful of early reflections and a
           200 ms diffuse tail, well below speech level. Real takes are never
           anechoic, and a completely dry voice is one of the strongest cues
           that nobody is in the room.

Usage:
  python3 tools/voice/polish.py in.wav --out out.wav --air -3 --room -22
"""

from __future__ import annotations

import argparse
from pathlib import Path


def high_shelf(y, sr: int, gain_db: float, fc: float = 6500.0):
    """First-order shelving cut/boost above fc (negative = darker)."""
    import numpy as np

    if abs(gain_db) < 1e-6:
        return y
    a = 10 ** (gain_db / 40.0)
    w0 = 2 * np.pi * fc / sr
    alpha = np.sin(w0) / (2 * 0.707)
    cosw = np.cos(w0)
    b0 = a * ((a + 1) + (a - 1) * cosw + 2 * np.sqrt(a) * alpha)
    b1 = -2 * a * ((a - 1) + (a + 1) * cosw)
    b2 = a * ((a + 1) + (a - 1) * cosw - 2 * np.sqrt(a) * alpha)
    a0 = (a + 1) - (a - 1) * cosw + 2 * np.sqrt(a) * alpha
    a1 = 2 * ((a - 1) - (a + 1) * cosw)
    a2 = (a + 1) - (a - 1) * cosw - 2 * np.sqrt(a) * alpha
    from scipy.signal import lfilter
    return lfilter([b0 / a0, b1 / a0, b2 / a0], [1.0, a1 / a0, a2 / a0], y)


def synth_room(sr: int, rt60: float = 0.22, seed: int = 5):
    """A small, dead room: early reflections plus a short exponential tail."""
    import numpy as np

    rng = np.random.default_rng(seed)
    n = int(rt60 * 1.6 * sr)
    ir = np.zeros(n, dtype=np.float64)
    # early reflections: a few discrete arrivals in the first 25 ms
    for t_ms, g in ((4, 0.55), (7, 0.38), (11, 0.28), (16, 0.20), (23, 0.13)):
        i = int(t_ms / 1000 * sr)
        if i < n:
            ir[i] += g * rng.choice([-1, 1])
    # diffuse tail, decaying
    tail = rng.standard_normal(n) * np.exp(-np.arange(n) / (rt60 * sr / 6.9))
    ir += 0.12 * tail
    ir[: int(0.002 * sr)] = 0  # keep the direct sound clean
    return ir / np.sqrt(np.sum(ir ** 2))


def polish(src: Path, dst: Path, air_db: float, room_db: float, rt60: float = 0.22,
           presence_db: float = 0.0) -> dict:
    import librosa
    import numpy as np
    import soundfile as sf
    from scipy.signal import fftconvolve

    y, sr = librosa.load(str(src), sr=None, mono=True)
    out = y
    if abs(presence_db) > 1e-6:
        # The clone sits ~5 dB below the human reference between 1.2 and 4 kHz —
        # the band that carries consonant presence. Restoring it is the closest
        # thing to "same voice, but someone is actually in the room".
        out = high_shelf(out, sr, presence_db, fc=1500.0)
    out = high_shelf(out, sr, air_db)
    if room_db > -100:
        ir = synth_room(sr, rt60)
        wet = fftconvolve(out, ir)[: len(out)]
        wet *= np.sqrt(np.mean(out ** 2)) / (np.sqrt(np.mean(wet ** 2)) + 1e-9)
        out = out + wet * (10 ** (room_db / 20))
    peak = float(np.abs(out).max())
    if peak > 0.89:
        out = out * (0.89 / peak)
    sf.write(str(dst), out.astype(np.float32), sr)
    return {"air_db": air_db, "room_db": room_db, "rt60": rt60,
            "dur": round(len(out) / sr, 2)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("src", type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--air", type=float, default=0.0, help="high-shelf gain above 6.5 kHz (dB)")
    ap.add_argument("--presence", type=float, default=0.0,
                    help="shelf gain above 1.5 kHz (dB); +4 restores the balance of the human reference")
    ap.add_argument("--room", type=float, default=-120.0, help="reverb level rel. to speech (dB)")
    ap.add_argument("--rt60", type=float, default=0.22)
    args = ap.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    info = polish(args.src, args.out, args.air, args.room, args.rt60, args.presence)
    print(f"wrote {args.out}  {info}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
