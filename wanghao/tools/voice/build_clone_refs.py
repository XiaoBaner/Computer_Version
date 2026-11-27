#!/usr/bin/env python3
"""Compose zero-shot clone references from a transcribed corpus.

A 3-second reference locks timbre but leaves the clone little prosody to copy.
Joining several sentences of the same speaker, same style, with a short pause
between them gives the clone prompt more to work with, and the reference text
stays exact because every clip already has its own transcript.

Each spec is `<name>=<clip_id>[+<clip_id>...]`; clips come from a JSONL whose
`audio` fields point at 24 kHz wavs and whose `text` holds the transcript.

Example:
  python3 tools/voice/build_clone_refs.py \
    --jsonl assets/voice/data/amitaro-yofukashi/train_raw.jsonl \
    --out-dir assets/voice/refs/amitaro/clone-refs \
    --spec warm=SETSUZOKU_047+SETSUZOKU_030 \
    --spec long=AISATSU_009+SETSUZOKU_042+QUESTION2_070
"""

from __future__ import annotations

import argparse
import json
import wave
from pathlib import Path

import librosa
import numpy as np


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jsonl", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--spec", action="append", required=True,
                    help="name=CLIP_ID[+CLIP_ID...]")
    ap.add_argument("--gap-s", type=float, default=0.18, help="silence between joined clips")
    args = ap.parse_args()

    clips: dict[str, tuple[Path, str]] = {}
    for line in args.jsonl.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        clips[Path(row["audio"]).stem] = (Path(row["audio"]), row["text"])

    args.out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for spec in args.spec:
        name, _, ids = spec.partition("=")
        if not ids:
            print(f"bad spec: {spec}")
            return 2
        parts, texts = [], []
        for cid in ids.split("+"):
            if cid not in clips:
                print(f"  ! {name}: unknown clip {cid}")
                break
            path, text = clips[cid]
            y, _ = librosa.load(str(path), sr=24000, mono=True)
            parts.append(y)
            texts.append(text)
        else:
            gap = np.zeros(int(args.gap_s * 24000), dtype=np.float32)
            joined = np.concatenate([np.concatenate([p, gap]) for p in parts])
            peak = float(np.max(np.abs(joined))) or 1.0
            pcm = np.clip(joined / peak * 0.95, -1.0, 1.0)
            dest = args.out_dir / f"{name}.wav"
            with wave.open(str(dest), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(24000)
                w.writeframes((pcm * 32767).astype("<i2").tobytes())
            dur = len(joined) / 24000
            ref_text = "".join(texts)
            manifest.append({"name": name, "path": str(dest), "seconds": round(dur, 2),
                             "clips": ids.split("+"), "ref_text": ref_text})
            print(f"  {name:10} {dur:5.2f}s  {ids}")
            print(f"             {ref_text}")

    (args.out_dir / "refs.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {args.out_dir}/refs.json ({len(manifest)} refs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
