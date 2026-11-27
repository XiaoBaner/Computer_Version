#!/usr/bin/env python3
"""Check WAV samples before a TTS blind test.

Usage:
    python3 tools/voice/analyze_audio.py assets/voice/samples
    python3 tools/voice/analyze_audio.py a.wav b.wav --expect-sr 24000

Only 16-bit PCM WAV is inspected. Use afinfo/ffprobe for other formats.
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


def db(value: float) -> float:
    return 20.0 * math.log10(value) if value > 0 else float("-inf")


def analyze(path: Path, expect_sr: int | None, expect_mono: bool, max_silence: float) -> dict:
    try:
        with wave.open(str(path), "rb") as wav:
            sample_rate = wav.getframerate()
            channels = wav.getnchannels()
            width = wav.getsampwidth()
            frames = wav.getnframes()
            raw = wav.readframes(frames)
    except (wave.Error, OSError) as exc:
        return {"path": path, "error": str(exc)}

    if width != 2:
        return {"path": path, "error": f"only 16-bit PCM is supported, got {width * 8}-bit"}

    duration = frames / sample_rate if sample_rate else 0.0

    samples = array("h")
    samples.frombytes(raw)
    if sys.byteorder == "big":
        samples.byteswap()

    peak_int = max((abs(sample) for sample in samples), default=0)
    sum_squares = sum(sample * sample for sample in samples)
    peak = peak_int / 32768.0
    rms = math.sqrt(sum_squares / len(samples)) / 32768.0 if samples else 0.0

    silence_threshold = 32768 * (10 ** (-40 / 20))
    silent = sum(1 for sample in samples if abs(sample) < silence_threshold)
    silence_ratio = silent / len(samples) if samples else 0.0
    clipping = sum(1 for sample in samples if abs(sample) >= 32767)

    problems: list[str] = []
    if expect_sr and sample_rate != expect_sr:
        problems.append(f"sr={sample_rate}")
    if expect_mono and channels != 1:
        problems.append(f"ch={channels}")
    # 0.892 is -0.99 dBFS; allow a sample mastered exactly at -1 dBFS.
    if peak > 0.892:
        problems.append("peak>-1dBFS")
    if clipping:
        problems.append(f"clip={clipping}")
    if rms < (10 ** (-30 / 20)):
        problems.append("rms<-30dBFS")
    if silence_ratio > max_silence:
        problems.append(f"silence>{max_silence:.0%}")
    if duration < 1.0:
        problems.append("short<1s")

    return {
        "path": path,
        "sr": sample_rate,
        "ch": channels,
        "duration": duration,
        "peak_db": db(peak),
        "rms_db": db(rms),
        "clipping": clipping,
        "silence": silence_ratio,
        "problems": problems,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect 16-bit PCM WAV files for TTS QA.")
    parser.add_argument("paths", nargs="+", type=Path, help="WAV files or directories")
    parser.add_argument("--expect-sr", type=int, default=24000, help="expected sample rate (0 to skip)")
    parser.add_argument("--allow-stereo", action="store_true", help="do not require mono")
    parser.add_argument("--max-silence", type=float, default=0.65, help="warn above this silence ratio")
    args = parser.parse_args()

    files = list(iter_wavs(args.paths))
    if not files:
        print("no .wav files found", file=sys.stderr)
        return 2

    print(f"{'file':52} {'sr':>6} {'ch':>2} {'dur':>7} {'peak':>8} {'rms':>8} {'clip':>5} {'sil%':>5}  verdict")
    failures = 0
    for path in files:
        result = analyze(path, args.expect_sr or None, not args.allow_stereo, args.max_silence)
        if "error" in result:
            print(f"{str(path):52}  ERROR: {result['error']}")
            failures += 1
            continue
        verdict = "OK" if not result["problems"] else "CHECK: " + ",".join(result["problems"])
        if result["problems"]:
            failures += 1
        print(
            f"{str(path):52} {result['sr']:6d} {result['ch']:2d} "
            f"{result['duration']:6.2f}s {result['peak_db']:7.1f} {result['rms_db']:7.1f} "
            f"{result['clipping']:5d} {result['silence'] * 100:5.1f}  {verdict}"
        )

    print(f"\n{len(files) - failures}/{len(files)} file(s) pass")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
