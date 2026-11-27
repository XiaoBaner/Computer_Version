#!/usr/bin/env python3
"""Turn a transcribed single-speaker corpus zip into Qwen3-TTS fine-tune JSONL.

Why this exists: `finetuning/dataset.py` in QwenLM/Qwen3-TTS asserts
`sr == 24000`, wants mono 16-bit wav, one speaker, 3-15 s per clip, and exact
text per clip. Corpora ship at 44.1/48 kHz with the text in a separate list.
This script bridges the two and refuses to guess: a clip whose text cannot be
found is dropped and reported, never written with a placeholder.

Text lookup, in order:
  1. an ID -> text table (`AISATSU_001:おはようございます。`)
  2. else the file stem itself, when it looks like a sentence

Usage:
  python3 tools/voice/build_corpus_jsonl.py \
    --zip assets/voice/refs/amitaro/MANAcorpus_amitaro_yofukashi.zip \
    --text assets/voice/refs/amitaro/meta/mana-corpus.txt \
    --prefer 44.1k \
    --out-dir assets/voice/data/amitaro-yofukashi \
    --out-jsonl assets/voice/data/amitaro-yofukashi/train_raw.jsonl
"""

from __future__ import annotations

import argparse
import io
import json
import re
import wave
import zipfile
from pathlib import Path

def decode_name(info: zipfile.ZipInfo) -> str:
    """Zip entries written on Japanese Windows use cp932 without the UTF-8 flag."""
    if info.flag_bits & 0x800:
        return info.filename
    raw = info.filename.encode("cp437", "replace")
    for enc in ("utf-8", "cp932"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return info.filename


def load_text_table(path: Path) -> dict[str, str]:
    table: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, text = line.partition(":")
        table[key.strip()] = text.strip()
    return table


def looks_like_sentence(stem: str) -> bool:
    """Japanese/CJK text with no path separators and no obvious id pattern."""
    if re.fullmatch(r"[A-Za-z0-9_\-]+", stem):
        return False
    return bool(re.search(r"[぀-ヿ一-鿿　-〿＀-￯]", stem))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--zip", required=True, type=Path)
    ap.add_argument("--text", type=Path, help="ID:text table, one per line")
    ap.add_argument("--prefer", default="44.1k",
                    help="prefer entries whose path contains this token (rate folder)")
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--out-jsonl", required=True, type=Path)
    ap.add_argument("--ref-audio", type=Path, help="reference clip; default: longest kept clip")
    ap.add_argument("--min-s", type=float, default=3.0)
    ap.add_argument("--max-s", type=float, default=15.0)
    ap.add_argument("--target-sr", type=int, default=24000)
    ap.add_argument("--limit", type=int, default=0, help="debug: stop after N kept clips")
    args = ap.parse_args()

    import librosa
    import numpy as np

    table = load_text_table(args.text) if args.text else {}

    zf = zipfile.ZipFile(args.zip)
    by_stem: dict[str, list[tuple[str, zipfile.ZipInfo]]] = {}
    for info in zf.infolist():
        if info.is_dir() or not info.filename.lower().endswith(".wav"):
            continue
        name = decode_name(info)
        stem = Path(name).stem
        by_stem.setdefault(stem, []).append((name, info))

    picked: list[tuple[str, str, zipfile.ZipInfo]] = []
    conflicts = 0
    for stem, options in by_stem.items():
        if len(options) > 1:
            preferred = [o for o in options if args.prefer and args.prefer in o[0]]
            if preferred:
                options = preferred
            else:
                conflicts += 1
            options = options[:1]
        name, info = options[0]
        text = table.get(stem)
        if text is None and looks_like_sentence(stem):
            text = stem
        if not text:
            continue
        picked.append((stem, text, info))

    picked.sort(key=lambda x: x[0])
    args.out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, str]] = []
    too_short = too_long = unreadable = 0
    for stem, text, info in picked:
        if args.limit and len(rows) >= args.limit:
            break
        try:
            y, sr = librosa.load(io.BytesIO(zf.read(info)), sr=args.target_sr, mono=True)
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"  ! {stem}: unreadable ({exc})")
            unreadable += 1
            continue
        dur = len(y) / args.target_sr
        if dur < args.min_s:
            too_short += 1
            continue
        if dur > args.max_s:
            too_long += 1
            continue
        peak = float(np.max(np.abs(y))) or 1.0
        pcm = np.clip(y / peak * 0.95, -1.0, 1.0)
        dest = args.out_dir / f"{stem}.wav"
        with wave.open(str(dest), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(args.target_sr)
            w.writeframes((pcm * 32767).astype("<i2").tobytes())
        rows.append({"audio": str(dest.resolve()), "text": text, "dur": f"{dur:.2f}"})

    if not rows:
        print("nothing kept — check --text / --min-s / --prefer")
        return 2

    ref = args.ref_audio or Path(rows[len(rows) // 2]["audio"])
    for row in rows:
        row["ref_audio"] = str(ref.resolve())
    args.out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with args.out_jsonl.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps({"audio": row["audio"], "text": row["text"],
                                 "ref_audio": row["ref_audio"]}, ensure_ascii=False) + "\n")

    total_min = sum(float(r["dur"]) for r in rows) / 60
    print(json.dumps({
        "kept": len(rows),
        "minutes": round(total_min, 1),
        "dropped_short": too_short,
        "dropped_long": too_long,
        "unreadable": unreadable,
        "missing_text": len(by_stem) - len(picked),
        "multi_rate_conflicts": conflicts,
        "ref_audio": str(ref),
        "jsonl": str(args.out_jsonl),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
