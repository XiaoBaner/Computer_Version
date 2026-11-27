#!/usr/bin/env python3
"""Build a named (not blind) audition page from `<speaker>-<style>.wav` files.

Complements make_blind_test.py: blind pages hide candidate names for scoring,
this one shows them for tuning, so you can hear what a style prompt actually did
to a known voice.

Example:
    python3 tools/voice/make_audition_page.py \
      --dir assets/voice/demos/qwen3tts \
      --title "Qwen3-TTS 本地音色试听"
"""

from __future__ import annotations

import argparse
import html
import json
import wave
from pathlib import Path

STYLE_LABELS = {
    "ref": "真人参考音",
    "normal": "正常",
    "neutral": "自然",
    "gentle": "温柔",
    "practice": "示范",
    "check_in": "关心确认",
    "slow": "慢速",
    "emotion": "情绪版",
    "emotion_hot": "情绪版·高温",
    "designed": "设计音色",
    "new": "新台词",
    "text": "只改文本",
    "instruct": "去造作",
    "scene": "潜台词",
    "v2text": "口语整段",
    "soft": "柔和",
    "smile": "带笑",
    "bright": "明亮",
    "native": "模型原生",
    "fast12": "变速 1.12×",
    "fast22": "变速 1.22×",
    "checkin": "关心确认",
    "yofukashi": "夜更かし·电台",
    "runrun": "るんるん·雀跃",
    "punsuka": "ぷんすか·假装生气",
    "sasayakiA": "ささやきA·耳语",
    "sasayakiB": "ささやきB·耳语本色",
}


def wav_seconds(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as wav:
            rate = wav.getframerate()
            return wav.getnframes() / rate if rate else None
    except (wave.Error, OSError):
        return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate an audition page with visible voice names.")
    parser.add_argument("--dir", required=True, type=Path, help="directory with <speaker>-<style>.wav")
    parser.add_argument("--title", default="音色试听")
    parser.add_argument("--note", default="")
    parser.add_argument("--out", type=Path, help="output html (default: <dir>/index.html)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    files = sorted(args.dir.glob("*-*.wav"))
    if not files:
        print("no <speaker>-<style>.wav files found")
        return 2

    speakers: dict[str, dict[str, Path]] = {}
    styles: list[str] = []
    for path in files:
        speaker, _, style = path.stem.partition("-")
        if not speaker or not style:
            continue
        speakers.setdefault(speaker, {})[style] = path
        if style not in styles:
            styles.append(style)
    styles.sort(key=lambda s: list(STYLE_LABELS).index(s) if s in STYLE_LABELS else 99)

    meta = {}
    meta_path = args.dir / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            meta = {}
    text = meta.get("text", "")
    texts_by_style = meta.get("texts_by_style") or {}

    header = "".join(f"<th>{html.escape(STYLE_LABELS.get(s, s))}</th>" for s in styles)
    rows = []
    for speaker, by_style in speakers.items():
        cells = []
        for style in styles:
            path = by_style.get(style)
            if not path:
                cells.append("<td>—</td>")
                continue
            secs = wav_seconds(path)
            dur = f'<span class="params">{secs:.1f}s</span>' if secs else ""
            cells.append(
                f'<td><audio controls preload="none" src="{html.escape(path.name)}"></audio>'
                f"{dur}</td>"
            )
        rows.append(
            f"<tr><td class=\"voice\"><strong>{html.escape(speaker)}</strong></td>{''.join(cells)}</tr>"
        )

    note_html = f'<div class="note">{html.escape(args.note)}</div>' if args.note else ""
    if texts_by_style:
        lines = "<br>".join(
            f"{html.escape(STYLE_LABELS.get(k, k))}：{html.escape(v)}"
            for k, v in texts_by_style.items()
        )
        text_html = f"<p><strong>文本：</strong><br>{lines}</p>"
    elif text:
        text_html = f"<p><strong>文本：</strong>{html.escape(text)}</p>"
    else:
        text_html = ""
    page = f"""<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(args.title)}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ margin: 0 auto; max-width: 1180px; padding: 30px 20px 60px;
         font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
  h1 {{ font-size: 25px; margin: 0 0 8px; }}
  .lede {{ color: #666; margin: 0 0 18px; }}
  .note {{ border-left: 3px solid #d09a3a; padding: 8px 12px; margin: 18px 0;
          background: rgba(208,154,58,.08); }}
  table {{ width: 100%; border-collapse: collapse; margin: 14px 0; }}
  th, td {{ border: 1px solid #d8d8d8; padding: 10px; vertical-align: top; }}
  th {{ text-align: left; font-weight: 600; }}
  audio {{ width: 100%; min-width: 200px; }}
  .params {{ color: #666; font-size: 12px; }}
  @media (prefers-color-scheme: dark) {{
    .lede, .params {{ color: #aaa; }}
    th, td {{ border-color: #444; }}
  }}
</style>
<body>
<h1>{html.escape(args.title)}</h1>
<p class="lede">同一段文本、同一组采样参数，逐行横向比较。同一副耳机，音量固定。</p>
{note_html}
{text_html}
<table>
  <thead><tr><th>音色</th>{header}</tr></thead>
  <tbody>{''.join(rows)}</tbody>
</table>
</body>
</html>
"""
    out = args.out or (args.dir / "index.html")
    out.write_text(page, encoding="utf-8")
    print(f"wrote {out} ({len(speakers)} speakers x {len(styles)} styles)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
