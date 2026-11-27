#!/usr/bin/env python3
"""Turn a `design_search.py` report.json into a ranked audition page.

Shows, for every candidate, the audio next to the numbers it scored on, so you
can hear *and* see how close it got to the target spec. The target is a row of
numbers — pitch, brightness, breathiness, rhythm — never a recording.

Example:
    python3 tools/voice/design_report_page.py --dir assets/voice/demos/design-spec07
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

# spec key -> character folder it was measured from (by clip count, see 总览.txt)
SPEC_NAMES = {
    "01": "阿尔托莉雅·卡斯特 (118)",
    "02": "久远寺有珠 (152)",
    "03": "摩根 (81)",
    "04": "苍崎青子 (149)",
    "05": "光之高扬斯卡娅 (132)",
    "06": "暗之高扬斯卡娅 (146)",
    "07": "梅柳齐娜·Lancer (140)",
    "08": "梅柳齐娜·Ruler (144)",
    "09": "盖提亚 (25)",
    "10": "所罗门 (9)",
    "11": "所罗门·Pretender (68)",
}

FIELDS = [
    ("f0_mean", "音高 F0", "Hz", 0),
    ("f0_sd", "音高起伏", "Hz", 0),
    ("centroid", "明亮度", "Hz", 0),
    ("flatness", "气声", "", 3),
    ("pause_per_min", "停顿/分", "", 0),
    ("phrase_len", "句长", "s", 2),
]


def fmt(value, digits: int) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}" if digits else f"{value:.0f}"


def delta(cell, target, digits: int) -> str:
    if cell is None or not target:
        return ""
    if digits:
        return f"{cell - target:+.{digits}f}"
    return f"{cell - target:+.0f}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", required=True, type=Path)
    ap.add_argument("--title", default="")
    args = ap.parse_args()

    report = json.loads((args.dir / "report.json").read_text(encoding="utf-8"))
    target = report["target"]
    key = report.get("spec_key", "")
    rows = sorted(report["candidates"], key=lambda r: r["score"])
    best = rows[0]["score"] if rows else 0.0

    title = args.title or f"按规格设计的候选（目标：{SPEC_NAMES.get(key, key)}）"

    head = "".join(
        f'<th>{html.escape(label)}<br><span class="dim">目标 {fmt(target.get(name), d)}'
        f"{unit}</span></th>"
        for name, label, unit, d in FIELDS
    )

    cards = []
    for rank, row in enumerate(rows, 1):
        cells = []
        for name, _label, _unit, d in FIELDS:
            value = row.get(name)
            dev = delta(value, target.get(name), d)
            dev_html = f'<div class="dim">{html.escape(dev)}</div>' if dev else ""
            cells.append(f"<td>{fmt(value, d)}{dev_html}</td>")
        cards.append(
            f"""<tr>
  <td class="rank">{rank}<div class="dim">{row['score']:.3f}</div></td>
  <td><audio controls preload="none" src="{html.escape(row['file'])}"></audio>
      <div class="dim">{row['id']} · {row.get('dur', 0):.1f}s · 生成 {row.get('gen_s', 0):.0f}s</div></td>
  <td class="prompt">{html.escape(row['prompt'])}</td>
  {''.join(cells)}
</tr>"""
        )

    page = f"""<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ margin: 0 auto; max-width: 1280px; padding: 30px 20px 60px;
         font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
  h1 {{ font-size: 24px; margin: 0 0 8px; }}
  .lede {{ color: #666; margin: 0 0 6px; }}
  .note {{ border-left: 3px solid #d09a3a; padding: 8px 12px; margin: 16px 0;
          background: rgba(208,154,58,.08); }}
  .text {{ font-size: 16px; margin: 14px 0; }}
  table {{ width: 100%; border-collapse: collapse; margin: 14px 0; }}
  th, td {{ border: 1px solid #d8d8d8; padding: 9px 10px; vertical-align: top;
           font-size: 13px; }}
  th {{ text-align: left; font-weight: 600; font-size: 12px; }}
  audio {{ width: 100%; min-width: 180px; }}
  .rank {{ font-size: 20px; font-weight: 700; text-align: center; }}
  .prompt {{ min-width: 210px; color: #333; }}
  .dim {{ color: #888; font-size: 11px; font-weight: 400; }}
  tr.best {{ background: rgba(80,170,110,.10); }}
  @media (prefers-color-scheme: dark) {{
    .lede, .dim {{ color: #999; }}
    th, td {{ border-color: #444; }}
    .prompt {{ color: #ddd; }}
  }}
</style>
<body>
<h1>{html.escape(title)}</h1>
<p class="lede">同一个规格、八条不同描述、同一段文本。排序 = 与目标数字的加权距离，越小越接近。</p>
<div class="note">规格只记录音高、明亮度、气声、节奏这几组数字，由这些数字反推描述再生成，没有输入任何人的录音。</div>
<p class="text"><strong>文本：</strong>{html.escape(report.get('text', ''))}</p>
<table>
  <thead><tr><th>排名<br><span class="dim">距离</span></th><th>试听</th><th>描述</th>{head}</tr></thead>
  <tbody>{''.join(cards)}</tbody>
</table>
</body>
</html>
"""
    out = args.dir / "index.html"
    out.write_text(page, encoding="utf-8")
    print(f"wrote {out} ({len(rows)} candidates, best {best:.3f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
