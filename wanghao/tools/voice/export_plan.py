#!/usr/bin/env python3
"""Export a per-line-emotion dialogue for listening, with the reasoning attached.

The interesting question with this set is not "is one clip good" but "do the
lines sound like *different* things to say". So the manifest prints each line's
emotion vector in words and in numbers, and the folder gets a continuous
playback file — the only honest way to hear whether eleven deliveries hold
together as one person having one conversation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

EMO_NAMES = ["喜", "怒", "哀", "惧", "厌", "郁", "惊", "静"]


def describe(vec: list[float]) -> str:
    parts = [f"{EMO_NAMES[i]}{v:.2f}" for i, v in enumerate(vec) if v and v > 0.001]
    return " · ".join(parts) if parts else "无"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plan", required=True, type=Path)
    ap.add_argument("--dir", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--gap", type=float, default=0.34)
    ap.add_argument("--room", type=float, default=-120.0, help="also write a room version at this dB")
    args = ap.parse_args()

    import librosa
    import numpy as np
    import soundfile as sf

    from polish import polish
    from repair_flow import tighten

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    items = list(plan.get("lines", []))
    alts = list(plan.get("alts", []))

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.wav"):
        old.unlink()

    rows, reel = [], []
    for item in items + alts:
        src = args.dir / f"{item['id']}.wav"
        if not src.exists():
            print(f"missing {src}", file=sys.stderr)
            continue
        y, sr = librosa.load(str(src), sr=None, mono=True)
        fixed, info = tighten(y, sr, args.gap, 0.45, 0.25, 0.60)
        sf.write(str(out / f"{item['id']}.wav"), fixed, sr)
        if args.room > -100:
            polish(out / f"{item['id']}.wav", out / f"{item['id']}-房间.wav", 0.0, args.room)
        rows.append((item, info["dur_in"], info["dur_out"]))
        if item in items:
            reel.append(fixed)
            reel.append(np.zeros(int(0.55 * sr), dtype=np.float32))

    ordered = [item for item in items if (out / f"{item['id']}.wav").exists()]
    if reel:
        sf.write(str(out / "00-连播全部.wav"), np.concatenate(reel), sr)

    md = ["# 每句一个情绪 · IndexTTS-2.5\n",
          f"参考音固定为素材B `ref-B.wav`；情绪向量顺序 {EMO_NAMES}。",
          "先听 `00-连播全部.wav`，判断十一句是不是像**同一个人在不同时候说的话**。\n",
          "| 文件 | 台词 | 情绪 | 语速 | 时长 | 为什么这么设 |",
          "|---|---|---|---|---|---|"]
    for item, a, b in rows:
        md.append(f"| `{item['id']}.wav` | {item['text']} | {describe(item['emo'])} "
                  f"| {item.get('speed', 1.0):.2f} | {b}s | {item.get('why', '')} |")
    (out / "清单.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"exported {len(rows)} clips to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
