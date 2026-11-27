#!/usr/bin/env python3
"""Reshape a clip's *prosody* while leaving its timbre alone.

The clone backend cannot be told "say it warmer" — Qwen3-TTS-Base takes a
reference and a text, nothing else, and the kit that wrapped it only ever
attached an emotion *label* to the file name. So the expressive part has to
come from somewhere else. Sampling gives you a spread of deliveries (pick the
one that already sounds like it cares); this script moves the delivery itself.

Praat's overlap-add resynthesis splits the clip into pitch marks and a spectral
envelope, so changing F0 and duration does not change the voice — only how it
is said. Three knobs, each isolated so a listener can hear one thing at a time:

  --range    widen or narrow the pitch range around the clip's own median.
             1.0 = untouched, 1.4 = noticeably more animated, 2.0 = theatrical.
  --tempo    <1 slows the whole line down (0.9 = 10% slower). Slower reads as
             softer/caring until it starts to drag.
  --final    lengthen the tail of each phrase (1.0 = off, 1.3 = a held ending),
             which is what turns a flat list of syllables into a sentence that
             means something.

Usage:
  python3 tools/voice/prosody.py in.wav --out out.wav --range 1.5 --final 1.25
"""

from __future__ import annotations

import argparse
from pathlib import Path


def _phrases(sound, min_silence: float = 0.12, floor_db: float = -35.0):
    """Voiced stretches as (start, end), so 'the last syllable' means something."""
    from parselmouth.praat import call

    tg = call(sound, "To TextGrid (silences)", 100, 0.0, floor_db,
              min_silence, 0.1, "silent", "sounding")
    n = int(call(tg, "Get number of intervals", 1))
    out = []
    for i in range(1, n + 1):
        label = call(tg, "Get label of interval", 1, i)
        if label == "sounding":
            out.append((call(tg, "Get start time of interval", 1, i),
                        call(tg, "Get end time of interval", 1, i)))
    return out


def reshape(src: Path, dst: Path, rng: float, tempo: float, final: float,
            final_ms: float = 220.0, fmin: float = 75.0, fmax: float = 600.0,
            decline: float = 0.0, jitter: float = 0.0, dt: float = 0.0025) -> dict:
    import numpy as np
    import parselmouth
    import soundfile as sf
    from parselmouth.praat import call

    snd = parselmouth.Sound(str(src))
    info: dict = {"dur_in": round(snd.duration, 3)}

    pitch = call(snd, "To Pitch", 0.0, fmin, fmax)
    vals = pitch.selected_array["frequency"]
    voiced = vals[vals > 0]
    if voiced.size < 10:
        raise SystemExit(f"{src}: too little voiced speech to reshape")
    med = float(np.median(voiced))

    # A 10 ms pitch grid quantises away the small period-to-period wobble a real
    # voice has; resynthesis at that grid measured 1.9 dB worse HNR than the
    # source. 2.5 ms is transparent (7.63 vs 7.69 dB on a known clip).
    manip = call(snd, "To Manipulation", dt, fmin, fmax)

    if abs(rng - 1.0) > 1e-6 or abs(decline) > 1e-6 or jitter > 0:
        pt = call(manip, "Extract pitch tier")
        # scale log-distance from the median: 1.0 is identity, >1 exaggerates.
        formula = f"{med:.4f} * (self/{med:.4f})^{rng:.4f}"
        if abs(decline) > 1e-6:
            # A sentence that starts slightly higher and lands lower reads as
            # meant, rather than recited. Linear in time, in semitones.
            d = snd.duration
            formula += f" * 2^((({decline/2:.4f}) - ({decline:.4f} * x / {d:.4f})) / 12)"
        if jitter > 0:
            # Her voice never holds an exact frequency. Three incommensurate slow
            # sines sum to something that drifts like a living larynx instead of
            # stepping like a quantiser — and, unlike random noise, stays smooth.
            formula += (f" * (1 + {jitter:.5f}*sin(2*pi*5.7*x + 0.3)"
                        f" + {jitter*0.55:.5f}*sin(2*pi*9.1*x + 1.9)"
                        f" + {jitter*0.35:.5f}*sin(2*pi*2.3*x + 3.7))")
        call(pt, "Formula", formula)
        call([manip, pt], "Replace pitch tier")

    if abs(tempo - 1.0) > 1e-6 or final != 1.0:
        dur = call("Create DurationTier", "dur", 0.0, snd.duration + 1.0)
        call(dur, "Add point", 0.0, 1.0 / tempo)  # Praat: relative rate, so invert
        if final != 1.0:
            for a, b in _phrases(snd):
                start = max(a, b - final_ms / 1000.0)
                # ramp into the hold instead of a step, or the seam is audible
                for k in range(6):
                    t = start + (b - start) * k / 5.0
                    call(dur, "Add point", t, (1.0 + (final - 1.0) * k / 5.0) / tempo)
        call([manip, dur], "Replace duration tier")

    res = call(manip, "Get resynthesis (overlap-add)")
    y = res.values[0] if res.values.ndim > 1 else res.values
    y = np.asarray(y, dtype=np.float32)
    peak = float(np.abs(y).max()) or 1.0
    y = y * (0.89 / peak) if peak > 0.89 else y
    sf.write(str(dst), y, int(res.sampling_frequency))

    info.update({"range": rng, "tempo": tempo, "final": final,
                 "median_f0": round(med, 1), "dur_out": round(len(y) / res.sampling_frequency, 3)})
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("src", type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--range", type=float, default=1.0, help="pitch range around median (1.0 = off)")
    ap.add_argument("--tempo", type=float, default=1.0, help="speed; 0.9 = 10%% slower")
    ap.add_argument("--final", type=float, default=1.0, help="tail lengthening of each phrase")
    ap.add_argument("--final-ms", type=float, default=220.0, help="how much tail to stretch")
    ap.add_argument("--decline", type=float, default=0.0,
                    help="total pitch drop across the clip, in semitones (3 = sentence lands lower)")
    ap.add_argument("--jitter", type=float, default=0.0,
                    help="fractional micro-wobble of F0, 0.006 = subtle, 0.015 = clearly unsteady")
    ap.add_argument("--dt", type=float, default=0.0025,
                    help="pitch grid (s); keep <=0.0025 or resynthesis dulls the voice")
    args = ap.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    info = reshape(args.src, args.out, args.range, args.tempo, args.final, args.final_ms,
                   decline=args.decline, jitter=args.jitter, dt=args.dt)
    print(f"wrote {args.out}  {info}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
