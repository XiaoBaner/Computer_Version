"""量"她多久开口"：流式合成的首包延迟与下行节奏。

首包延迟是实时对话里最贵的一个数字——用户说完到听见第一个字，超过 1.5 秒
对话就开始像在打电话给客服。这个脚本只做测量，不改任何音频。

    python3 tools/voice/stream_probe.py --creds <csv> [--runs 8] [--style gentle]

输出每次的首包、末包、总时长，以及中位数 / P95。
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from voice import tts  # noqa: E402

LINE = "嗯，我在。你不用急着说清楚，想到哪儿说到哪儿就行。"


def once(creds: str, style: str, voice: str | None) -> dict:
    t0 = time.perf_counter()
    first = None
    chunks = 0
    total = 0
    rate = 0
    for pcm, sr in tts.synthesize(LINE, style, creds=creds, voice=voice):
        if first is None:
            first = time.perf_counter() - t0
        chunks += 1
        total += len(pcm)
        rate = sr
    end = time.perf_counter() - t0
    audio_s = total / (rate * 2) if rate else 0
    return {
        "first_ms": round(first * 1000) if first else None,
        "total_ms": round(end * 1000),
        "chunks": chunks,
        "audio_s": round(audio_s, 2),
        "rtf": round(end / audio_s, 2) if audio_s else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--creds", required=True)
    ap.add_argument("--runs", type=int, default=8)
    ap.add_argument("--style", default="gentle")
    ap.add_argument("--voice", default=None, help="音色 id，默认取 voices.json 的 B")
    args = ap.parse_args()

    rows = []
    for i in range(args.runs):
        r = once(args.creds, args.style, args.voice)
        rows.append(r)
        print(f"#{i + 1}  首包 {r['first_ms']}ms  整句 {r['total_ms']}ms  "
              f"音频 {r['audio_s']}s  {r['chunks']} 块  RTF {r['rtf']}")
    firsts = [r["first_ms"] for r in rows if r["first_ms"]]
    if firsts:
        firsts.sort()
        p95 = firsts[min(len(firsts) - 1, int(round(0.95 * (len(firsts) - 1))))]
        print(f"\n首包中位数 {round(statistics.median(firsts))}ms  "
              f"P95 {p95}ms  最快 {firsts[0]}ms  最慢 {firsts[-1]}ms")


if __name__ == "__main__":
    main()
