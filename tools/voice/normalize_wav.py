#!/usr/bin/env python3
"""Normalize 16-bit PCM WAV files to a target RMS level for fair A/B listening.

This is not LUFS; it is a lightweight RMS/peak normalization that needs only
the Python standard library. For delivery loudness, use ffmpeg loudnorm or
pyloudnorm as described in docs/voice-tuning.md.

Usage:
    python3 tools/voice/normalize_wav.py assets/voice/demos/edge --out assets/voice/demos/edge/normalized
"""

from __future__ import annotations

import argparse
import math
import sys
import wave
from array import array
from pathlib import Path


def iter_wavs(paths: list[Path]):
    for path in paths:
        if path.is_dir():
            yield from sorted(path.rglob("*.wav"))
        elif path.suffix.lower() == ".wav":
            yield path


def normalize(source: Path, target: Path, target_rms_db: float, peak_db: float) -> float:
    with wave.open(str(source), "rb") as wav:
        params = wav.getparams()
        raw = wav.readframes(wav.getnframes())

    if params.sampwidth != 2:
        raise ValueError(f"{source}: only 16-bit PCM is supported")

    samples = array("h")
    samples.frombytes(raw)
    if sys.byteorder == "big":
        samples.byteswap()
    if not samples:
        raise ValueError(f"{source}: empty audio")

    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples)) / 32768.0
    if rms <= 0:
        raise ValueError(f"{source}: silent audio")

    gain = 10 ** ((target_rms_db - 20 * math.log10(rms)) / 20)
    peak_limit = 32768 * (10 ** (peak_db / 20))
    peak = max(abs(sample) for sample in samples)
    if peak * gain > peak_limit:
        gain = peak_limit / peak

    scaled = array(
        "h",
        (max(-32768, min(32767, int(round(sample * gain)))) for sample in samples),
    )
    if sys.byteorder == "big":
        scaled.byteswap()

    target.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(target), "wb") as wav:
        wav.setparams(params)
        wav.writeframes(scaled.tobytes())

    return 20 * math.log10(gain)


def main() -> int:
    parser = argparse.ArgumentParser(description="RMS-normalize 16-bit PCM WAV files.")
    parser.add_argument("paths", nargs="+", type=Path, help="WAV files or directories")
    parser.add_argument("--out", required=True, type=Path, help="output directory")
    parser.add_argument("--target-rms-db", type=float, default=-20.0, help="target RMS in dBFS")
    parser.add_argument("--peak-db", type=float, default=-1.0, help="peak ceiling in dBFS")
    parser.add_argument("--force", action="store_true", help="allow overwriting existing output files")
    args = parser.parse_args()

    files = list(iter_wavs(args.paths))
    if not files:
        print("no .wav files found", file=sys.stderr)
        return 2

    failures = 0
    for source in files:
        target = args.out / source.name
        if target.exists() and not args.force:
            print(f"skip existing {target} (use --force)")
            continue
        try:
            gain_db = normalize(source, target, args.target_rms_db, args.peak_db)
            print(f"{source} -> {target} ({gain_db:+.1f} dB)")
        except (ValueError, wave.Error, OSError) as exc:
            print(f"FAIL {source}: {exc}", file=sys.stderr)
            failures += 1

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
