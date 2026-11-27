#!/usr/bin/env python3
"""Package the chosen style for all three voices, plus a page to pick between them.

Two outputs:

  * ``assets/voice/final/`` — the audio the demo actually plays, one folder per
    voice, plus ``manifest.json`` describing the model, the voice ids and which
    file answers which line. The demo only has to read the manifest; nothing
    about the cloud voice ids needs to live in the app code.
  * a local page where the same line can be heard in 01 / B / 13 back to back,
    with a style switcher, because "which voice" is a taste call that has to be
    made by ear.

Audio is normalised to RMS -20 dBFS and 24 kHz for every voice, so switching
voices never switches loudness as well.

Usage:
  python tools/voice/export_final.py --src assets/voice/demos/v35 \
      --style rich_max --out ~/Desktop/试听-三音色最终版
"""

from __future__ import annotations

import argparse
import html
import json
import shutil
from pathlib import Path

VOICES = [
    ("p351", "01", "小卡", "cosyvoice-v3.5-plus-p351-c95cbb4bd14840fb99d2aa7e9df0d74b",
     "assets/voice/packs/01/ref-long.wav"),
    ("p35B", "B", "红红", "cosyvoice-v3.5-plus-p35b-55e40918db194576a56a2d89fd8f5746",
     "assets/voice/packs/B/ref.wav"),
    ("v35p13", "13", "小U", "cosyvoice-v3.5-plus-v35p13-333d99fcdb53405098bf69498d032e3c",
     "assets/voice/packs/13/ref-long.wav"),
]
STYLES = [("scene_think", "想好再说"), ("rich", "丰富"),
          ("rich_more", "更浓"), ("rich_max", "更浓+")]


def prep(src: Path, dst: Path):
    import librosa
    import numpy as np
    import soundfile as sf

    y, sr = librosa.load(str(src), sr=None, mono=True)
    if sr != 24000:
        y = librosa.resample(y, orig_sr=sr, target_sr=24000)
        sr = 24000
    rms = float(np.sqrt(np.mean(y ** 2))) or 1.0
    y = y * (10 ** (-20 / 20) / rms)
    peak = float(np.abs(y).max())
    if peak > 0.89:
        y = y * (0.89 / peak)
    sf.write(str(dst), y, sr)
    return len(y) / sr


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, default=Path("assets/voice/demos/v35"))
    ap.add_argument("--style", default="rich_max")
    ap.add_argument("--final", type=Path, default=Path("assets/voice/final"))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--plan", type=Path,
                    default=Path("assets/voice/demos/sucaiB/lines-v2.json"))
    args = ap.parse_args()

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    items = list(plan["lines"]) + list(plan.get("alts", []))
    texts = [i["id"] for i in items]
    text = {i["id"]: i["text"] for i in items}

    out = args.out.expanduser()
    out.mkdir(parents=True, exist_ok=True)
    final = args.final
    final.mkdir(parents=True, exist_ok=True)

    # 1. 最终音频 + manifest
    clips: dict[str, dict[str, str]] = {}
    durs: dict[str, dict[str, float]] = {}
    for src_key, short, label, voice_id, ref in VOICES:
        d = final / short
        d.mkdir(exist_ok=True)
        for pid in texts:
            src = args.src / src_key / args.style / f"{pid}.wav"
            if not src.exists():
                raise SystemExit(f"缺文件：{src}")
            dst = d / f"{pid}.wav"
            durs.setdefault(short, {})[pid] = prep(src, dst)
            clips.setdefault(pid, {})[short] = f"{short}/{pid}.wav"
        print(f">> {label}: {len(texts)} 条 -> {d}")

    manifest = {
        "model": "cosyvoice-v3.5-plus",
        "style": args.style,
        "default_voice": "B",
        "note": "同一个模型、三种复刻音色；音频已统一到 24 kHz、RMS -20 dBFS。"
                "前端按 voices 列出可选项，按 lines 里的 files 取文件。",
        "voices": [{"key": s, "label": lab, "voice_id": vid,
                    "reference": ref, "dir": s}
                   for _, s, lab, vid, ref in VOICES],
        "lines": [{"id": pid, "text": text[pid], "files": clips[pid]} for pid in texts],
    }
    (final / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    # 2. 试听页（三种音色 × 四种浓淡）
    for src_key, short, *_ in VOICES:
        d = out / "音频" / short
        d.mkdir(parents=True, exist_ok=True)
        for style, _ in STYLES:
            for pid in texts:
                src = args.src / src_key / style / f"{pid}.wav"
                if src.exists():
                    prep(src, d / f"{pid}-{style}.wav")

    rows = []
    for pid in texts:
        cells = "".join(
            f"<td><button data-i='{pid}' data-v='{s}'>{html.escape(lab)}</button></td>"
            for _, s, lab, *_ in VOICES)
        rows.append(f"<tr><th class='ln'>{pid}<span class='sub'>{html.escape(text[pid])}</span></th>"
                    f"{cells}<td><button class='cmp' data-i='{pid}'>三音色</button></td></tr>")

    head_cells = "".join(f"<th>{html.escape(lab)}</th>" for _, _, lab, *_ in VOICES)
    style_btns = "".join(
        f"<button class='st{' on' if k == 0 else ''}' data-st='{s}'>{html.escape(lab)}</button>"
        for k, (s, lab) in enumerate(STYLES))
    page = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>三音色最终版</title>
<style>
 :root {{ color-scheme: light dark; --fg:#1b1b1f; --mut:#6b6b76; --line:#d8d8e0; --hi:#8a4a9e; }}
 @media (prefers-color-scheme: dark) {{ :root {{ --fg:#e8e8ec; --mut:#9a9aa6; --line:#33333c; --hi:#c48ad8; }} }}
 body {{ font:15px/1.6 -apple-system,"PingFang SC",sans-serif; color:var(--fg);
   margin:40px auto; max-width:1000px; padding:0 20px; }}
 h1 {{ font-size:22px; font-weight:600; margin:0 0 4px; }}
 p.sub {{ color:var(--mut); margin:0 0 16px; }}
 .bar {{ margin:0 0 22px; }}
 table {{ border-collapse:collapse; width:100%; }}
 th,td {{ border-bottom:1px solid var(--line); padding:7px 4px; font-weight:400; }}
 thead th {{ font-size:13px; color:var(--mut); text-align:center; }}
 th.ln {{ text-align:left; padding:10px 12px 10px 0; width:46%; }}
 th.ln .sub {{ display:block; font-size:12px; color:var(--mut); }}
 td {{ text-align:center; }}
 button {{ font:inherit; font-size:13px; padding:5px 10px; margin:1px; border-radius:7px;
   cursor:pointer; border:1px solid var(--line); background:transparent; color:var(--fg); }}
 button:hover,button.on {{ border-color:var(--hi); color:var(--hi); }}
 button.cmp {{ font-size:12px; color:var(--mut); }}
</style>
<h1>三个音色 · 每条台词都能换</h1>
<p class="sub">同一个模型（cosyvoice-v3.5-plus）、三份复刻音色。选一个浓淡，然后同一句话在
01 / B / 13 上挨个听；「对比」会把三个版本连续放一遍。音频统一响度与采样率，换音色不会顺带换音量。</p>
<div class="bar">浓淡：{style_btns}</div>
<table><thead><tr><th class="ln">台词</th>
{head_cells}<th>对比</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<script>
let cur=null, style='scene_think';
function stop(){{ if(cur){{cur.pause();cur=null;}}
  document.querySelectorAll('button.on').forEach(b=>b.classList.remove('on')); }}
function play(src,btn){{ return new Promise(res=>{{
  if(!src) return res();
  const a=new Audio(encodeURI(src)); cur=a; if(btn) btn.classList.add('on');
  a.onended=()=>{{ if(btn) btn.classList.remove('on'); res(); }};
  a.onerror=res; a.play().catch(res); }}); }}
const url=(v,i,s)=>'音频/'+v+'/'+i+'-'+(s||style)+'.wav';
document.querySelectorAll('button.st').forEach(b=>b.onclick=()=>{{
  stop(); document.querySelectorAll('button.st').forEach(x=>x.classList.remove('on'));
  b.classList.add('on'); style=b.dataset.st; }});
document.querySelectorAll('button[data-v]').forEach(b=>b.onclick=()=>{{
  stop(); play(url(b.dataset.v,b.dataset.i),b); }});
document.querySelectorAll('button.cmp').forEach(b=>b.onclick=async()=>{{
  stop(); b.classList.add('on');
  for(const v of ['01','B','13']) await play(url(v,b.dataset.i),null);
  b.classList.remove('on'); }});
</script>
</html>"""
    (out / "index.html").write_text(page, encoding="utf-8")

    md = ["# 三音色最终版 · 时长\n", "| 台词 | 01 | B | 13 |", "|---|---|---|---|"]
    for pid in texts:
        md.append(f"| {pid} {text[pid]} | " + " | ".join(
            f"{durs[s][pid]:.2f}s" for _, s, *_ in VOICES) + " |")
    (out / "时长.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"-> {out}/index.html\n-> {final}/manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
