#!/usr/bin/env python3
"""Speak the project's demo lines in every voice pack, under one model load.

A voice pack is a folder with `ref.wav` in it (the packs from voicepacks-v1.0.zip
also carry a README and config, but all this needs is the reference). The same
eleven lines with the same per-line emotion vectors are rendered for each pack,
so the packs can be compared on the only thing that matters — whether the same
sentence, said the same way, sounds like a person in that voice.

Model load on M4 is most of the cost (~90 s), so packs run in one process and
each line is skipped if its output already exists. Killing the job and restarting
it resumes where it stopped.

Usage:
  ~/.cache/indextts-venv/bin/python -u tools/voice/packs_batch.py \
    --src ~/.cache/indextts-src/src --model-dir ~/.cache/modelscope/indextts2.5 \
    --packs-root assets/voice/packs \
    --plan assets/voice/demos/sucaiB/lines-v2.json \
    --out-root assets/voice/demos/packs
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", required=True, type=Path)
    ap.add_argument("--model-dir", required=True, type=Path)
    ap.add_argument("--packs-root", required=True, type=Path,
                    help="directory whose subfolders each contain ref.wav")
    ap.add_argument("--plan", required=True, type=Path)
    ap.add_argument("--out-root", required=True, type=Path)
    ap.add_argument("--only", default="", help="comma-separated pack names, default all")
    ap.add_argument("--lang", default="ZH")
    args = ap.parse_args()

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    items = list(plan.get("lines", [])) + list(plan.get("alts", []))
    if not items:
        raise SystemExit("plan has no lines")

    root = args.packs_root.expanduser().resolve()
    wanted = [p.strip() for p in args.only.split(",") if p.strip()]
    packs = sorted(p for p in root.iterdir()
                   if p.is_dir() and (p / "ref.wav").exists()
                   and (not wanted or p.name in wanted))
    if not packs:
        raise SystemExit(f"no voice packs with ref.wav under {root}")

    # Work out the whole to-do list before loading the model, so the log says
    # up front how long this will take.
    out_root = args.out_root.expanduser().resolve()
    todo = [(p, item) for p in packs for item in items
            if not (out_root / p.name / f"{item['id']}.wav").exists()]
    print(f">> {len(packs)} 个音色，{len(items)} 句/个，待生成 {len(todo)} 条", flush=True)
    if not todo:
        return 0

    sys.path.insert(0, str(args.src.expanduser().resolve()))
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    from indextts.infer_v2_5 import IndexTTS2  # noqa: E402

    model_dir = str(args.model_dir.expanduser().resolve())
    tts = IndexTTS2(cfg_path=os.path.join(model_dir, "config.yaml"), model_dir=model_dir,
                    use_bf16=False, use_cuda_kernel=False, use_deepspeed=False,
                    use_qwen_emo=False)
    print(f">> device={tts.device}", flush=True)

    done = 0
    for pack, item in todo:
        out_dir = out_root / pack.name
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{item['id']}.wav"
        print(f">> [{done + 1}/{len(todo)}] {pack.name}/{item['id']} {item['text']}", flush=True)
        tts.infer(spk_audio_prompt=str(pack / "ref.wav"), text=item["text"],
                  lang=args.lang, output_path=str(out),
                  emo_vector=[float(x) for x in item["emo"]],
                  duration_factor=float(item.get("speed", 1.0)),
                  verbose=False)
        done += 1
        print(f">> wrote {pack.name}/{out.name}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
