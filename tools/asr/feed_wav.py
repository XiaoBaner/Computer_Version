#!/usr/bin/env python3
"""Feed a 16 kHz mono 16-bit WAV through voice.asr without Flask.

Examples::

    python3 tools/asr/feed_wav.py --wav sample.wav                 # fixture backend
    VOICE_ASR_BACKEND=funasr python3 tools/asr/feed_wav.py --wav sample.wav
    python3 tools/asr/feed_wav.py --wav sample.wav --realtime      # 20 ms pacing

The tool prints the ``transcript`` / ``session.error`` frames that the Flask
integration would put on its WebSocket send queue.  It appends silence after
the file so the last speech segment reaches its endpoint before shutdown.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from voice.asr import AsrUnavailable, open_stream, warmup  # noqa: E402


def _fail(message: str, code: int = 2) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def _read_wav(path: Path) -> bytes:
    try:
        with wave.open(str(path), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            compression = handle.getcomptype()
            frames = handle.readframes(handle.getnframes())
    except (OSError, wave.Error) as exc:
        _fail(f"无法读取 WAV：{exc}")
    if (channels, width, rate, compression) != (1, 2, 16000, "NONE"):
        _fail(
            "需要 16 kHz、单声道、16-bit PCM WAV；"
            f"当前为 {rate} Hz / {channels} 声道 / {width * 8}-bit / {compression}。\n"
            "转换示例：\n"
            "  afconvert -f WAVE -d LEI16@16000 -c 1 input.aiff output.wav\n"
            "  ffmpeg -i input.wav -ar 16000 -ac 1 -c:a pcm_s16le output.wav"
        )
    return frames


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wav", type=Path, required=True)
    parser.add_argument(
        "--backend",
        choices=("fixture", "funasr"),
        help="default: VOICE_ASR_BACKEND or fixture",
    )
    parser.add_argument("--session-id", default="S1")
    parser.add_argument("--block-ms", type=int, default=20)
    parser.add_argument(
        "--vad-threshold",
        type=float,
        default=None,
        help="normalised RMS threshold; fixture defaults to 0 (everything is speech)",
    )
    parser.add_argument(
        "--tail-silence-ms",
        type=int,
        default=2200,
        help="silence appended so the final segment can close (default: 2200)",
    )
    parser.add_argument(
        "--realtime",
        action="store_true",
        help="sleep one block duration per block and keep the production 2 s backlog cap",
    )
    parser.add_argument(
        "--max-backlog-ms",
        type=int,
        default=0,
        help="0 (default) fits the whole file in fast mode; --realtime keeps 2000 ms",
    )
    parser.add_argument("--timeout", type=float, default=120.0, help="flush timeout")
    parser.add_argument(
        "--require-transcript",
        action="store_true",
        help="exit 1 when no final transcript was produced",
    )
    args = parser.parse_args()

    if args.block_ms <= 0:
        _fail("--block-ms must be positive")
    if args.tail_silence_ms < 0:
        _fail("--tail-silence-ms must not be negative")
    block_bytes = 16000 * args.block_ms // 1000 * 2
    frames = _read_wav(args.wav)
    if len(frames) % 2:
        _fail("WAV 数据不是完整的 16-bit 采样")

    transcripts: list[tuple[str, str, bool]] = []
    errors: list[tuple[str, str, bool]] = []
    speech: list[tuple[bool, int, float]] = []
    timing = {"onset": None, "first_partial_ms": None, "first_final_ms": None}

    def on_transcript(input_id: str, text: str, is_final: bool) -> None:
        transcripts.append((input_id, text, is_final))
        if timing["onset"] is not None:
            elapsed_ms = (time.monotonic() - timing["onset"]) * 1000
            key = "first_final_ms" if is_final else "first_partial_ms"
            if timing[key] is None:
                timing[key] = round(elapsed_ms, 1)
        frame = {
            "v": 1,
            "type": "transcript",
            "session_id": args.session_id,
            "input_id": input_id,
            "text": text,
            "is_final": is_final,
        }
        print(json.dumps(frame, ensure_ascii=False), flush=True)

    def on_error(code: str, message: str, retryable: bool) -> None:
        errors.append((code, message, retryable))
        frame = {
            "v": 1,
            "type": "session.error",
            "session_id": args.session_id,
            "code": code,
            "message": message,
            "retryable": retryable,
        }
        print(json.dumps(frame, ensure_ascii=False), flush=True)

    def on_speech(active: bool, t_ms: int, level: float) -> None:
        speech.append((active, t_ms, level))
        if active and timing["onset"] is None:
            timing["onset"] = time.monotonic()

    audio_ms = len(frames) // 2 * 1000 // 16000
    if args.max_backlog_ms > 0:
        backlog_ms = args.max_backlog_ms
    elif args.realtime:
        backlog_ms = 2000
    else:
        backlog_ms = max(2000, audio_ms + args.tail_silence_ms + 1000)

    backend_name = args.backend or os.environ.get("VOICE_ASR_BACKEND", "fixture")
    if backend_name == "funasr":
        # Load before feeding: otherwise the first utterance waits for the
        # 881 MB checkpoint and the 2 s backlog guard drops it.
        print("# warming up funasr ...", file=sys.stderr)
        try:
            print(f"# {warmup('funasr')}", file=sys.stderr)
        except AsrUnavailable as exc:
            _fail(f"ASR 后端不可用：{exc}")

    try:
        stream = open_stream(
            args.session_id,
            on_transcript,
            backend=args.backend,
            on_speech=on_speech,
            on_error=on_error,
            max_backlog_ms=backlog_ms,
            vad_threshold=args.vad_threshold,
        )
    except AsrUnavailable as exc:
        _fail(f"ASR 后端不可用：{exc}")

    print(
        f"# backend={stream.backend_name} model={stream.model_version} "
        f"audio={len(frames) / 32000:.2f}s tail={args.tail_silence_ms}ms",
        file=sys.stderr,
    )

    started = time.monotonic()
    seq = 0
    t_ms = 0
    offset = 0
    try:
        so_far = 0
        while offset < len(frames) or so_far < args.tail_silence_ms:
            if offset < len(frames):
                chunk = frames[offset : offset + block_bytes]
                offset += len(chunk)
            else:
                chunk = b"\x00" * block_bytes
            if not chunk:
                break
            if len(chunk) % 2:
                chunk += b"\x00"
            stream.feed(chunk, seq, t_ms)
            seq += 1
            samples = len(chunk) // 2
            t_ms += samples * 1000 // 16000
            if offset >= len(frames):
                so_far += samples * 1000 // 16000
            if args.realtime:
                time.sleep(args.block_ms / 1000.0)

        if not stream.flush(args.timeout):
            _fail("ASR 处理超时，仍有音频未消费", code=1)
    finally:
        stream.close()

    finals = [item for item in transcripts if item[2]]
    elapsed = time.monotonic() - started
    audio_seconds = (len(frames) // 2) / 16000
    processed_seconds = audio_seconds + args.tail_silence_ms / 1000
    timing_out = {
        key: value for key, value in timing.items() if key != "onset"
    }
    print(
        f"# finals={len(finals)} errors={len(errors)} speech_events={len(speech)} "
        f"elapsed={elapsed:.2f}s audio={audio_seconds:.2f}s "
        # In --realtime the 20 ms pacing dominates wall clock, so call this a
        # wall ratio rather than inference RTF; fast mode is a throughput run.
        f"wall_ratio={elapsed / processed_seconds if processed_seconds else 0:.2f} "
        f"timing_ms={json.dumps(timing_out, ensure_ascii=False)} "
        f"stats={json.dumps(stream.stats, ensure_ascii=False)}",
        file=sys.stderr,
    )
    if errors and any(code == "ASR_UNAVAILABLE" for code, _, _ in errors):
        return 1
    if args.require_transcript and not finals:
        print("# no final transcript was produced", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
