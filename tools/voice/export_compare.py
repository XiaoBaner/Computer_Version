#!/usr/bin/env python3
"""One page that puts the local clone and the cloud clone side by side.

Same reference, same eleven lines, same emotion plan — so a difference you hear
is a difference between the two engines, not between two sets of instructions.
Each cell has 本地 / 云端 buttons and an A/B button that plays one after the other
with a short gap, because switching between two clips by hand is exactly the
comparison nobody does carefully.

Both sides get identical treatment: comma pauses tightened to 0.34 s by cutting
silence only, resampled to 24 kHz, normalised to RMS -20 dBFS.

Usage:
  python tools/voice/export_compare.py \
    --local assets/voice/demos/packs --cloud assets/voice/demos/packs-cloud \
    --out ~/Desktop/试听-本地vs云端
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def prep(src: Path, dst: Path, gap: float):
    import librosa
    import numpy as np
    import soundfile as sf

    from repair_flow import tighten

    y, sr = librosa.load(str(src), sr=None, mono=True)
    y, _ = tighten(y, sr, gap, 0.45, 0.25, 0.60)
    if sr != 24000:
        y = librosa.resample(y, orig_sr=sr, target_sr=24000)
        sr = 24000
    rms = float(np.sqrt(np.mean(y ** 2))) or 1.0
    y = y * (10 ** (-20 / 20) / rms)
    peak = float(np.abs(y).max())
    if peak > 0.89:
        y = y * (0.89 / peak)
    sf.write(str(dst), y, sr)
    return y, sr


def median_f0(path: Path) -> float:
    import librosa
    import numpy as np

    y, sr = librosa.load(str(path), sr=None, mono=True)
    v = librosa.pyin(y, fmin=70, fmax=500, sr=sr, frame_length=2048)[0]
    v = v[np.isfinite(v)]
    return float(np.median(v)) if v.size else 0.0


def metrics(y, sr) -> dict:
    import librosa
    import numpy as np
    from scipy.signal import find_peaks

    # pYIN, not YIN: plain YIN octave-jumps on breathy female speech and made a
    # 250 Hz clip read as 294 Hz.
    f0, _, _ = librosa.pyin(y, fmin=70, fmax=500, sr=sr, frame_length=2048)
    v = f0[np.isfinite(f0) & (f0 > 0)]
    st = 12 * np.log2(v / np.median(v)) if len(v) else np.zeros(1)
    hop = int(0.010 * sr)
    rms = librosa.feature.rms(y=y, frame_length=hop * 2, hop_length=hop)[0]
    db = 20 * np.log10(np.maximum(rms, 1e-8))
    voiced = db > db.max() - 30
    win = 20
    peak = np.array([db[max(0, i - win):i + win].max() for i in range(len(db))])
    depth = np.where(voiced, peak - db, 0.0)
    dips, _ = find_peaks(depth, height=9, distance=4)
    iv = np.diff(dips) * 0.010
    seg = librosa.effects.split(y, top_db=30)
    return {
        "f0": float(np.median(v)) if len(v) else 0.0,
        "span": float(np.percentile(st, 95) - np.percentile(st, 5)),
        "cv": float(np.std(iv) / np.mean(iv)) if len(iv) > 2 else 0.0,
        "dur": len(y) / sr,
        "voiced": sum(b - a for a, b in seg) / sr,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--local", required=True, type=Path)
    ap.add_argument("--cloud", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--plan", type=Path,
                    default=Path("assets/voice/demos/sucaiB/lines-v2.json"))
    ap.add_argument("--gap", type=float, default=0.34)
    ap.add_argument("--label-a", default="本地")
    ap.add_argument("--label-b", default="云端")
    args = ap.parse_args()

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    items = list(plan.get("lines", [])) + list(plan.get("alts", []))
    text = {i["id"]: i["text"] for i in items}
    order = [i["id"] for i in items]

    out = args.out.expanduser()
    out.mkdir(parents=True, exist_ok=True)

    ref_root = Path("assets/voice/packs")
    packs = sorted(p.name for p in args.cloud.iterdir()
                   if p.is_dir() and any(p.glob("*.wav")))
    rows = []
    for pack in packs:
        d = out / pack
        d.mkdir(exist_ok=True)
        got, m_local, m_cloud = [], {"f0": [], "span": [], "cv": [], "dur": []}, \
            {"f0": [], "span": [], "cv": [], "dur": []}
        for pid in order:
            srcs = {"local": args.local / pack / f"{pid}.wav",
                    "cloud": args.cloud / pack / f"{pid}.wav"}
            if not all(s.exists() for s in srcs.values()):
                continue
            for side, src in srcs.items():
                y, sr = prep(src, d / f"{pid}-{side}.wav", args.gap)
                m = metrics(y, sr)
                acc = m_local if side == "local" else m_cloud
                for k in acc:
                    acc[k].append(m[k])
            got.append(pid)
        if not got:
            continue
        ref_path = ref_root / pack / "ref.wav"
        rows.append({"pack": pack, "ids": got,
                     "ref": median_f0(ref_path) if ref_path.exists() else 0.0,
                     "local": {k: sum(v) / len(v) for k, v in m_local.items() if v},
                     "cloud": {k: sum(v) / len(v) for k, v in m_cloud.items() if v}})

    def esc(s: str) -> str:
        return html.escape(s, quote=True)

    head = "".join(f"<th title='{esc(text[i])}'>{esc(i)}</th>" for i in order)
    body = []
    for r in rows:
        cells = []
        for pid in order:
            if pid in r["ids"]:
                cells.append(
                    f"<td><button class='lo' data-p='{r['pack']}' data-i='{pid}' "
                    f"data-s='local' title='{esc(text[pid])}'>{args.label_a[:2]}</button>"
                    f"<button class='cl' data-p='{r['pack']}' data-i='{pid}' "
                    f"data-s='cloud' title='{esc(text[pid])}'>{args.label_b[:2]}</button>"
                    f"<button class='ab' data-p='{r['pack']}' data-i='{pid}' "
                    f"title='先本地后云端'>A/B</button></td>")
            else:
                cells.append("<td class='na'>–</td>")
        lo, cl = r["local"], r["cloud"]
        body.append(
            f"<tr><th class='pk'>{esc(r['pack'])}"
            f"<span class='sub'>参考 {r['ref']:.0f}Hz"
            f"<br>本地 {lo['f0']:.0f} ({lo['f0'] - r['ref']:+.0f}) · {lo['cv']:.2f}"
            f"<br>云端 {cl['f0']:.0f} ({cl['f0'] - r['ref']:+.0f}) · {cl['cv']:.2f}"
            f"</span></th>{''.join(cells)}</tr>")

    legend = "".join(f"<li><b>{esc(i)}</b> {esc(text[i])}</li>" for i in order)
    page = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>本地 vs 云端</title>
<style>
 :root {{ color-scheme: light dark; --fg:#1b1b1f; --mut:#6b6b76; --line:#d8d8e0;
          --lo:#2f6f5e; --cl:#8a4a9e; }}
 @media (prefers-color-scheme: dark) {{ :root {{ --fg:#e8e8ec; --mut:#9a9aa6;
          --line:#33333c; --lo:#67c2a8; --cl:#c48ad8; }} }}
 body {{ font: 15px/1.6 -apple-system,"PingFang SC","Noto Sans SC",sans-serif;
        color:var(--fg); margin:40px auto; max-width:1180px; padding:0 20px; }}
 h1 {{ font-size:22px; font-weight:600; margin:0 0 4px; }}
 p.sub {{ color:var(--mut); margin:0 0 26px; }}
 table {{ border-collapse:collapse; width:100%; }}
 th,td {{ border-bottom:1px solid var(--line); padding:6px 3px; font-weight:400; }}
 thead th {{ font-size:13px; color:var(--mut); text-align:center; }}
 th.pk {{ text-align:left; font-size:15px; padding:12px 12px 12px 0; white-space:nowrap; }}
 th.pk .sub {{ display:block; font-size:12px; color:var(--mut); font-weight:400; }}
 button {{ font:inherit; font-size:13px; padding:5px 8px; margin:1px; border-radius:7px;
          cursor:pointer; border:1px solid var(--line); background:transparent; color:var(--fg); }}
 button.lo:hover,button.lo.on {{ border-color:var(--lo); color:var(--lo); }}
 button.cl:hover,button.cl.on {{ border-color:var(--cl); color:var(--cl); }}
 button.ab {{ color:var(--mut); }}
 button.ab:hover {{ border-color:var(--fg); color:var(--fg); }}
 td {{ text-align:center; }}
 td.na {{ color:var(--line); }}
 ul {{ color:var(--mut); font-size:13px; columns:2; margin-top:26px; }}
 li {{ break-inside:avoid; }}
</style>
<h1>本地 vs 云端 · 同一份参考音、同一批台词</h1>
<p class="sub">「{args.label_a[:2]}」/「{args.label_b[:2]}」两组对比，「A/B」= 先放一组再放另一组。
两边都剪到 0.34 秒停顿、统一 24 kHz 与响度。每行小字是平均中位音高与节奏变异系数。</p>
<table><thead><tr><th class="pk">音色</th>{head}</tr></thead>
<tbody>{''.join(body)}</tbody></table>
<ul>{legend}</ul>
<script>
let cur = null;
function stop() {{ if (cur) {{ cur.pause(); cur = null; }}
  document.querySelectorAll('button.on').forEach(b => b.classList.remove('on')); }}
function playOne(src, btn) {{
  if (!src) return Promise.resolve();
  return new Promise(res => {{
    const a = new Audio(encodeURI(src)); cur = a;
    if (btn) btn.classList.add('on');
    a.onended = () => {{ if (btn) btn.classList.remove('on'); res(); }};
    a.onerror = res; a.play().catch(res);
  }});
}}
function src(p, i, s) {{ return p + '/' + i + '-' + s + '.wav'; }}
document.querySelectorAll('button.lo,button.cl').forEach(b => {{
  b.onclick = () => {{ stop(); playOne(src(b.dataset.p, b.dataset.i, b.dataset.s), b); }};
}});
document.querySelectorAll('button.ab').forEach(b => {{
  b.onclick = async () => {{
    stop(); b.classList.add('on');
    await playOne(src(b.dataset.p, b.dataset.i, 'local'), null);
    await new Promise(r => setTimeout(r, 380));
    await playOne(src(b.dataset.p, b.dataset.i, 'cloud'), null);
    b.classList.remove('on');
  }};
}});
</script>
</html>"""
    (out / "index.html").write_text(page, encoding="utf-8")

    md = ["# 本地 vs 云端 · 逐句指标\n",
          "| 音色 | 参考 F0 | 本地 F0 | 本地偏差 | 云端 F0 | 云端偏差 | 本地跨度 | 云端跨度 "
          "| 本地节奏CV | 云端节奏CV |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lo, cl, ref = r["local"], r["cloud"], r["ref"]
        md.append(f"| {r['pack']} | {ref:.0f} | {lo['f0']:.0f} | {lo['f0'] - ref:+.0f} | "
                  f"{cl['f0']:.0f} | {cl['f0'] - ref:+.0f} | {lo['span']:.1f} | "
                  f"{cl['span']:.1f} | {lo['cv']:.2f} | {cl['cv']:.2f} |")
    (out / "指标.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"{len(rows)} 个音色对比 -> {out}/index.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
