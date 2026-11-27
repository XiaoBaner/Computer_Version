#!/usr/bin/env python3
"""Recompute every candidate's numbers from its WAV, then rescore.

Measurements can be wrong for reasons that have nothing to do with the audio:
brightness was first measured full-band against 64 kbps mp3 targets, which the
codec band-limits, so every candidate scored as "far too bright". Re-reading the
generated WAVs is cheap and needs no GPU, so fix the ruler and redo the numbers
instead of defending them.

Usage:
  python3 tools/voice/remeasure.py \
    --dir assets/voice/demos/design-batch \
    --spec assets/voice/analysis/fgo-style-spec.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from design_search import distance, measure  # noqa: E402


def write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", required=True, type=Path)
    ap.add_argument("--spec", required=True, type=Path)
    ap.add_argument("--rate", type=int, default=24000)
    args = ap.parse_args()

    import librosa
    import numpy as np

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    touched = 0
    for report_path in sorted(args.dir.glob("*/report.json")):
        key = report_path.parent.name
        if key not in spec:
            print(f"{key}: not in spec, skipped")
            continue
        report = json.loads(report_path.read_text(encoding="utf-8"))
        target = spec[key]["summary"]
        rows = []
        for row in report.get("candidates", []):
            wav = report_path.parent / row["file"]
            if not wav.exists():
                print(f"{key}/{row['file']}: missing, dropped")
                continue
            y, sr = librosa.load(str(wav), sr=args.rate, mono=True)
            m = measure(y, sr, librosa, np)
            m.update({"id": row["id"], "prompt": row["prompt"],
                      "gen_s": row.get("gen_s"), "file": row["file"],
                      "score": distance(m, target)})
            rows.append(m)
        rows.sort(key=lambda r: r["id"])
        report.update({"target": target, "candidates": rows})
        write_json(report_path, report)
        touched += 1
        if rows:
            best = min(rows, key=lambda r: r["score"])
            print(f"{key}: {len(rows)} rows, best {best['id']} score {best['score']:.3f} "
                  f"(F0 {best['f0_mean']:.0f} vs {target['f0_mean']:.0f}, "
                  f"cen {best['centroid']:.0f} vs {target['centroid']:.0f})")
    print(f"re-measured {touched} profiles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
