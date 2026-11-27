#!/usr/bin/env python3
"""Score how *natural* a generated clip sounds, as opposed to how well it matches a spec.

Fitting a numeric profile and sounding like a person are different things: a
candidate can land 300.2 Hz against a 296 Hz target and still be unusable because
its opening 嗯 is an aspirated sigh rather than a voiced hum. Listening picked
that out immediately; the spec score ranked it first.

So this measures the things listeners actually reacted to, and says so with
components rather than one opaque number:

  onset     the first syllable's noise: spectral flatness and zero-crossing rate
            (a voiced 嗯 is smooth; an aspirated one reads as a sigh or a pant)
  voice     harmonic-to-noise ratio over the whole clip
  flow      whether the sentence is chopped into stubs instead of phrased
  pitch     how often pitch spikes well above the clip's own median
  level     clipping and dead-silent stretches

Total is 0-1, higher is more natural. It is a *screen*, not a judge: on the 40
anchor clips it does not separate the five a human liked perfectly, so treat it
as "these are worth listening to", not "this is the best one".

Usage:
  python3 tools/voice/naturalness.py assets/voice/demos/listen-anchor/*.wav
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _clip(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def assess(path: Path, sr_target: int = 24000) -> dict:
    import librosa
    import numpy as np

    y, sr = librosa.load(str(path), sr=sr_target, mono=True)
    intervals = librosa.effects.split(y, top_db=35, frame_length=1024, hop_length=256)
    if len(intervals) == 0:
        return {"total": 0.0, "error": "no speech"}

    onset = y[intervals[0][0]: intervals[0][1]]
    flat_on = float(np.mean(librosa.feature.spectral_flatness(y=onset)))
    zcr_on = float(np.mean(librosa.feature.zero_crossing_rate(onset)))

    harm, perc = librosa.effects.hpss(y)
    hnr_db = 10 * np.log10(max(np.sum(harm ** 2), 1e-9) / max(np.sum(perc ** 2), 1e-9))

    segs = [(b - a) / sr for a, b in intervals]
    mean_seg = float(np.mean(segs))

    f0, _, _ = librosa.pyin(y, fmin=60, fmax=600, sr=sr, frame_length=1024, hop_length=256)
    voiced = f0[~np.isnan(f0)]
    med = float(np.median(voiced)) if voiced.size else 0.0
    spike = float(np.mean(voiced > max(420.0, med * 1.55))) if voiced.size else 0.0

    peak = float(np.max(np.abs(y)))
    silent = float(np.mean(np.abs(y) < 1e-4))

    parts = {
        "onset": 1 - _clip(flat_on / 0.06) * 0.6 - _clip(zcr_on / 0.25) * 0.4,
        "voice": _clip((hnr_db + 3) / 15),
        "flow": _clip((mean_seg - 0.25) / 0.55),
        "pitch": 1 - _clip(spike / 0.25),
        "level": (1 - _clip((peak - 0.98) / 0.02)) * (1 - _clip((silent - 0.45) / 0.2)),
    }
    # Calibrated against five clips a human picked out of forty. Whole-clip HNR got
    # the lowest weight on purpose: the clip they liked most is one of the breathiest
    # in the set (HNR -4.8 dB). Breathiness itself is fine — an *aspirated onset* is
    # what they heard as a sigh, so the first syllable carries the weight.
    weights = {"onset": 0.50, "voice": 0.10, "flow": 0.20, "pitch": 0.15, "level": 0.05}
    total = sum(parts[k] * weights[k] for k in parts)
    return {
        "total": round(float(total), 3),
        **{k: round(float(v), 3) for k, v in parts.items()},
        "flat_onset": round(flat_on, 4), "zcr_onset": round(zcr_on, 4),
        "hnr_db": round(float(hnr_db), 2), "mean_seg": round(mean_seg, 2),
        "median_f0": round(med), "spike_frac": round(spike, 3),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--json-out", type=Path)
    ap.add_argument("--top", type=int, default=0, help="only show the top N")
    args = ap.parse_args()

    rows = []
    for p in args.files:
        r = assess(p)
        r["file"] = p.name
        rows.append(r)
    rows.sort(key=lambda r: r["total"], reverse=True)
    if args.top:
        rows = rows[: args.top]

    print(f"{'file':<20}{'总分':>6}{'起音':>6}{'音质':>6}{'断句':>6}{'音高':>6}{'HNR':>7}{'句长':>6}")
    for r in rows:
        print(f"{r['file']:<20}{r['total']:6.3f}{r['onset']:6.2f}{r['voice']:6.2f}"
              f"{r['flow']:6.2f}{r['pitch']:6.2f}{r['hnr_db']:7.1f}{r['mean_seg']:6.2f}")
    if args.json_out:
        args.json_out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
