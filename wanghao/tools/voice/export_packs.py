#!/usr/bin/env python3
"""Turn rendered voice packs into one audition page you can actually compare.

Comparing eleven voices one folder at a time is how you end up judging the
first three and guessing at the rest. So the output is a single page: one row
per voice, one column per line, click a cell to hear *that sentence in that
voice*. Rows are sorted by measured pitch so the spread (bright to low) is
visible before you play anything.

Also writes, per pack, a continuous playback file, and the usual pause
tightening (samples only, 0.34 s comma pauses) that the rest of the kit uses.

Usage:
  python tools/voice/export_packs.py \
    --dir assets/voice/demos/packs --out ~/Desktop/试听-音色包-全部
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def stats(y, sr):
    import librosa
    import numpy as np

    f0 = librosa.yin(y, fmin=70, fmax=500, sr=sr, frame_length=2048)
    f0 = f0[np.isfinite(f0) & (f0 > 0)]
    iv = librosa.effects.split(y, top_db=30)
    voiced = sum(b - a for a, b in iv) / sr
    return (float(np.median(f0)) if f0.size else 0.0, voiced, len(y) / sr)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--plan", type=Path,
                    default=Path("assets/voice/demos/sucaiB/lines-v2.json"))
    ap.add_argument("--gap", type=float, default=0.34)
    args = ap.parse_args()

    import librosa
    import numpy as np
    import soundfile as sf

    from repair_flow import tighten

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    items = list(plan.get("lines", [])) + list(plan.get("alts", []))
    text = {i["id"]: i["text"] for i in items}
    order = [i["id"] for i in items]

    packs = sorted(p for p in args.dir.iterdir() if p.is_dir())
    out = args.out.expanduser()
    out.mkdir(parents=True, exist_ok=True)

    rows = []
    for pack in packs:
        dst = out / pack.name
        dst.mkdir(exist_ok=True)
        reel, rates = [], []
        got = []
        for pid in order:
            src = pack / f"{pid}.wav"
            if not src.exists():
                continue
            y, sr = librosa.load(str(src), sr=None, mono=True)
            fixed, _ = tighten(y, sr, args.gap, 0.45, 0.25, 0.60)
            # IndexTTS speaks at 22.05 kHz; the kit spec is 24 kHz, and comparing
            # voices at different loudness is comparing two things at once.
            if sr != 24000:
                fixed = librosa.resample(fixed, orig_sr=sr, target_sr=24000)
                sr = 24000
            rms = float(np.sqrt(np.mean(fixed ** 2))) or 1.0
            fixed = fixed * (10 ** (-20 / 20) / rms)
            peak = float(np.abs(fixed).max())
            if peak > 0.89:
                fixed = fixed * (0.89 / peak)
            sf.write(str(dst / f"{pid}.wav"), fixed, sr)
            _, voiced, _ = stats(fixed, sr)
            chars = len([c for c in text[pid] if c not in "，。？！、；："])
            rates.append(chars / max(voiced, 1e-6))
            got.append(pid)
            reel.append(fixed)
            reel.append(np.zeros(int(0.55 * sr), dtype=np.float32))
        if not got:
            continue
        sf.write(str(dst / "00-连播全部.wav"), np.concatenate(reel[:-1]), sr)
        # One number per pack: median pitch across its whole line set.
        allf0 = []
        for pid in got:
            y, sr = librosa.load(str(dst / f"{pid}.wav"), sr=None, mono=True)
            f0 = librosa.yin(y, fmin=70, fmax=500, sr=sr, frame_length=2048)
            allf0.extend(f0[np.isfinite(f0) & (f0 > 0)].tolist())
        rows.append({"pack": pack.name, "ids": got,
                     "f0": float(np.median(allf0)) if allf0 else 0.0,
                     "rate": float(np.mean(rates)) if rates else 0.0})

    # Sorted by pitch: the spread is the story, the listener picks the row.
    rows.sort(key=lambda r: r["f0"])

    def esc(s: str) -> str:
        return html.escape(s, quote=True)

    cells_head = "".join(
        f"<th title='{esc(text[i])}'>{esc(i)}</th>" for i in order)
    body = []
    for r in rows:
        tds = []
        for pid in order:
            if pid in r["ids"]:
                tds.append(f"<td><button data-p='{esc(r['pack'])}' data-i='{esc(pid)}' "
                           f"title='{esc(text[pid])}'>{esc(pid)}</button></td>")
            else:
                tds.append("<td class='na'>–</td>")
        body.append(
            f"<tr><th class='pk'>{esc(r['pack'])}<span class='sub'>{r['f0']:.0f} Hz · "
            f"{r['rate']:.1f} 字/秒</span>"
            f"<button class='all' data-pack='{esc(r['pack'])}' title='连播'>▶ 全部</button></th>"
            f"{''.join(tds)}</tr>")

    legend = "".join(f"<li><b>{esc(i)}</b> {esc(text[i])}</li>" for i in order)
    page = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>音色包横向试听</title>
<style>
 :root {{ color-scheme: light dark; --fg:#1b1b1f; --mut:#6b6b76; --line:#d8d8e0; --hot:#b4432e; }}
 @media (prefers-color-scheme: dark) {{ :root {{ --fg:#e8e8ec; --mut:#9a9aa6; --line:#33333c; --hot:#ff8a6a; }} }}
 body {{ font: 15px/1.6 -apple-system, "PingFang SC", "Noto Sans SC", sans-serif; color:var(--fg);
        margin: 40px auto; max-width: 1080px; padding: 0 20px; }}
 h1 {{ font-size: 22px; font-weight: 600; margin: 0 0 4px; }}
 p.sub {{ color: var(--mut); margin: 0 0 28px; }}
 table {{ border-collapse: collapse; width: 100%; }}
 th, td {{ border-bottom: 1px solid var(--line); padding: 6px 4px; font-weight: 400; }}
 th {{ font-size: 13px; color: var(--mut); text-align: center; }}
 th.pk {{ text-align: left; font-size: 15px; color: var(--fg); padding: 14px 12px 14px 0;
         white-space: nowrap; }}
 th.pk .sub {{ display:block; font-size: 12px; color: var(--mut); }}
 button {{ font: inherit; padding: 6px 10px; border-radius: 8px; cursor: pointer;
          border: 1px solid var(--line); background: transparent; color: var(--fg); }}
 button:hover {{ border-color: var(--hot); color: var(--hot); }}
 button.playing {{ background: var(--hot); border-color: var(--hot); color: #fff; }}
 button.all {{ margin-top: 6px; font-size: 12px; padding: 3px 8px; }}
 td {{ text-align: center; }}
 td.na {{ color: var(--line); }}
 ul {{ color: var(--mut); font-size: 13px; columns: 2; margin-top: 28px; }}
 li {{ break-inside: avoid; }}
</style>
<h1>音色包横向试听</h1>
<p class="sub">点格子听「这一句 + 这个音色」。每行按音高排序，下面是每一句的台词。
停顿已剪到 0.34 秒，其余未改动。</p>
<table>
<thead><tr><th class="pk">音色</th>{cells_head}</tr></thead>
<tbody>{''.join(body)}</tbody>
</table>
<ul>{legend}</ul>
<script>
const reel = {json.dumps({r['pack']: f"{r['pack']}/00-连播全部.wav" for r in rows}, ensure_ascii=False)};
function play(src, btn) {{
  if (window.cur) {{ window.cur.pause(); }}
  document.querySelectorAll('button.playing').forEach(b => b.classList.remove('playing'));
  const a = new Audio(encodeURI(src));
  window.cur = a;
  if (btn) {{ btn.classList.add('playing'); }}
  a.onended = () => {{ if (btn) btn.classList.remove('playing'); }};
  a.play();
}}
document.querySelectorAll('td button').forEach(b => {{
  b.onclick = () => play(b.dataset.p + '/' + b.dataset.i + '.wav', b);
}});
document.querySelectorAll('button.all').forEach(b => {{
  b.onclick = () => play(reel[b.dataset.pack], b);
}});
</script>
</html>"""
    (out / "index.html").write_text(page, encoding="utf-8")
    print(f"{len(rows)} 个音色 -> {out}/index.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
