#!/usr/bin/env python3
"""Shared measurement primitives.

The corpus clips are 64 kbps mp3, which band-limits them well below 12 kHz;
generated audio is full-band 24 kHz wav. A plain spectral centroid measured on
both is therefore not comparable — the codec alone drops it by several hundred
Hz. Measure brightness inside a band both can contain (100–7000 Hz) and the
number starts meaning the voice rather than the file format.
"""

from __future__ import annotations

BAND_LO = 100.0
BAND_HI = 7000.0


def band_centroid(y, sr, librosa, numpy, n_fft: int = 1024,
                  lo: float = BAND_LO, hi: float = BAND_HI) -> float:
    """Magnitude-weighted spectral centroid restricted to [lo, hi] Hz."""
    spec = numpy.abs(librosa.stft(y, n_fft=n_fft))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
    mask = (freqs >= lo) & (freqs <= hi)
    total = spec[mask].sum()
    if total <= 0:
        return 0.0
    return float((freqs[mask][:, None] * spec[mask]).sum() / total)
