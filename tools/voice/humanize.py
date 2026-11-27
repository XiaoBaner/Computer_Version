#!/usr/bin/env python3
"""Add the cues a synthetic take is missing, without touching its timbre.

A cloned line can have the right voice and still read as "not a person". Two
things listeners notice that sampling never produces:

  * **breaths.** Speakers inhale before phrases; a clone jumps from silence
    straight into voicing. A soft band-limited inhale, well below speech level,
    placed just before each new phrase start does most of the work.
  * **micro-dynamics.** Human loudness wanders by a decibel or so over a few
    seconds; synthetic output holds a flat envelope.

Both are deliberately conservative — the point is to stop the clip sounding
machined, not to add an audible effect. Levels are documented so you can dial
them and compare, and every knob is off-able to hear the difference.

Usage:
  python3 tools/voice/humanize.py in.wav --out out.wav --breath-db -24 --dynamics 0.02
"""

from __future__ import annotations

import argparse

import numpy as np


def make_inhale(n: int, sr: int, rng) -> np.ndarray:
    """A breath, not a hiss.

    Flat band-limited noise reads as tape hiss because it has no shape. An inhale
    passes through the vocal tract, so it carries a broad resonance around
    1.2-2 kHz and rolls off above ~4 kHz; it also swells slowly and is cut off
    when the vocal folds close. Both are applied here.
    """
    from scipy.signal import lfilter, iirpeak

    noise = rng.standard_normal(n)
    # Merge two broad vocal-tract resonances (a resonant inhale, not broadband hiss).
    b1, a1 = iirpeak(1500.0 / (sr / 2), 1500.0 / 900.0)
    b2, a2 = iirpeak(750.0 / (sr / 2), 750.0 / 500.0)
    y = lfilter(b1, a1, noise) + 0.6 * lfilter(b2, a2, noise)
    spec = np.fft.rfft(y)
    freqs = np.fft.rfftfreq(n, 1 / sr)
    # Roll off the very top: breath energy above 4 kHz is what sounds like hiss.
    spec[freqs > 4000] *= np.exp(-(freqs[freqs > 4000] - 4000) / 1800.0)
    spec[freqs < 300] = 0
    x = np.fft.irfft(spec, n)
    x /= np.max(np.abs(x)) + 1e-9
    t = np.linspace(0, 1, n)
    envelope = np.sin(np.pi * np.clip(t, 0, 1)) ** 2.0
    envelope[-max(1, n // 8):] *= np.linspace(1.0, 0.0, max(1, n // 8))
    return x * envelope


def humanize(y: np.ndarray, sr: int, breath_db: float, dynamics: float,
             min_gap: float, seed: int = 7,
             lead_in: float = 0.0, breath_len: float = 0.0) -> tuple[np.ndarray, dict]:
    import librosa

    rng = np.random.default_rng(seed)
    intervals = librosa.effects.split(y, top_db=35, frame_length=1024, hop_length=256)
    if len(intervals) == 0:
        return y, {"breaths": 0}

    speech_rms = float(np.sqrt(np.mean(y[intervals[0][0]:intervals[-1][1]] ** 2)))
    pieces: list[np.ndarray] = []
    cursor = 0
    breaths = 0

    # A generated clip often starts on its very first voiced sample: no room, no
    # breath, straight into speech. Real turns have a lead-in, so offer one.
    if lead_in > 0:
        n = int(lead_in * sr)
        pad = np.zeros(n, dtype=np.float32)
        if breath_db > -100:
            inhale = make_inhale(int((breath_len or min(0.24, lead_in * 0.8)) * sr), sr, rng)
            inhale *= speech_rms * (10 ** (breath_db / 20)) / (np.sqrt(np.mean(inhale ** 2)) + 1e-9)
            pad[n - len(inhale):] = inhale
            breaths += 1
        pieces.append(pad)
    for idx, (a, b) in enumerate(intervals):
        gap = a - cursor
        if idx > 0 and gap / sr >= min_gap:
            dur = breath_len or min(0.22, max(0.14, gap / sr * 0.55))
            inhale = make_inhale(int(dur * sr), sr, rng)
            inhale *= speech_rms * (10 ** (breath_db / 20)) / (np.sqrt(np.mean(inhale ** 2)) + 1e-9)
            lead = max(0, gap - len(inhale))
            pieces.append(y[cursor:cursor + lead])
            pieces.append(inhale)
            cursor += lead + len(inhale)
            breaths += 1
        pieces.append(y[cursor:b])
        cursor = b
    pieces.append(y[cursor:])
    out = np.concatenate(pieces)

    if dynamics > 0:
        t = np.arange(len(out)) / sr
        drift = 1 + dynamics * np.sin(2 * np.pi * 0.55 * t + rng.uniform(0, 6.28)) \
                  + dynamics * 0.6 * np.sin(2 * np.pi * 1.35 * t + rng.uniform(0, 6.28))
        out = out * drift

    peaks = np.abs(out)
    if peaks.max() > 0:
        out = out * min(1.0, 0.89 / peaks.max())
    return out.astype(np.float32), {"breaths": breaths, "dur": round(len(out) / sr, 2)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("src")
    ap.add_argument("--out", required=True)
    ap.add_argument("--breath-db", type=float, default=-24.0,
                    help="inhale RMS relative to speech RMS (dB); -24 is subtle, -18 obvious")
    ap.add_argument("--dynamics", type=float, default=0.02, help="slow gain swing, 0.02 = +/-2%")
    ap.add_argument("--min-gap", type=float, default=0.18, help="only inhale before gaps this long (s)")
    ap.add_argument("--lead-in", type=float, default=0.0,
                    help="prepend this much silence and breathe into it (s); 0.25 reads as a person starting to speak")
    ap.add_argument("--breath-len", type=float, default=0.0,
                    help="fixed inhale length (s); 0 = derive from the gap (0.14-0.22)")
    ap.add_argument("--no-breath", action="store_true")
    args = ap.parse_args()

    import librosa
    import soundfile as sf

    y, sr = librosa.load(args.src, sr=24000, mono=True)
    out, info = humanize(y, sr, -120.0 if args.no_breath else args.breath_db,
                         args.dynamics, args.min_gap, lead_in=args.lead_in,
                         breath_len=args.breath_len)
    sf.write(args.out, out, sr)
    print(f"wrote {args.out}  {info}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
