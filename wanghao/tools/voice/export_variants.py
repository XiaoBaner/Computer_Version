#!/usr/bin/env python3
"""Put the style variants of one voice on one page, side by side per line.

Deliberately *no* pause tightening and no pitch locking here: the question is
whether an instruction makes the voice sound like a person, and both of those
post-processes change the thing being judged. Only loudness and sample rate are
unified, so switching a button does not also switch the volume.

Usage:
  python tools/voice/export_variants.py --src assets/voice/demos/variants \
      --out ~/Desktop/试听-云端更像人
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

LABELS = {"none": "原始", "oral": "口语", "oral_emo": "口语+情绪",
          "scene": "场景", "loose": "节奏松", "plain": "旧模板",
          "scene_line": "逐句场景", "scene_line_lock": "逐句·锁",
          "scene_think": "想好再说", "scene_think_lock": "想好·锁",
          "scene_air": "气声", "scene_warm": "笑意", "think_hen": "嗯·修正", "hen_nocomma": "嗯·去逗号", "rich": "丰富", "rich_more": "更浓", "rich_max": "更浓+"}
ORDER = ["plain", "none", "oral", "oral_emo", "scene", "loose",
         "scene_line", "scene_line_lock", "scene_think", "scene_think_lock",
         "think_hen", "hen_nocomma", "rich", "rich_more", "rich_max"]
BLURB = {
    "plain": "旧模板：像对朋友说话，不要念稿",
    "none": "不发任何指令，让模型自己发挥",
    "oral": "随口说、边说边想、别把每个字都咬清楚",
    "oral_emo": "口语 + 每句的情绪词",
    "scene": "刚下班靠沙发回消息的场景描述 + 情绪",
    "loose": "节奏随意、有时快有时慢、别那么均匀 + 情绪",
    "scene_line": "每句话单独写一个处境，而不是所有句子共用一段描述",
    "scene_line_lock": "同左，再用 PSOLA 把中位音高锁回参考音",
    "scene_think": "边说边想，中间轻轻停一下，像在斟酌怎么安慰对方",
    "scene_think_lock": "同左，再锁音高",
    "think_hen": "想好再说 + 交代开头那个「嗯」只是随口应一声、别拖长",
    "hen_nocomma": "想好再说 + 送进 TTS 的文本去掉「嗯」后面的逗号（显示不变）",
    "rich": "逐句写处境和具体情绪，去掉几乎每句都出现的「安静、温和」",
    "rich_more": "同一套词，情绪再明显一档，用来比较会不会过头",
    "rich_max": "只把关心和安抚那几句推到发紧、带鼻音，开场与应答仍然轻",
}


NAMES = {
    "01": "01 · 短参考（3 秒）",
    "long01": "01 · 长参考（11 秒）",
    "B": "B · 参考 7.5 秒",
    "13": "13 · 短参考（3 秒）",
    "long13": "13 · 长参考（10 秒）",
    "v3513": "13 · v3.5-flash",
    "v35p13": "13 · v3.5-plus",
    "p351": "01 · v3.5-plus（11 秒参考）",
    "p35B": "B · v3.5-plus（7.5 秒参考）",
}


# 13's 10 s reference produced byte-identical audio to its 3.4 s one, so listing
# it twice would just be two buttons playing the same file.
SKIP = {"long13"}


def prep(src: Path, dst: Path):
    import librosa
    import numpy as np
    import soundfile as sf

    y, sr = librosa.load(str(src), sr=None, mono=True)
    if sr != 24000:
        y = librosa.resample(y, orig_sr=sr, target_sr=24000)
        sr = 24000
    # /r/ is a 0.15 s tail so nothing is clipped by a player's fade-out.
    rms = float(np.sqrt(np.mean(y ** 2))) or 1.0
    y = y * (10 ** (-20 / 20) / rms)
    peak = float(np.abs(y).max())
    if peak > 0.89:
        y = y * (0.89 / peak)
    sf.write(str(dst), y, sr)
    return len(y) / sr


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, default=Path("assets/voice/demos/variants"))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--plan", type=Path,
                    default=Path("assets/voice/demos/sucaiB/lines-v2.json"))
    args = ap.parse_args()

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    items = {i["id"]: i["text"] for i in list(plan["lines"]) + list(plan.get("alts", []))}
    out = args.out.expanduser()
    out.mkdir(parents=True, exist_ok=True)

    sections, rows_md = [], []
    for label_dir in sorted(p for p in args.src.iterdir() if p.is_dir()):
        label = label_dir.name
        if label in SKIP:
            continue
        styles = [s for s in ORDER if (label_dir / s).is_dir()]
        styles += sorted(p.name for p in label_dir.iterdir()
                         if p.is_dir() and p.name not in styles)
        if not styles:
            continue
        ids = sorted({p.stem for s in styles for p in (label_dir / s).glob("*.wav")})
        d = out / label
        d.mkdir(exist_ok=True)
        durs: dict[str, dict[str, float]] = {}
        cells_rows = []
        for pid in ids:
            cells = []
            for s in styles:
                src = label_dir / s / f"{pid}.wav"
                if src.exists():
                    durs.setdefault(s, {})[pid] = prep(src, d / f"{pid}-{s}.wav")
                    cells.append(
                        f"<td><button data-p='{label}' data-i='{pid}' data-s='{s}'>"
                        f"{LABELS.get(s, s)}</button></td>")
                else:
                    cells.append("<td class='na'>–</td>")
            cells_rows.append(
                f"<tr><th class='ln'>{pid}<span class='sub'>{html.escape(items.get(pid, ''))}"
                f"</span></th>{''.join(cells)}</tr>")
        head = "".join(f"<th>{LABELS.get(s, s)}</th>" for s in styles)
        reel = "".join(
            f"<button class='reel' data-p='{label}' data-s='{s}'>连播 · {LABELS.get(s, s)}</button>"
            for s in styles)
        sections.append(
            f"<h2>{html.escape(NAMES.get(label, label))}</h2>"
            f"<table><thead><tr><th class='ln'>句子</th>"
            f"{head}</tr></thead><tbody>{''.join(cells_rows)}</tbody></table>"
            f"<p class='reelrow'>{reel}</p>")
        for s in styles:
            dd = durs.get(s, {})
            rows_md.append(f"| {label} | {LABELS.get(s, s)} | "
                           + " | ".join(f"{dd[i]:.2f}s" if i in dd else "–" for i in ids)
                           + f" | {sum(dd.values()):.2f}s |")

    used = {s.name for s in args.src.iterdir() if s.is_dir()
            for s in s.iterdir() if s.is_dir()}
    ids_all = sorted({p.stem for d in args.src.glob("*/*/") for p in d.glob("*.wav")}) \
        if any(args.src.glob("*/*/")) else []
    if rows_md:
        head = "| 音色 | 指令 | " + " | ".join(ids_all) + " | 总时长 |"
        rows_md[:0] = ["# 云端变体 · 时长\n", head,
                       "|" + "---|" * (len(ids_all) + 3)]
    legend = "".join(f"<li><b>{LABELS[k]}</b> {v}</li>"
                     for k, v in BLURB.items() if k in used)
    page = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>云端 · 更像人</title>
<style>
 :root {{ color-scheme: light dark; --fg:#1b1b1f; --mut:#6b6b76; --line:#d8d8e0; --hi:#8a4a9e; }}
 @media (prefers-color-scheme: dark) {{ :root {{ --fg:#e8e8ec; --mut:#9a9aa6;
   --line:#33333c; --hi:#c48ad8; }} }}
 body {{ font:15px/1.6 -apple-system,"PingFang SC","Noto Sans SC",sans-serif; color:var(--fg);
   margin:40px auto; max-width:1180px; padding:0 20px; }}
 h1 {{ font-size:22px; font-weight:600; margin:0 0 4px; }}
 h2 {{ font-size:17px; font-weight:600; margin:34px 0 8px; }}
 p.sub {{ color:var(--mut); margin:0 0 8px; }}
 table {{ border-collapse:collapse; width:100%; }}
 th,td {{ border-bottom:1px solid var(--line); padding:6px 3px; font-weight:400; }}
 thead th {{ font-size:13px; color:var(--mut); text-align:center; }}
 th.ln {{ text-align:left; padding:10px 12px 10px 0; width:34%; }}
 th.ln .sub {{ display:block; font-size:12px; color:var(--mut); }}
 td {{ text-align:center; }}
 td.na {{ color:var(--line); }}
 button {{ font:inherit; font-size:13px; padding:5px 9px; margin:1px; border-radius:7px;
   cursor:pointer; border:1px solid var(--line); background:transparent; color:var(--fg); }}
 button:hover,button.on {{ border-color:var(--hi); color:var(--hi); }}
 .reelrow {{ margin:6px 0 0; }}
 button.reel {{ font-size:12px; color:var(--mut); }}
 ul {{ color:var(--mut); font-size:13px; }}
</style>
<h1>云端 · 同一个音色，几种「说人话」的调法</h1>
<p class="sub">音色本身没动：同一段参考音、同一个克隆 id、同一个模型。变的是指令、
语速，以及参考音的长短。只统一了响度和采样率，<b>没有剪停顿、没有锁音高</b>，
因为要判断的就是节奏和自然度。</p>
<ul>{legend}</ul>
{''.join(sections)}
<script>
let cur=null;
function stop(){{ if(cur){{cur.pause();cur=null;}}
  document.querySelectorAll('button.on').forEach(b=>b.classList.remove('on')); }}
function play(src,btn){{ return new Promise(res=>{{
  if(!src) return res();
  const a=new Audio(encodeURI(src)); cur=a; if(btn) btn.classList.add('on');
  a.onended=()=>{{ if(btn) btn.classList.remove('on'); res(); }};
  a.onerror=res; a.play().catch(res); }}); }}
const url=(p,i,s)=>p+'/'+i+'-'+s+'.wav';
document.querySelectorAll('button[data-i]').forEach(b=>{{
  b.onclick=()=>{{ stop(); play(url(b.dataset.p,b.dataset.i,b.dataset.s),b); }}; }});
document.querySelectorAll('button.reel').forEach(b=>{{
  b.onclick=async()=>{{
    stop(); b.classList.add('on');
    const ids=[...document.querySelectorAll('button[data-i]')]
      .filter(x=>x.dataset.p===b.dataset.p&&x.dataset.s===b.dataset.s)
      .map(x=>x.dataset.i);
    for(const i of ids) await play(url(b.dataset.p,i,b.dataset.s),null);
    b.classList.remove('on'); }}; }});
</script>
</html>"""
    (out / "index.html").write_text(page, encoding="utf-8")
    (out / "时长.md").write_text("\n".join(rows_md) + "\n", encoding="utf-8")
    print(f"-> {out}/index.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
