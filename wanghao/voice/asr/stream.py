"""Per-session streaming ASR wrapper.

Contract (``docs/weekend-modules.md`` section 3)::

    stream = open_stream(session_id, on_transcript)
    stream.feed(pcm, seq, t_ms)     # 16 kHz, mono, s16le, ordered blocks
    stream.close()

The wrapper owns what the contract assigns to the server: speech segmentation,
the server-side voice ``input_id``, interim/final delivery, sequence-gap
handling, the ~2 s upstream backlog limit and the ASR failure fallback.

Callbacks run on one worker thread, never on the WebSocket receive thread, so
``feed`` is non-blocking.  The integration layer must put the resulting
``transcript``/``session.error`` frames on its own send queue.
"""

from __future__ import annotations

import logging
import math
import os
import sys
import threading
import time
import uuid
from array import array
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Optional

from .backends import Backend, create_backend
from .errors import AsrError, AsrUnavailable

TranscriptCallback = Callable[[str, str, bool], None]
SpeechCallback = Callable[[bool, int, float], None]
ErrorCallback = Callable[[str, str, bool], None]

SAMPLE_RATE = 16000
AUDIO_GAP = "AUDIO_GAP"
ASR_UNAVAILABLE = "ASR_UNAVAILABLE"

GAP_MESSAGE = "音频序号不连续，已重新开始这一段，请再说一次。"
BACKLOG_MESSAGE = "音频积压超过 2 秒，已丢弃未完成的这一段，请再说一次。"
UNAVAILABLE_MESSAGE = "语音识别暂时不可用，请改用文字输入。"


@dataclass(slots=True)
class _Block:
    pcm: bytes
    seq: int
    t_ms: int
    samples: int


@dataclass(slots=True)
class _Segment:
    input_id: str
    samples: int = 0


def _rms_level(pcm: bytes) -> float:
    """Normalised RMS of a little-endian 16-bit PCM block (0.0–1.0)."""

    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder == "big":
        samples.byteswap()
    if not samples:
        return 0.0
    energy = 0
    for value in samples:
        energy += value * value
    return min(1.0, math.sqrt(energy / len(samples)) / 32768.0)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class AsrStream:
    """One ASR stream per logical WebSocket session (survives reconnects)."""

    def __init__(
        self,
        session_id: str,
        on_transcript: TranscriptCallback,
        *,
        backend: Backend,
        on_speech: Optional[SpeechCallback] = None,
        on_error: Optional[ErrorCallback] = None,
        sample_rate: int = SAMPLE_RATE,
        vad_threshold: float = 0.01,
        min_speech_ms: int = 120,
        pre_roll_ms: int = 150,
        end_silence_ms: int = 700,
        max_segment_ms: int = 20000,
        max_backlog_ms: int = 2000,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id must be a non-empty string")
        if not callable(on_transcript):
            raise TypeError("on_transcript must be callable")
        if sample_rate != SAMPLE_RATE:
            raise ValueError("only 16000 Hz mono PCM is supported")
        if not 0.0 <= vad_threshold <= 1.0:
            raise ValueError("vad_threshold must be in [0, 1]")
        for name, value in (
            ("min_speech_ms", min_speech_ms),
            ("pre_roll_ms", pre_roll_ms),
            ("end_silence_ms", end_silence_ms),
            ("max_segment_ms", max_segment_ms),
            ("max_backlog_ms", max_backlog_ms),
        ):
            if not _is_int(value) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if pre_roll_ms > max_segment_ms:
            raise ValueError("pre_roll_ms must not exceed max_segment_ms")

        self.session_id = session_id
        self.backend_name = backend.name
        self.model_version = backend.model_version
        self.sample_rate = sample_rate
        self._backend = backend
        self._on_transcript = on_transcript
        self._on_speech = on_speech
        self._on_error = on_error
        self._logger = logger or logging.getLogger("voice.asr")

        self._vad_threshold = float(vad_threshold)
        self._min_speech_samples = sample_rate * min_speech_ms // 1000
        self._pre_roll_samples_limit = sample_rate * pre_roll_ms // 1000
        self._end_silence_samples = sample_rate * end_silence_ms // 1000
        self._max_segment_samples = sample_rate * max_segment_ms // 1000
        self._max_backlog_samples = sample_rate * max_backlog_ms // 1000

        self._cond = threading.Condition()
        self._stats_lock = threading.Lock()
        self._pending: Deque[_Block] = deque()
        self._pending_samples = 0
        self._busy = False
        self._closed = False
        self._backlog_gap = False

        self._clock_samples = 0
        self._last_seq: Optional[int] = None
        self._last_t_ms = 0
        self._segment: Optional[_Segment] = None
        self._speech_run_samples = 0
        self._speech_onset_t_ms = 0
        self._pre_roll: Deque[_Block] = deque()
        self._pre_roll_samples = 0
        self._last_voice_sample = 0
        self._partial_text = ""
        self._available = True
        self._disabled = False
        self._failure_reported = False
        self._stats = {
            "blocks": 0,
            "dropped_blocks": 0,
            "segments": 0,
            "partials": 0,
            "finals": 0,
            "errors": 0,
        }

        self._worker = threading.Thread(
            target=self._run,
            name=f"asr-{session_id}",
            daemon=True,
        )
        self._worker.start()

    # ------------------------------------------------------------------ public

    def feed(self, pcm: bytes, seq: int, t_ms: int) -> None:
        """Queue one ordered PCM block (non-blocking).

        ``pcm`` must contain complete 16-bit samples.  A block may be any
        length up to the 2 s backlog limit; the browser normally sends 20 ms.
        """

        if not isinstance(pcm, (bytes, bytearray, memoryview)):
            raise TypeError("pcm must be bytes-like")
        data = bytes(pcm)
        if not _is_int(seq) or seq < 0:
            raise ValueError("seq must be a non-negative integer")
        if not _is_int(t_ms) or t_ms < 0:
            raise ValueError("t_ms must be a non-negative integer")
        if not data or len(data) % 2:
            raise ValueError("pcm must contain complete 16-bit samples")
        samples = len(data) // 2
        if samples > self._max_backlog_samples:
            raise ValueError("single block exceeds the upstream backlog limit")

        with self._cond:
            if self._closed:
                raise RuntimeError("ASR stream is closed")
            if self._pending_samples + samples > self._max_backlog_samples:
                dropped = len(self._pending)
                self._pending.clear()
                self._pending_samples = 0
                self._backlog_gap = True
                self._bump("dropped_blocks", dropped)
            self._pending.append(_Block(data, seq, t_ms, samples))
            self._pending_samples += samples
            self._cond.notify()

    def flush(self, timeout: Optional[float] = None) -> bool:
        """Wait until queued blocks have been processed.

        Returns ``False`` on timeout.  ``flush`` does not end the current
        speech segment; keep feeding silence if a final transcript is needed.
        """

        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            while self._pending or self._busy:
                if self._closed:
                    return not (self._pending or self._busy)
                if deadline is None:
                    self._cond.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cond.wait(remaining)
            return True

    def close(self) -> None:
        """Discard pending audio and stop the worker; safe to call twice."""

        with self._cond:
            if self._closed:
                return
            self._closed = True
            self._pending.clear()
            self._pending_samples = 0
            self._cond.notify_all()
        self._worker.join(timeout=2.0)
        if self._worker.is_alive():
            self._logger.warning(
                "ASR worker for session %s did not stop in 2 s", self.session_id
            )
            return
        try:
            self._backend.close()
        except Exception:  # pragma: no cover - defensive
            self._logger.exception("closing ASR backend failed")

    @property
    def available(self) -> bool:
        return self._available

    @property
    def stats(self) -> dict[str, object]:
        with self._cond:
            pending = len(self._pending)
        with self._stats_lock:
            snapshot: dict[str, object] = dict(self._stats)
        snapshot["pending_blocks"] = pending
        snapshot["backend"] = self.backend_name
        snapshot["model_version"] = self.model_version
        snapshot["available"] = self._available
        return snapshot

    def _bump(self, key: str, amount: int = 1) -> None:
        with self._stats_lock:
            self._stats[key] += amount

    def __enter__(self) -> "AsrStream":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------ worker

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._pending and not self._closed:
                    self._cond.wait()
                if not self._pending and self._closed:
                    return
                block = self._pending.popleft()
                self._pending_samples -= block.samples
                backlog_gap = self._backlog_gap
                self._backlog_gap = False
                self._busy = True
            try:
                self._handle(block, backlog_gap)
            except Exception:
                self._logger.exception("ASR worker failed for %s", self.session_id)
                self._disable("语音识别线程异常，请改用文字输入。")
            finally:
                with self._cond:
                    self._busy = False
                    self._cond.notify_all()

    def _handle(self, block: _Block, backlog_gap: bool) -> None:
        if self._disabled:
            return

        if self._last_seq is not None and block.seq <= self._last_seq:
            return  # duplicate or stale block: ignore, per the wire contract
        if backlog_gap:
            if self._last_seq is not None and block.seq == self._last_seq + 1:
                # Only duplicate/stale blocks were dropped; no audio was lost.
                backlog_gap = False
            else:
                self._reset_segment()
                self._emit_error(AUDIO_GAP, BACKLOG_MESSAGE, True)
        elif self._last_seq is not None and block.seq != self._last_seq + 1:
            self._reset_segment()
            self._emit_error(AUDIO_GAP, GAP_MESSAGE, True)
        self._last_seq = block.seq

        self._bump("blocks")
        self._clock_samples += block.samples
        self._last_t_ms = block.t_ms
        level = _rms_level(block.pcm)
        voiced = level >= self._vad_threshold

        if self._segment is None:
            self._push_pre_roll(block)
            if not voiced:
                self._speech_run_samples = 0
                return
            if self._speech_run_samples == 0:
                self._speech_onset_t_ms = block.t_ms
            self._speech_run_samples += block.samples
            if self._speech_run_samples >= self._min_speech_samples:
                self._activate(block, level)
            return

        self._accept(block)
        if self._segment is None:  # the backend failed and reset the segment
            return
        self._segment.samples += block.samples
        if voiced:
            self._last_voice_sample = self._clock_samples
        if (
            self._segment.samples >= self._max_segment_samples
            or self._clock_samples - self._last_voice_sample >= self._end_silence_samples
        ):
            self._finalize(block.t_ms)

    def _push_pre_roll(self, block: _Block) -> None:
        self._pre_roll.append(block)
        self._pre_roll_samples += block.samples
        while (
            self._pre_roll
            and self._pre_roll_samples - self._pre_roll[0].samples
            >= self._pre_roll_samples_limit
        ):
            oldest = self._pre_roll.popleft()
            self._pre_roll_samples -= oldest.samples

    def _activate(self, block: _Block, level: float) -> None:
        self._segment = _Segment(input_id=self._new_input_id())
        self._partial_text = ""
        self._speech_run_samples = 0
        self._bump("segments")
        try:
            self._backend.reset()
        except AsrError as exc:
            self._backend_failed(exc)
            return
        except Exception as exc:
            self._backend_failed(
                AsrUnavailable(f"识别后端重置失败：{exc}", retryable=True, fatal=False)
            )
            return
        self._emit_speech(True, self._speech_onset_t_ms, level)
        self._last_voice_sample = self._clock_samples
        pre_roll = list(self._pre_roll)
        self._pre_roll.clear()
        self._pre_roll_samples = 0
        for item in pre_roll:
            if self._disabled or self._segment is None:
                return
            self._accept(item)

    def _accept(self, block: _Block) -> None:
        try:
            text = self._backend.accept(block.pcm)
        except AsrError as exc:
            self._backend_failed(exc)
            return
        except Exception as exc:
            self._backend_failed(
                AsrUnavailable(f"识别后端异常：{exc}", retryable=True, fatal=False)
            )
            return
        text = (text or "").strip()
        if not text or text == self._partial_text:
            return
        self._partial_text = text
        self._bump("partials")
        self._emit_transcript(text, False)

    def _finalize(self, t_ms: int) -> None:
        segment = self._segment
        if segment is None:
            return
        try:
            text = (self._backend.finish() or "").strip()
        except AsrError as exc:
            self._backend_failed(exc)
            return
        except Exception as exc:
            self._backend_failed(
                AsrUnavailable(f"识别后端异常：{exc}", retryable=True, fatal=False)
            )
            return
        if text:
            self._bump("finals")
            self._emit_transcript(text, True)
        self._emit_speech(False, t_ms, 0.0)
        self._reset_segment(notify_speech=False)

    def _reset_segment(self, *, notify_speech: bool = True) -> None:
        if self._segment is not None and notify_speech:
            self._emit_speech(False, self._last_t_ms, 0.0)
        self._segment = None
        self._partial_text = ""
        self._speech_run_samples = 0
        self._speech_onset_t_ms = 0
        self._pre_roll.clear()
        self._pre_roll_samples = 0
        # Reset is best-effort here: a broken backend must not recurse into
        # _backend_failed -> _reset_segment.
        try:
            self._backend.reset()
        except Exception:
            self._logger.exception("resetting ASR backend failed")

    def _backend_failed(self, exc: AsrError) -> None:
        self._reset_segment()
        self._bump("errors")
        if exc.fatal:
            self._disable(f"{UNAVAILABLE_MESSAGE}（{exc}）")
            return
        if not self._failure_reported:
            self._failure_reported = True
            self._emit_error(
                ASR_UNAVAILABLE,
                f"{UNAVAILABLE_MESSAGE}（{exc}）",
                exc.retryable,
            )

    def _disable(self, message: str) -> None:
        if self._disabled:
            return
        self._disabled = True
        self._available = False
        self._bump("errors")
        self._emit_error(ASR_UNAVAILABLE, message, False)

    def _new_input_id(self) -> str:
        # "A" keeps server-allocated voice IDs distinct from browser text IDs.
        return "A" + uuid.uuid4().hex[:16]

    # --------------------------------------------------------------- callbacks

    def _emit_transcript(self, text: str, is_final: bool) -> None:
        segment = self._segment
        if segment is None or not text:
            return
        try:
            self._on_transcript(segment.input_id, text, is_final)
        except Exception:
            self._logger.exception("on_transcript callback failed")

    def _emit_speech(self, active: bool, t_ms: int, level: float) -> None:
        if self._on_speech is None:
            return
        try:
            self._on_speech(active, int(t_ms), max(0.0, min(1.0, float(level))))
        except Exception:
            self._logger.exception("on_speech callback failed")

    def _emit_error(self, code: str, message: str, retryable: bool) -> None:
        if self._on_error is None:
            self._logger.warning("%s: %s", code, message)
            return
        try:
            self._on_error(code, message, retryable)
        except Exception:
            self._logger.exception("on_error callback failed")


def open_stream(
    session_id: str,
    on_transcript: TranscriptCallback,
    *,
    backend: str | Backend | None = None,
    on_speech: Optional[SpeechCallback] = None,
    on_error: Optional[ErrorCallback] = None,
    sample_rate: int = SAMPLE_RATE,
    vad_threshold: Optional[float] = None,
    min_speech_ms: int = 120,
    pre_roll_ms: int = 150,
    end_silence_ms: int = 700,
    max_segment_ms: Optional[int] = None,
    max_backlog_ms: int = 2000,
    partial_every_blocks: int = 10,
    logger: Optional[logging.Logger] = None,
) -> AsrStream:
    """Open the server-side ASR stream for one logical session.

    ``backend`` may be a backend name or an already-built backend.  Without an
    explicit value, ``VOICE_ASR_BACKEND`` is used and defaults to ``fixture``.
    The fixture treats every block as speech and cuts a segment every 2 s, so
    silent integration fixtures still exercise the full interim/final path.
    """

    backend_spec = backend
    if backend_spec is None or isinstance(backend_spec, str):
        name = backend_spec or os.environ.get("VOICE_ASR_BACKEND", "fixture")
        if name == "fixture":
            backend_obj = create_backend(
                name, partial_every_blocks=partial_every_blocks
            )
            if vad_threshold is None:
                vad_threshold = 0.0
            if max_segment_ms is None:
                max_segment_ms = 2000
        else:
            backend_obj = create_backend(name)
    else:
        backend_obj = backend_spec

    if vad_threshold is None:
        vad_threshold = 0.01
    if max_segment_ms is None:
        max_segment_ms = 20000

    return AsrStream(
        session_id,
        on_transcript,
        backend=backend_obj,
        on_speech=on_speech,
        on_error=on_error,
        sample_rate=sample_rate,
        vad_threshold=vad_threshold,
        min_speech_ms=min_speech_ms,
        pre_roll_ms=pre_roll_ms,
        end_silence_ms=end_silence_ms,
        max_segment_ms=max_segment_ms,
        max_backlog_ms=max_backlog_ms,
        logger=logger,
    )
