#!/usr/bin/env python3
"""Clone a voice on Alibaba Bailian and speak the same lines there, for comparison.

Everything local has already been tuned as far as it goes; the open question is
whether a bigger hosted model says these sentences more like a person. So the
cloud side is deliberately the same input as the local side — same ref.wav, same
eleven lines — and the output is measured with the same ruler.

Two calls, both plain HTTP so nothing has to be installed:

  1. enrollment — upload the reference as a base64 data URI (no OSS bucket, no
     public URL), get back a voice id.
  2. synthesis  — speak each line with that voice id.

The API key is read from DASHSCOPE_API_KEY and never written anywhere. If
DASHSCOPE_WORKSPACE is set, the workspace-scoped domain is used (the docs
recommend it); otherwise the public endpoint.

Usage:
  export DASHSCOPE_API_KEY=...
  python tools/voice/cloud_bailian.py enroll --pack assets/voice/packs/01 \
      --target-model cosyvoice-v2
  python tools/voice/cloud_bailian.py synth --pack assets/voice/packs/01 \
      --voice cosyvoice-pack01-xxxx --plan assets/voice/demos/sucaiB/lines-v2.json \
      --out-dir assets/voice/demos/packs-cloud/01
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

STATE = Path.home() / ".cache" / "voice-cloud" / "voices.json"
KEY_FILE = Path.home() / ".config" / "dashscope" / "api_key"


def load_creds(csv_path: str | None) -> tuple[str, str]:
    """(api key, base url). Never printed, never logged, never copied into the repo.

    The console's key export is a two-column CSV (field,value) with a workspace
    specific ``dashScope`` base URL — using the workspace domain matters, because
    a key belongs to one workspace.
    """
    if csv_path:
        fields: dict[str, str] = {}
        for line in Path(csv_path).read_text(encoding="utf-8").splitlines():
            if "," in line:
                k, v = line.split(",", 1)
                fields[k.strip()] = v.strip().strip('"')
        key = fields.get("apiKey", "")
        base = fields.get("dashScope", "")
        if key and base:
            return key, base.rstrip("/")
    key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not key and KEY_FILE.exists():
        key = KEY_FILE.read_text(encoding="utf-8").strip()
    ws = os.environ.get("DASHSCOPE_WORKSPACE")
    base = (f"https://{ws}.cn-beijing.maas.aliyuncs.com/api/v1" if ws
            else "https://dashscope.aliyuncs.com/api/v1")
    if not key:
        raise SystemExit(
            "没有凭据：--creds <控制台导出的 csv>，或 export DASHSCOPE_API_KEY=...")
    return key, base


def post(base: str, path: str, payload: dict, key: str, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise SystemExit(f"HTTP {e.code}: {body[:800]}")


def data_uri(path: Path) -> str:
    raw = path.read_bytes()
    return "data:audio/wav;base64," + base64.b64encode(raw).decode("ascii")


def cmd_enroll(args) -> int:
    key, base = load_creds(args.creds)
    ref = Path(args.ref) if getattr(args, "ref", None) else args.pack / "ref.wav"
    payload = {
        "model": "voice-enrollment",
        "input": {
            "action": "create_voice",
            "target_model": args.target_model,
            "prefix": args.prefix or args.pack.name,
            "audio": data_uri(ref),
            "language_hints": ["zh"],
        },
    }
    print(f">> 上传参考音 {ref} ({ref.stat().st_size / 1024:.0f} KB) -> {args.target_model}")
    out = post(base, "/services/audio/tts/customization", payload, key)
    voice = (out.get("output") or {}).get("voice") or out.get("output", {}).get("voice_id")
    if not voice:
        raise SystemExit(f"没有拿到 voice id：{json.dumps(out, ensure_ascii=False)[:800]}")
    STATE.parent.mkdir(parents=True, exist_ok=True)
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    # Key by prefix when one is given: re-enrolling the same pack with a longer
    # reference must not overwrite the id of the voice that already works.
    state[args.prefix or args.pack.name] = {"voice": voice,
                                            "target_model": args.target_model}
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    print(f">> voice id = {voice}（已记到 {STATE}）")
    return 0


def cmd_synth(args) -> int:
    key, base = load_creds(args.creds)
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    items = list(plan.get("lines", [])) + list(plan.get("alts", []))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for i, item in enumerate(items, 1):
        dst = out_dir / f"{item['id']}.wav"
        if dst.exists() and not args.force:
            print(f">> skip {dst.name}")
            continue
        payload = {
            "model": args.model,
            "input": {"text": item["text"], "voice": args.voice,
                      "format": "wav", "sample_rate": 24000},
        }
        if args.instruct:
            # Coarse, one instruction per line, derived from the same emotion
            # vector the local run used — otherwise the comparison is unfair.
            payload["input"]["instruction"] = instruct_for(item)
        t0 = time.time()
        out = post(base, "/services/audio/tts/generation", payload, key)
        audio_url = ((out.get("output") or {}).get("audio") or {}).get("url")
        if not audio_url:
            raise SystemExit(f"[{item['id']}] 没有音频：{json.dumps(out, ensure_ascii=False)[:800]}")
        with urllib.request.urlopen(audio_url, timeout=120) as r:
            dst.write_bytes(r.read())
        print(f">> [{i}/{len(items)}] {item['id']} {dst.name} {time.time() - t0:.1f}s")
    return 0


EMO = ["喜", "怒", "哀", "惧", "厌", "郁", "惊", "静"]


def instruct_for(item: dict) -> str:
    """Turn the 8-dim vector into one short Chinese direction."""
    vec = item.get("emo") or []
    top = max(range(len(vec)), key=lambda i: vec[i]) if vec else None
    words = {0: "轻快一点", 1: "带一点急", 2: "低一点、心疼",
             3: "收着、安抚", 4: "淡一点", 5: "放慢、低沉", 6: "稍微提起来", 7: "安静、温和"}
    bits = []
    if top is not None and vec[top] > 0.05:
        bits.append(words.get(top, "温和"))
    speed = item.get("speed", 1.0)
    bits.append("语速稍慢" if speed > 1.03 else "语速稍快" if speed < 1.0 else "正常语速")
    return "，".join(bits) + "，像对朋友说话，不要念稿"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("enroll")
    e.add_argument("--pack", required=True, type=Path)
    e.add_argument("--creds", default=None, help="控制台导出的 apiKey csv")
    e.add_argument("--target-model", default="cosyvoice-v2")
    e.add_argument("--prefix", default="")
    e.add_argument("--ref", default="", help="override the reference clip (e.g. a stitched longer one)")
    e.set_defaults(fn=cmd_enroll)

    s = sub.add_parser("synth")
    s.add_argument("--voice", required=True)
    s.add_argument("--creds", default=None, help="控制台导出的 apiKey csv")
    s.add_argument("--plan", required=True)
    s.add_argument("--out-dir", required=True)
    s.add_argument("--model", default="cosyvoice-v2")
    s.add_argument("--instruct", action="store_true",
                   help="also send a per-line instruction (only some models accept it)")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_synth)

    args = ap.parse_args()
    load_creds(args.creds)  # fail early, before any upload, if there are no creds
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
