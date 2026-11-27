#!/usr/bin/env python3
"""Synthesize Qwen3-TTS-Flash system voices through Alibaba Cloud DashScope.

This is the only way to get the cloud voices the project picked earlier:
Maia (四月), Chelsie (千雪), Serena (苏瑶), Cherry (芊悦), Vivian (十三) and
the rest of the 48-voice roster. They are NOT in the local CustomVoice
checkpoint: local "Serena" is different weights from cloud "Serena".

Requires an API key from the Bailian / Model Studio console:
    https://bailian.console.aliyun.com/   ->  API-KEY
Put it in the environment, never in a file or on the command line:

    export DASHSCOPE_API_KEY="sk-..."
    python3 tools/voice/synth_dashscope.py \
      --voices Maia,Chelsie --out assets/voice/demos/cloud-maia-chelsea

The script never prints or stores the key; it only forwards it as a Bearer token.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

ENDPOINTS = {
    "cn": "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
    "intl": "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
}

DEFAULT_TEXT = (
    "嗯……我们可以慢一点。你刚才说到导师，停了一下。"
    "今天挺累的吧？最烦的是哪件事？"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Synthesize qwen3-tts-flash voices via DashScope.")
    parser.add_argument("--voices", default="Maia,Chelsie", help="comma-separated voice ids")
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--model", default="qwen3-tts-flash", help="qwen3-tts-flash or qwen3-tts-instruct-flash")
    parser.add_argument("--instructions", default="", help="natural-language style instruction (instruct model only)")
    parser.add_argument("--language-type", default="Chinese")
    parser.add_argument("--region", default="cn", choices=sorted(ENDPOINTS))
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--tag", default="", help="suffix for output file names")
    return parser.parse_args()


def request_audio(api_key: str, endpoint: str, model: str, text: str, voice: str,
                  language_type: str, instructions: str) -> dict:
    payload = {
        "model": model,
        "input": {"text": text, "voice": voice, "language_type": language_type},
    }
    if instructions:
        payload["input"]["instructions"] = instructions
        payload["input"]["optimize_instructions"] = True
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_url(data: dict) -> str | None:
    output = data.get("output") or {}
    audio = output.get("audio") or {}
    return audio.get("url") or output.get("url")


def to_wav(source: Path, target: Path) -> None:
    """Convert whatever DashScope returns (wav/mp3) to 24 kHz mono 16-bit PCM."""
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", "LEI16@24000", "-c", "1", str(source), str(target)],
        check=True,
        capture_output=True,
    )


def wav_seconds(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as wav:
            rate = wav.getframerate()
            return wav.getnframes() / rate if rate else None
    except (wave.Error, OSError):
        return None


def main() -> int:
    args = parse_args()
    api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        print(
            "DASHSCOPE_API_KEY is not set.\n"
            "Get a key at https://bailian.console.aliyun.com/ (API-KEY), then:\n"
            '  export DASHSCOPE_API_KEY="sk-..."',
            file=sys.stderr,
        )
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    voices = [v.strip() for v in args.voices.split(",") if v.strip()]
    endpoint = ENDPOINTS[args.region]

    records = []
    for voice in voices:
        started = time.time()
        print(f"requesting {voice} ({args.model}) ...", flush=True)
        try:
            data = request_audio(
                api_key, endpoint, args.model, args.text, voice, args.language_type, args.instructions
            )
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            print(f"FAIL {voice}: HTTP {exc.code} {detail}", flush=True)
            records.append({"voice": voice, "error": f"HTTP {exc.code}: {detail}"})
            continue
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"FAIL {voice}: {exc}", flush=True)
            records.append({"voice": voice, "error": str(exc)})
            continue

        url = extract_url(data)
        if not url:
            print(f"FAIL {voice}: no audio url in {json.dumps(data, ensure_ascii=False)[:400]}", flush=True)
            records.append({"voice": voice, "error": "no audio url", "response": data})
            continue

        suffix = ".wav" if ".wav" in url.lower() else ".mp3"
        tag = f"-{args.tag}" if args.tag else ""
        target = args.out / f"{voice.lower()}{tag}.wav"
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / f"raw{suffix}"
            urllib.request.urlretrieve(url, raw)
            to_wav(raw, target)

        seconds = wav_seconds(target)
        elapsed = time.time() - started
        print(f"{target.name}: {seconds:.2f}s audio in {elapsed:.1f}s", flush=True)
        records.append(
            {
                "voice": voice,
                "model": args.model,
                "text": args.text,
                "instructions": args.instructions,
                "file": target.name,
                "duration": round(seconds, 3) if seconds else None,
                "seconds": round(elapsed, 2),
                "request_id": data.get("request_id"),
            }
        )

    meta = {
        "provider": "dashscope",
        "region": args.region,
        "text": args.text,
        "model": args.model,
        "instructions": args.instructions,
        "records": records,
    }
    (args.out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    failures = sum(1 for r in records if "error" in r)
    print(f"done: {len(records) - failures}/{len(records)} ok -> {args.out}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
