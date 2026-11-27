#!/usr/bin/env python3
"""Measure how far a clone drifted from its reference in *timbre*, not pitch.

Pitch-locking fixed the centre frequency but four voices still sound like
somebody else, which means the drift is in the spectral envelope — the formants
that carry vocal identity. So this measures, per clip:

  * F1/F2/F3 medians (Praat Burg), reported as the relative error in percent;
  * LTAS distance over six bands, in dB;
  * MFCC-DTW distance to the reference (time alignment first, then compare).

A clip whose numbers are close to the reference and still sounds wrong would
mean the metric is not capturing it — worth knowing either way.

Usage:
  python tools/voice/timbre_check.py --ref packs/08/ref.wav take.wav ...
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path


def clean(path: Path, dst: Path) -> Path:
    import librosa
    import soundfile as sf

    y, sr = librosa.load(str(path), sr=16000, mono=True)
    sf.write(str(dst), y, 16000, subtype="PCM_16")
    return dst


def formants(path: Path, tmp: Path):
    import numpy as np
    import parselmouth
    from parselmouth.praat import call

    snd = parselmouth.Sound(str(clean(path, tmp / "f.wav")))
    fm = call(snd, "To Formant (burg)", 0.0, 5, 5500, 0.025, 50)
    vals = {1: [], 2: [], 3: []}
    n = int(call(fm, "Get number of frames"))
    for i in range(1, n + 1):
        for k in vals:
            v = call(fm, "Get value at time", k, call(fm, "Get time from frame number", i), "hertz", "linear")
            if v and v == v and 100 < v < 5000:
                vals[k].append(v)
    return [float(np.median(vals[k])) if vals[k] else 0.0 for k in (1, 2, 3)]


def ltas(y, sr):
    import librosa
    import numpy as np

    S = np.abs(librosa.stft(y, n_fft=1024, hop_length=256))
    power = (S ** 2).mean(axis=1)
    freqs = librosa.fft_frequencies(sr=sr, n_fft=1024)
    bands = [(80, 300), (300, 800), (800, 1500), (1500, 3000), (3000, 5000), (5000, 8000)]
    out = []
    for lo, hi in bands:
        m = (freqs >= lo) & (freqs < hi)
        out.append(10 * np.log10(power[m].sum() + 1e-12))
    return np.array(out)


def mfcc_dtw(y1, y2, sr=16000):
    import librosa
    import numpy as np

    m1 = librosa.feature.mfcc(y=y1, sr=sr, n_mfcc=20, n_fft=1024, hop_length=256)
    m2 = librosa.feature.mfcc(y=y2, sr=sr, n_mfcc=20, n_fft=1024, hop_length=256)
    m1 = (m1 - m1.mean(1, keepdims=True)) / (m1.std(1, keepdims=True) + 1e-6)
    m2 = (m2 - m2.mean(1, keepdims=True)) / (m2.std(1, keepdims=True) + 1e-6)
    _, wp = librosa.sequence.dtw(X=m1, Y=m2, metric="euclidean")
    d = np.mean([np.linalg.norm(m1[:, i] - m2[:, j]) for i, j in wp[::-1]])
    return float(d)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ref", required=True, type=Path)
    ap.add_argument("files", nargs="+", type=Path)
    args = ap.parse_args()

    import librosa
    import numpy as np

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        yr, sr = librosa.load(str(args.ref), sr=16000, mono=True)
        rf = formants(args.ref, tmp)
        rl = ltas(yr, sr)
        print(f"参考 {args.ref.name}: F1/F2/F3 = {rf[0]:.0f}/{rf[1]:.0f}/{rf[2]:.0f} Hz")
        print(f"{'文件':<16}{'F1Δ%':>7}{'F2Δ%':>7}{'F3Δ%':>7}{'LTAS dB':>9}{'MFCC-DTW':>10}")
        for f in args.files:
            y, _ = librosa.load(str(f), sr=16000, mono=True)
            fo = formants(f, tmp)
            lo = ltas(y, sr)
            d1 = [abs(fo[i] - rf[i]) / rf[i] * 100 if rf[i] else 0 for i in range(3)]
            ld = float(np.sqrt(np.mean((lo - rl) ** 2)))
            print(f"{f.name:<16}{d1[0]:>7.1f}{d1[1]:>7.1f}{d1[2]:>7.1f}"
                  f"{ld:>9.2f}{mfcc_dtw(yr, y):>10.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
