#!/usr/bin/env python3
"""Measure how "one syllable at a time" a clip sounds, against real human speech.

The complaint "一个字一个字的" is not about speed — our takes already run at
5–6 characters per sounding second, which is normal conversational Chinese. It is
about *articulation*: whether the energy dips between syllables are deep and
regular, the way a reading voice separates every character, instead of the shallow
blurred transitions of connected speech.

So: take the short-term RMS inside voiced regions and count the dips that fall
more than `--dip-db` below the local peak and then come back up. Report dips per
second of sounding time. Calibrate against real recordings — a number only means
something next to a human one.

Usage:
  python tools/voice/staccato.py a.wav b.wav ... [--dip-db 9] [--min-ms 40]
"""

from __future__ import annotations

import argparse

import numpy as np


def analyse(path: str, dip_db: float, min_ms: float, top_db: float):
    import librosa
    from scipy.signal import find_peaks

    y, sr = librosa.load(path, sr=None, mono=True)
    hop = 160  # 10 ms at 16 kHz; scaled below
    hop = max(64, int(sr * 0.010))
    rms = librosa.feature.rms(y=y, frame_length=hop * 2, hop_length=hop)[0]
    db = 20 * np.log10(np.maximum(rms, 1e-8))
    voiced = db > (db.max() - top_db)
    if voiced.sum() < 10:
        return None
    # local peak envelope: running max over ~200 ms
    win = max(3, int(0.20 / 0.010))
    peak = np.array([db[max(0, i - win):i + win].max() for i in range(len(db))])
    depth = peak - db
    min_len = max(2, int(min_ms / 10))
    dips, _ = find_peaks(
        np.where(voiced, depth, 0.0), height=dip_db, distance=min_len)
    voiced_s = voiced.sum() * 0.010
    total_s = len(y) / sr
    return {
        "file": path.split("/")[-1],
        "dur": total_s,
        "voiced": voiced_s,
        "dips": len(dips),
        "dips_per_s": len(dips) / max(voiced_s, 1e-6),
        "mean_depth": float(np.mean(depth[dips])) if len(dips) else 0.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--dip-db", type=float, default=9.0)
    ap.add_argument("--min-ms", type=float, default=40.0)
    ap.add_argument("--top-db", type=float, default=30.0)
    args = ap.parse_args()

    rows = [r for r in (analyse(f, args.dip_db, args.min_ms, args.top_db) for f in args.files) if r]
    rows.sort(key=lambda r: r["dips_per_s"])
    print(f"{'文件':<22}{'总长':>7}{'有声':>8}{'凹坑数':>8}{'每秒凹坑':>10}{'平均深度':>10}")
    for r in rows:
        print(f"{r['file']:<22}{r['dur']:>7.2f}{r['voiced']:>8.2f}{r['dips']:>8d}"
              f"{r['dips_per_s']:>10.2f}{r['mean_depth']:>10.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
