#!/usr/bin/env python3
"""Export the whole spoken dialogue to one flat folder, with the text in a manifest.

IndexTTS output already has sensible phrasing, so the only processing applied is
the pause repair: any comma pause longer than 0.45 s is cut back to 0.34 s. No
EQ — this engine is not short of presence the way the Qwen one was.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from repair_flow import tighten  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lines", required=True, type=Path, help="the text file used for generation")
    ap.add_argument("--dir", required=True, type=Path, help="generated wav directory")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--gap", type=float, default=0.34)
    args = ap.parse_args()

    import librosa
    import soundfile as sf

    texts = [ln.strip() for ln in args.lines.read_text(encoding="utf-8").splitlines() if ln.strip()]
    wavs = sorted(args.dir.glob("*.wav"))
    if not wavs:
        raise SystemExit(f"no wav in {args.dir}")
    if len(wavs) != len(texts):
        print(f"warning: {len(wavs)} wav vs {len(texts)} lines", file=sys.stderr)

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.wav"):
        old.unlink()

    rows = []
    for i, wav in enumerate(wavs):
        text = texts[i] if i < len(texts) else wav.stem
        name = f"{i + 1:02d}.wav"
        y, sr = librosa.load(str(wav), sr=None, mono=True)
        fixed, info = tighten(y, sr, args.gap, 0.45, 0.25, 0.60)
        sf.write(str(out / name), fixed, sr)
        rows.append((name, text, info["dur_in"], info["dur_out"], info["cuts"]))

    lines = ["# 演示台词 · IndexTTS-2.5（喜 0.45 + 静 0.35）\n",
             "参考音：素材B `ref-B.wav`。停顿超过 0.45 秒的已剪到 0.34 秒，其余未改动。\n",
             "| 文件 | 台词 | 原长 | 处理后 | 剪了几处 |", "|---|---|---|---|---|"]
    for name, text, a, b, cuts in rows:
        lines.append(f"| `{name}` | {text} | {a}s | {b}s | {cuts} |")
    (out / "清单.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"exported {len(rows)} lines to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
