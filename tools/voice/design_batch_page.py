#!/usr/bin/env python3
"""One audition page for one or more `design_batch.py` runs.

Profiles are merged across runs, so a second round aimed at the miss from the
first sits next to it instead of in a separate page you have to cross-reference.
Candidates keep the run they came from in their id (`b1-c03`).

Each profile prints its measured target above the candidates, so you read how far
a candidate landed while you listen. Audio paths are relative to the output page.

Usage:
  python3 tools/voice/design_batch_page.py \
    --dir assets/voice/demos/design-batch --dir assets/voice/demos/design-batch2 \
    --out assets/voice/demos/design-all/index.html --top 4
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from design_search import pitch_ok, rank_key  # noqa: E402

FIELDS = [
    ("f0_mean", "音高", "Hz", 0),
    ("f0_sd", "起伏", "Hz", 0),
    ("centroid", "明亮度", "Hz", 0),
    ("flatness", "气声", "", 3),
    ("phrase_len", "句长", "s", 2),
    ("pause_per_min", "停顿/分", "", 0),
]


def fmt(value, digits: int) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}" if digits else f"{value:.0f}"


def collect(dirs: list[Path]) -> tuple[dict[str, dict], list[str]]:
    merged: dict[str, dict] = {}
    skipped = []
    for run_index, base in enumerate(dirs, 1):
        for d in sorted(p for p in base.iterdir() if p.is_dir()):
            report_path = d / "report.json"
            if not report_path.exists():
                continue
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
                target = report["target"]
            except (json.JSONDecodeError, OSError, KeyError) as exc:
                skipped.append(f"{d.name}: {type(exc).__name__}")
                continue
            key = report["spec_key"]
            entry = merged.setdefault(key, {
                "spec_key": key, "label": report.get("label", key),
                "gender": report.get("gender", ""), "target": target,
                "text": report.get("text", ""), "rows": [],
            })
            # dir names differ by a trailing digit, so their first two characters are
            # identical ("design-batch"/"design-batch2"); use the run's position instead
            prefix = f"r{run_index}"
            for row in report.get("candidates", []):
                wav = d / row["file"]
                if not wav.exists():
                    continue
                entry["rows"].append({**row, "uid": f"{prefix}-{row['id']}", "wav": wav})
    return merged, skipped


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", required=True, action="append", type=Path, dest="dirs")
    ap.add_argument("--out", type=Path, help="output html (default: <first dir>/index.html)")
    ap.add_argument("--top", type=int, default=4, help="candidates to show per profile")
    ap.add_argument("--title", default="按声学规格设计的候选音色")
    args = ap.parse_args()

    merged, skipped = collect(args.dirs)
    if skipped:
        print(f"skipped {len(skipped)} unreadable report(s): {', '.join(skipped)}")
    merged = {k: v for k, v in merged.items() if v["rows"]}
    if not merged:
        print("no report with any candidate yet")
        return 2

    out = args.out or (args.dirs[0] / "index.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    page_dir = out.parent

    profiles = [merged[k] for k in sorted(merged)]
    summary_rows, sections = [], []
    for entry in profiles:
        target = entry["target"]
        rows = sorted(entry["rows"], key=lambda r: rank_key(r, target))[: args.top]

        head = "".join(
            f'<th>{html.escape(label)}<br><span class="dim">{fmt(target.get(name), dd)}{unit}</span></th>'
            for name, label, unit, dd in FIELDS
        )
        cards = []
        for rank, row in enumerate(rows, 1):
            cells = []
            for name, _label, _unit, dd in FIELDS:
                value = row.get(name)
                tgt = target.get(name)
                dev = ""
                if value is not None and tgt:
                    dev = f"{value - tgt:+.{dd}f}" if dd else f"{value - tgt:+.0f}"
                cells.append(f'<td>{fmt(value, dd)}<div class="dim">{html.escape(dev)}</div></td>')
            src = os.path.relpath(row["wav"], page_dir)
            ok = pitch_ok(row, target)
            cards.append(
                f"""<tr{' class="best"' if rank == 1 and ok else ''}>
  <td class="rank">{rank}<div class="dim">{row['score']:.2f}</div>
      <div class="{'ok' if ok else 'bad'}">{'✓' if ok else '✗'}</div></td>
  <td><audio controls preload="none" src="{html.escape(src)}"></audio>
      <div class="dim">{html.escape(row['uid'])} · {row.get('dur', 0):.1f}s</div></td>
  <td class="prompt">{html.escape(row['prompt'])}</td>
  {''.join(cells)}
</tr>"""
            )
        sections.append(
            f"""<section>
<h2>{html.escape(entry['spec_key'])} · {html.escape(entry['label'])}
<span class="tag">{html.escape(entry['gender'])}</span></h2>
<table>
  <thead><tr><th>排名<br><span class="dim">距离</span></th><th>试听</th><th>描述</th>{head}</tr></thead>
  <tbody>{''.join(cards)}</tbody>
</table>
</section>"""
        )
        best = rows[0]
        mark = "✓" if pitch_ok(best, target) else "✗"
        summary_rows.append(
            f"<tr><td>{html.escape(entry['spec_key'])}</td>"
            f"<td>{html.escape(entry['label'])}</td>"
            f"<td>{html.escape(entry['gender'])}</td>"
            f"<td>{fmt(target.get('f0_mean'), 0)}</td>"
            f"<td>{fmt(best.get('f0_mean'), 0)} {mark}</td>"
            f"<td>{fmt(target.get('centroid'), 0)}</td>"
            f"<td>{fmt(best.get('centroid'), 0)}</td>"
            f"<td>{best['score']:.2f}</td>"
            f"<td>{html.escape(best['uid'])}</td></tr>"
        )

    text = profiles[0].get("text", "")
    page = f"""<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(args.title)}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ margin: 0 auto; max-width: 1320px; padding: 30px 20px 70px;
         font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
  h1 {{ font-size: 25px; margin: 0 0 8px; }}
  h2 {{ font-size: 18px; margin: 40px 0 6px; padding-top: 14px; border-top: 1px solid #ccc; }}
  .tag {{ font-size: 12px; font-weight: 400; color: #888; }}
  .lede {{ color: #666; margin: 0 0 8px; }}
  .note {{ border-left: 3px solid #d09a3a; padding: 8px 12px; margin: 16px 0;
          background: rgba(208,154,58,.08); }}
  .text {{ font-size: 16px; margin: 12px 0 18px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 12px 0; }}
  th, td {{ border: 1px solid #d8d8d8; padding: 8px 9px; vertical-align: top; font-size: 13px; }}
  th {{ text-align: left; font-weight: 600; font-size: 12px; }}
  audio {{ width: 100%; min-width: 170px; }}
  .rank {{ font-size: 18px; font-weight: 700; text-align: center; width: 56px; }}
  .prompt {{ min-width: 205px; color: #333; }}
  .dim {{ color: #888; font-size: 11px; font-weight: 400; }}
  .ok {{ color: #2e8b57; font-size: 11px; }}
  .bad {{ color: #c05a5a; font-size: 11px; }}
  tr.best {{ background: rgba(80,170,110,.10); }}
  @media (prefers-color-scheme: dark) {{
    .lede, .dim, .tag {{ color: #999; }}
    th, td {{ border-color: #444; }}
    .prompt {{ color: #ddd; }}
    h2 {{ border-color: #444; }}
  }}
</style>
<body>
<h1>{html.escape(args.title)}</h1>
<p class="lede">细字是实测基准，候选按接近程度排。✓ = 音高在目标 ±12% 内；✗ = 这一维不合格，后面数字再好也不算。</p>
<div class="note">基准只有测量统计量；描述由这些数字推出，模型只收到中文描述和文本，没有输入任何人的录音。
明亮度在 100–7000 Hz 内测量，避免合成音与录音在高频上的差异被误读成音色。</div>
<p class="text"><strong>文本：</strong>{html.escape(text)}</p>
<h2 style="border-top:none;margin-top:26px">总览 · 每组最优</h2>
<table>
  <thead><tr><th>#</th><th>角色</th><th>性别</th><th>目标音高</th><th>候选音高</th>
  <th>目标明亮度</th><th>候选明亮度</th><th>距离</th><th>候选</th></tr></thead>
  <tbody>{''.join(summary_rows)}</tbody>
</table>
{''.join(sections)}
</body>
</html>
"""
    out.write_text(page, encoding="utf-8")
    print(f"wrote {out} ({len(profiles)} profiles)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
