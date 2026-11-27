#!/usr/bin/env python3
"""Measure a voice's *style* as numbers, so it can be designed for rather than copied.

Listening to a recording and describing it ("low, warm, breathy, slow") is how
you borrow characteristics without reproducing the person. Numbers make those
adjectives checkable: pitch range, brightness, breathiness, rhythm. Feed the
resulting spec into VoiceDesign prompts and use the same script to score the
candidates, so selection stops being guesswork.

Reports, per folder:
  F0 mean / sd / p10 / p90   pitch level and how much it moves
  centroid                   brightness (Hz) — high = thin/bright, low = warm/dark
  flatness                   breathiness / noisiness (0–1) — high = airy or husky
  silence ratio, pause rate  rhythm: how much of the clip is pause, how often
  speech segment length      typical unbroken phrase length (s)

Usage:
  python3 tools/voice/voice_spec.py /Users/haowang/Desktop/training/01 /Users/haowang/Desktop/training/02
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

AUDIO_EXT = {".wav", ".mp3", ".ogg", ".flac", ".m4a"}


def analyze(path: Path, sr_target: int = 24000) -> dict | None:
    import librosa
    import numpy as np

    try:
        y, _ = librosa.load(str(path), sr=sr_target, mono=True)
    except Exception:  # noqa: BLE001 - skip unreadable files
        return None
    if y.size < sr_target * 0.3:
        return None

    f0, voiced_flag, _ = librosa.pyin(
        y, fmin=60, fmax=500, sr=sr_target, frame_length=1024, hop_length=256
    )
    voiced = f0[~np.isnan(f0)]
    if voiced.size < 10:
        return None

    from metrics import band_centroid

    centroid = band_centroid(y, sr_target, librosa, np)
    flatness = float(np.mean(librosa.feature.spectral_flatness(y=y)))

    intervals = librosa.effects.split(y, top_db=35, frame_length=1024, hop_length=256)
    seg = [(b - a) / sr_target for a, b in intervals]
    speech = sum(seg)
    n_pause = max(len(seg) - 1, 0)

    return {
        "file": path.name,
        "dur": round(len(y) / sr_target, 2),
        "f0_mean": round(float(voiced.mean()), 1),
        "f0_sd": round(float(voiced.std()), 1),
        "f0_p10": round(float(np.percentile(voiced, 10)), 1),
        "f0_p90": round(float(np.percentile(voiced, 90)), 1),
        "centroid": round(centroid),
        "flatness": round(flatness, 4),
        "silence_ratio": round(1 - speech / (len(y) / sr_target), 3),
        "pause_per_min": round(n_pause / (speech / 60), 1) if speech else 0,
        "phrase_len": round(statistics.mean(seg), 2) if seg else 0,
    }


def summarize(rows: list[dict]) -> dict:
    keys = ["dur", "f0_mean", "f0_sd", "centroid", "flatness", "silence_ratio", "pause_per_min", "phrase_len"]
    out = {"clips": len(rows)}
    for k in keys:
        vals = [r[k] for r in rows]
        out[k] = round(statistics.median(vals), 2)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("folders", nargs="+", type=Path)
    ap.add_argument("--json-out", type=Path, help="write per-clip rows + summary here")
    ap.add_argument("--limit", type=int, default=0, help="sample only the first N clips per folder")
    args = ap.parse_args()

    report = {}
    for folder in args.folders:
        files = sorted(p for p in folder.rglob("*") if p.suffix.lower() in AUDIO_EXT)
        if args.limit:
            files = files[: args.limit]
        rows = [r for f in files if (r := analyze(f))]
        if not rows:
            print(f"{folder.name}: no readable clips")
            continue
        s = summarize(rows)
        report[folder.name] = {"summary": s, "clips": rows}
        print(
            f"{folder.name:>4}  n={s['clips']:4d}  F0 {s['f0_mean']:5.1f}Hz (sd {s['f0_sd']:4.1f})  "
            f"centroid {s['centroid']:5.0f}Hz  flatness {s['flatness']:.3f}  "
            f"silence {s['silence_ratio']:.2f}  pauses/min {s['pause_per_min']:5.1f}  "
            f"phrase {s['phrase_len']:.2f}s"
        )

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
