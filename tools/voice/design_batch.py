#!/usr/bin/env python3
"""Design one candidate voice per measured profile — from numbers, not recordings.

For every profile in `voice_spec.py`'s output (pitch, spread, brightness,
breathiness, rhythm), derive a Chinese description from those numbers and have
VoiceDesign synthesise it, then score the result with the same ruler. Each
profile gets several differently-phrased descriptions so the page shows what
wording does at that point in the space.

The spec is a table of measurement statistics. No audio of anyone's voice is
read or fed to the model at any point — the only inputs are numbers and text.

Resumable: an existing `cNN.wav` plus its row in `report.json` is skipped, so an
interrupted run picks up where it stopped.

Usage:
  python3 tools/voice/design_batch.py \
    --model-dir ~/.cache/modelscope/models/Qwen--Qwen3-TTS-12Hz-1.7B-VoiceDesign/snapshots/master \
    --spec assets/voice/analysis/fgo-style-spec.json \
    --out assets/voice/demos/design-batch --per-profile 4 --device mps
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from design_search import GENDER, WEIGHTS, distance, measure  # noqa: E402

# Folder -> character (by clip count against 总览.txt), for the page only.
LABEL = {
    "01": "阿尔托莉雅·卡斯特", "02": "久远寺有珠", "03": "摩根",
    "04": "苍崎青子", "05": "光之高扬斯卡娅", "06": "暗之高扬斯卡娅",
    "07": "梅柳齐娜·Lancer", "08": "梅柳齐娜·Ruler", "09": "盖提亚",
    "10": "所罗门", "11": "所罗门·Pretender",
    "anchor04": "04 锚点 · 苍崎青子 c03",
    "anchor06": "06 锚点 · 暗之高扬斯卡娅 c03",
}


def describe(s: dict, gender: str) -> dict:
    """Turn a measured profile into short Chinese adjectives."""
    f0, sd = s["f0_mean"], s["f0_sd"]
    cen, flat = s["centroid"], s["flatness"]
    pl, pm = s["phrase_len"], s["pause_per_min"]

    if gender == "male":
        who = "成年男性"
        if f0 < 140:
            pitch = "嗓音很低的男低音"
        elif f0 < 180:
            pitch = "偏低沉的男中音"
        else:
            pitch = "男中音"
    else:
        who = "成年女性"
        if f0 < 250:
            pitch = "中低音"
        elif f0 < 300:
            pitch = "中音"
        elif f0 < 330:
            pitch = "中高音"
        else:
            pitch = "偏高的中音"

    # thresholds are quantiles of the measured corpus (band-limited centroid
    # 100-7000 Hz: 1656-2041 across the 11 profiles), not raw full-band numbers
    bright = ("音色偏暗、共鸣靠后" if cen < 1800 else
              "音色中性偏暖" if cen < 1930 else "音色明亮通透")
    breath = ("干净、几乎没有气声" if flat < 0.06 else
              "带一点气声" if flat < 0.12 else
              "气声明显" if flat < 0.17 else "气声很重、略带沙哑")
    tempo = ("句子短促、停顿多" if pl < 0.7 else
             "句子中等长度" if pl < 1.1 else "句子较长、连贯")
    energy = ("语调平稳、起伏很小" if sd < 30 else
              "语调自然起伏" if sd < 50 else "语调起伏大、有活力")
    rhythm = "话说得很密" if pm > 90 else "停顿适中" if pm > 45 else "停顿多、留白长"
    return {"who": who, "pitch": pitch, "bright": bright, "breath": breath,
            "tempo": tempo, "energy": energy, "rhythm": rhythm}


def prompts_for(s: dict, gender: str, pitch: str = "") -> list[str]:
    d = describe(s, gender)
    if pitch:
        d = {**d, "pitch": pitch}
    return [
        f"{d['who']}，{d['pitch']}，{d['bright']}，{d['breath']}，{d['tempo']}，{d['energy']}，说话不做作。",
        f"{d['who']}，{d['pitch']}，在安静的房间里说话，{d['breath']}，{d['tempo']}，{d['rhythm']}。",
        f"{d['who']}，{d['pitch']}，{d['bright']}，语速不快，{d['tempo']}，{d['energy']}。",
        f"{d['who']}，{d['pitch']}，{d['breath']}，{d['energy']}，{d['tempo']}，语气温和克制。",
    ]


# How far each pitch phrase actually lands, measured on the first 44 candidates.
# The model answers categories, not numbers, so a sweep has to move in categories.
PITCH_LADDER = {
    "female": [("低音", 170), ("中低音", 200), ("中低音偏上", 225), ("中音偏低", 250),
               ("中音", 285), ("中音偏高", 305), ("中高音", 325), ("偏高的中音", 345)],
    "male": [("很低的男低音", 110), ("低沉的男低音", 130), ("偏低的男中音", 155),
             ("男中音", 180), ("偏高的男中音", 205)],
}


def sweep_prompts(s: dict, gender: str, n: int) -> list[str]:
    """Hold the other adjectives fixed and walk pitch toward the target."""
    ladder = sorted(PITCH_LADDER[gender], key=lambda kv: abs(kv[1] - s["f0_mean"]))
    return [prompts_for(s, gender, pitch=phrase)[0] for phrase, _hz in ladder[:n]]


def write_json(path: Path, obj) -> None:
    """Write through a temp file so a concurrent reader never sees half a report."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_done(report_path: Path) -> tuple[dict, list[dict]]:
    if report_path.exists():
        try:
            r = json.loads(report_path.read_text(encoding="utf-8"))
            return r.get("target", {}), r.get("candidates", [])
        except (json.JSONDecodeError, OSError):
            return {}, []
    return {}, []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model-dir", required=True, type=Path)
    ap.add_argument("--spec", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--text", default="嗯，我在。你不用急着说清楚。")
    ap.add_argument("--per-profile", type=int, default=4)
    ap.add_argument("--pitch-sweep", action="store_true",
                    help="vary only the pitch phrase, walking toward the target (for a second round)")
    ap.add_argument("--prompt-file", type=Path,
                    help="JSON {key: [prompt, ...]} replacing the derived prompts for those keys")
    ap.add_argument("--keys", default="", help="comma list of spec keys (default: all)")
    ap.add_argument("--device", default="mps")
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args()

    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel
    import librosa
    import numpy as np

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    overrides = json.loads(args.prompt_file.read_text(encoding="utf-8")) if args.prompt_file else {}
    keys = [k.strip() for k in args.keys.split(",") if k.strip()] or sorted(spec)
    missing = [k for k in keys if k not in spec]
    if missing:
        print(f"spec has no keys {missing}; has {list(spec)}")
        return 2

    model = Qwen3TTSModel.from_pretrained(str(args.model_dir), dtype=torch.bfloat16, low_cpu_mem_usage=True)
    model.model.to(args.device)
    model.model.eval()
    model.device = torch.device(args.device)
    print(f"model loaded on {args.device}", flush=True)

    started = time.time()
    done_total, total, made = 0, len(keys) * args.per_profile, 0
    summary = []
    for key in keys:
        target = spec[key]["summary"]
        gender = GENDER.get(key, "female")
        out_dir = args.out / key
        out_dir.mkdir(parents=True, exist_ok=True)
        report_path = out_dir / "report.json"
        _, rows = load_done(report_path)
        # rows carry measurements, not a fixed score: recompute against this run's
        # target so a changed spec cannot leave a cached candidate ranked by a stale one
        for r in rows:
            r["score"] = distance(r, target)
        have = {r["id"] for r in rows}
        if key in overrides:
            prompts = overrides[key][: args.per_profile]
        elif args.pitch_sweep:
            prompts = sweep_prompts(target, gender, args.per_profile)
        else:
            prompts = prompts_for(target, gender)[: args.per_profile]

        for i, prompt in enumerate(prompts, 1):
            cid = f"c{i:02d}"
            done_total += 1
            if cid in have and (out_dir / f"{cid}.wav").exists():
                print(f"[{done_total}/{total}] {key} {cid} cached", flush=True)
                continue
            torch.manual_seed(args.seed)
            t0 = time.time()
            try:
                wavs, sr = model.generate_voice_design(
                    text=args.text, instruct=prompt, language="Chinese",
                    temperature=0.9, top_p=0.95,
                )
            except Exception as exc:  # noqa: BLE001 - keep the batch alive, report it
                print(f"[{done_total}/{total}] {key} {cid} FAILED {type(exc).__name__}: {exc}", flush=True)
                continue
            y = wavs[0]
            sf.write(str(out_dir / f"{cid}.wav"), y, sr)
            m = measure(y, sr, librosa, np)
            m.update({"id": cid, "prompt": prompt, "gen_s": round(time.time() - t0, 1),
                      "score": distance(m, target), "file": f"{cid}.wav"})
            rows = [r for r in rows if r["id"] != cid] + [m]
            rows.sort(key=lambda r: r["id"])
            write_json(report_path, {
                "spec_key": key, "label": LABEL.get(key, key), "gender": gender,
                "text": args.text, "target": target, "candidates": rows,
            })
            made += 1
            eta_min = (time.time() - started) / made * (total - done_total) / 60
            print(f"[{done_total}/{total}] {key} {cid} F0 {m['f0_mean']:5.1f} cen {m['centroid']:5.0f} "
                  f"flat {m['flatness']:.3f} score {m['score']:.3f} ({m['gen_s']:.0f}s)"
                  f" ~{eta_min:.0f} min left", flush=True)

        best = min(rows, key=lambda r: r["score"]) if rows else None
        if best:
            summary.append({"key": key, "label": LABEL.get(key, key), "gender": gender,
                            "target": target, "best_id": best["id"], "score": best["score"],
                            "f0_mean": best["f0_mean"], "centroid": best["centroid"],
                            "flatness": best["flatness"], "prompt": best["prompt"]})

    (args.out / "summary.json").write_text(
        json.dumps({"text": args.text, "profiles": summary}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"done: {(time.time() - started) / 60:.1f} min -> {args.out}", flush=True)
    for row in summary:
        print(f"  {row['key']} {row['label']:<14} best {row['best_id']} score {row['score']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
