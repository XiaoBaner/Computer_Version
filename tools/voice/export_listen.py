#!/usr/bin/env python3
"""Copy candidates into one flat folder for listening, with an ordered manifest.

Flat because that is how people actually listen: open the folder in Finder, hit
space, arrow down. Nested `02/c03.wav` paths mean clicking into directories
between every sample, and the names carry no information once they are out of
context.

Files land as `<key>-<uid>.wav` — the same ids the HTML page shows — so a note
like "02-c04 太尖" maps back to a row without guessing.

Usage:
  python3 tools/voice/export_listen.py \
    --dir assets/voice/demos/design-batch --dir assets/voice/demos/design-batch2 \
    --out assets/voice/demos/listen
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from design_batch_page import collect  # noqa: E402
from design_search import pitch_ok, rank_key  # noqa: E402

HEADER = """# 试听清单

文本统一是：**嗯，我在。你不用急着说清楚。**

每一行：编号 · 角色 · 文件名 · 实测音高 / 目标音高 · 实测明亮度 / 目标明亮度 · ✓否

✓ 的意思是音高落在目标 ±12% 以内。✗ 的不代表难听，只是不是这个角色的音区。
挑的时候按听感来，数字只是参考——同一个编号在页面上也能听到。

按组听，每组从第 1 行开始，那是这一组数字上最接近的一个。
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", required=True, action="append", type=Path, dest="dirs")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--zip", action="store_true", help="also write <out>.zip")
    args = ap.parse_args()

    merged, skipped = collect(args.dirs)
    if skipped:
        print(f"skipped {len(skipped)} report(s): {', '.join(skipped)}")
    merged = {k: v for k, v in merged.items() if v["rows"]}
    if not merged:
        print("nothing to export")
        return 2

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.wav"):
        old.unlink()

    lines = [HEADER]
    total = 0
    for key in sorted(merged):
        entry = merged[key]
        target = entry["target"]
        rows = sorted(entry["rows"], key=lambda r: rank_key(r, target))
        lines.append(f"\n## {key} · {entry['label']}（{entry['gender']}）\n")
        lines.append("| 文件 | 音高 | 目标 | 明亮度 | 目标 | | 描述 |")
        lines.append("|---|---|---|---|---|---|---|")
        for rank, row in enumerate(rows, 1):
            # "anchor04" is a spec key, not something to type into a player. Prefix with A
            # so these do not collide with the earlier `04-r1-c03.wav` from another round.
            short = key.replace("anchor", "A") or key
            name = f"{short}-{row['uid']}.wav"
            shutil.copy2(row["wav"], out / name)
            ok = "✓" if pitch_ok(row, target) else "✗"
            lines.append(
                f"| `{name}` | {row['f0_mean']:.0f} | {target['f0_mean']:.0f} "
                f"| {row['centroid']:.0f} | {target['centroid']:.0f} | {ok} "
                f"| {row['prompt']} |"
            )
            total += 1

    (out / "清单.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "README.txt").write_text(
        "试听方法：在 Finder 里打开这个文件夹，选中任意一个 wav 按空格键，\n"
        "然后用 ↓ 逐个往下听。文件名前缀是角色编号，跟 清单.md 和网页一一对应。\n"
        f"共 {total} 个文件。\n", encoding="utf-8")

    if args.zip:
        shutil.make_archive(str(out), "zip", root_dir=out.parent, base_dir=out.name)
        print(f"wrote {out}.zip")

    print(f"exported {total} clips to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
