#!/usr/bin/env python3
"""Generate a voice from a *spec*, not from someone's recording.

VoiceDesign turns a description into a timbre, but the wording moves the result
a lot (we measured 39–119 Hz of drift from small edits). So don't trust one
attempt: aim at a numeric target, generate a batch of candidates from
deliberately varied prompts, measure every candidate with the same ruler used to
write the spec, and keep the closest. The target can come from anywhere — it is
a table of numbers, so no recording of anyone's voice ever enters the model.

Usage:
  python3 tools/voice/design_search.py \
    --model-dir ~/.cache/modelscope/models/Qwen--Qwen3-TTS-12Hz-1.7B-VoiceDesign/snapshots/master \
    --spec assets/voice/analysis/fgo-style-spec.json --spec-key 07 \
    --text "嗯，我在。你不用急着说清楚。" \
    --out assets/voice/demos/design-07 --device mps
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

# spec key -> performer gender. Keys 09-11 measure male voices (~118-181 Hz), so a
# female description there would be ranked against a target it cannot reach.
GENDER = {
    "01": "female", "02": "female", "03": "female", "04": "female",
    "05": "female", "06": "female", "07": "female", "08": "female",
    "09": "male", "10": "male", "11": "male",
}

# Each variant leans on one dimension; the search needs spread, not eloquence.
PROMPT_VARIANTS = [
    "{who}，中低音区，声音偏暗，气息感明显，语速不快不慢，像深夜电台里说话。",
    "{who}，声音低沉温和，略带沙哑，句子之间有自然停顿，不表演。",
    "{who}，中音偏低，气息多、共鸣靠后，语速接近日常对话，安静地说。",
    "{who}，声音厚实偏暖，带一点沙，语速稍慢，尾音自然收住。",
    "{who}，中低音，轻声但不虚，句子短，停顿多，像在听人说话。",
    "{who}，音色柔和偏暗，呼吸声可闻，语速正常，不做作。",
    "{who}，偏低的中音，声音贴近、私密，语速平稳，句尾下沉。",
    "{who}，温暖的中低音，略微气声，语速偏慢，像晚上聊天。",
]


def build_prompts(gender: str) -> list[str]:
    who = "成年男性" if gender == "male" else "成年女性"
    if gender == "male":
        # same eight dimensions, pitched where a male voice actually sits
        male = [
            "成年男性，低沉的中音，声音偏暗，气息自然，语速不快不慢，像深夜电台里说话。",
            "成年男性，声音低沉温和，略带沙哑，句子之间有自然停顿，不表演。",
            "成年男性，中音偏低，共鸣靠后，语速接近日常对话，安静地说。",
            "成年男性，声音厚实偏暖，带一点沙，语速稍慢，尾音自然收住。",
            "成年男性，低音，轻声但不虚，句子短，停顿多，像在听人说话。",
            "成年男性，音色偏暗，呼吸声可闻，语速正常，不做作。",
            "成年男性，偏低的中音，声音贴近、私密，语速平稳，句尾下沉。",
            "成年男性，温暖的男中音，略微气声，语速偏慢，像晚上聊天。",
        ]
        return male
    return [v.format(who=who) for v in PROMPT_VARIANTS]


def measure(y, sr, librosa, np) -> dict:
    f0, _, _ = librosa.pyin(y, fmin=60, fmax=500, sr=sr, frame_length=1024, hop_length=256)
    voiced = f0[~np.isnan(f0)]
    from metrics import band_centroid

    centroid = band_centroid(y, sr, librosa, np)
    flatness = float(np.mean(librosa.feature.spectral_flatness(y=y)))
    intervals = librosa.effects.split(y, top_db=35, frame_length=1024, hop_length=256)
    seg = [(b - a) / sr for a, b in intervals]
    speech = sum(seg)
    return {
        "dur": round(len(y) / sr, 2),
        "f0_mean": round(float(voiced.mean()), 1) if voiced.size else 0.0,
        "f0_sd": round(float(voiced.std()), 1) if voiced.size else 0.0,
        "centroid": round(centroid),
        "flatness": round(flatness, 4),
        "pause_per_min": round(max(len(seg) - 1, 0) / (speech / 60), 1) if speech else 0,
        "phrase_len": round(sum(seg) / len(seg), 2) if seg else 0,
    }


WEIGHTS = {"f0_mean": 1.0, "f0_sd": 0.5, "centroid": 1.0, "flatness": 1.5,
           "pause_per_min": 0.3, "phrase_len": 0.5}

# Per-field scale = roughly the spread of these six numbers across the reference
# profiles. A fixed scale keeps scores comparable between profiles and stays sane
# when a target sits near zero — dividing by the target (relative error) made any
# candidate against 所罗门·Pretender's 0.010 breathiness score 20-30 on that field
# alone, which buried every other dimension.
SCALE = {"f0_mean": 70.0, "f0_sd": 15.0, "centroid": 120.0,
         "flatness": 0.06, "pause_per_min": 28.0, "phrase_len": 0.45}


def distance(got: dict, target: dict) -> float:
    total = 0.0
    for k, w in WEIGHTS.items():
        if k not in target or target[k] is None:
            continue
        total += w * abs(got[k] - target[k]) / SCALE[k]
    return round(total, 3)


# A sum of normalised errors lets a bright-but-wrong-pitch candidate tie a
# warm-but-right-pitch one. Pitch is not tradeable — 12% off is a different voice,
# however well the other numbers line up — so it gates the ranking instead.
PITCH_TOL = 0.12


def pitch_ok(got: dict, target: dict) -> bool:
    t = target.get("f0_mean")
    return bool(t) and abs(got["f0_mean"] - t) <= PITCH_TOL * t


def rank_key(row: dict, target: dict) -> tuple[int, float]:
    return (0 if pitch_ok(row, target) else 1, row["score"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model-dir", required=True, type=Path)
    ap.add_argument("--spec", required=True, type=Path, help="output of voice_spec.py")
    ap.add_argument("--spec-key", required=True, help="folder name inside the spec")
    ap.add_argument("--text", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--device", default="mps")
    ap.add_argument("--gender", choices=["female", "male"], default="",
                    help="voice gender to design (default: from the spec key)")
    ap.add_argument("--n", type=int, default=0, help="candidates to run (default: all variants)")
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args()

    import librosa
    import numpy as np
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    if args.spec_key not in spec:
        print(f"spec has no key {args.spec_key!r}; available: {list(spec)}")
        return 2
    target = spec[args.spec_key]["summary"]
    gender = args.gender or GENDER.get(args.spec_key, "female")
    print("target:", {k: target[k] for k in WEIGHTS if k in target})
    print("gender:", gender)

    model = Qwen3TTSModel.from_pretrained(str(args.model_dir), dtype=torch.bfloat16, low_cpu_mem_usage=True)
    model.model.to(args.device)
    model.model.eval()
    model.device = torch.device(args.device)

    args.out.mkdir(parents=True, exist_ok=True)
    variants = build_prompts(gender)
    prompts = variants[: args.n] if args.n else variants
    rows = []
    for i, prompt in enumerate(prompts, 1):
        torch.manual_seed(args.seed)
        t0 = time.time()
        try:
            wavs, sr = model.generate_voice_design(text=args.text, instruct=prompt,
                                                   language="Chinese", temperature=0.9, top_p=0.95)
        except Exception as exc:  # noqa: BLE001 - keep going, report
            print(f"  ! c{i:02d}: {type(exc).__name__}: {exc}")
            continue
        y = wavs[0]
        path = args.out / f"c{i:02d}.wav"
        sf.write(str(path), y, sr)
        m = measure(y, sr, librosa, np)
        m.update({"id": f"c{i:02d}", "prompt": prompt, "gen_s": round(time.time() - t0, 1),
                  "score": distance(m, target), "file": path.name})
        rows.append(m)
        print(f"  c{i:02d} F0 {m['f0_mean']:5.1f} sd {m['f0_sd']:5.1f} centroid {m['centroid']:5.0f} "
              f"flat {m['flatness']:.3f}  score {m['score']:.3f}")

    rows.sort(key=lambda r: r["score"])
    report = {"target": target, "spec_key": args.spec_key, "gender": gender,
              "text": args.text, "candidates": rows}
    (args.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nranking:")
    for r in rows[:5]:
        print(f"  {r['id']}  score {r['score']:.3f}  F0 {r['f0_mean']}  flat {r['flatness']}")
    if rows:
        print(f"\nbest: {rows[0]['id']} -> {args.out / rows[0]['file']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
