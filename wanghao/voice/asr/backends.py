"""Recognition backends for the streaming ASR wrapper.

Two backends are provided:

``fixture``
    Deterministic, standard-library only.  It is the default so the Flask
    integration, the browser page and the offline examples can run before a
    model is downloaded.  Any PCM block counts as speech (the stream opens a
    segment every ``max_segment_ms``), so silence fixtures produce transcripts.

``funasr``
    FunASR ``paraformer-zh-streaming`` adapter, the baseline in
    ``docs/demo-development-baseline.md``.  Imports of ``funasr``/``numpy``
    happen lazily; the model is shared by every session in the process and
    inference is serialised, which is enough for a single-process course demo.

The stream owns segmentation, ``input_id`` allocation, gaps and backlog.  A
backend only turns the PCM of one speech segment into interim/final text.
"""

from __future__ import annotations

import importlib.util
import os
import threading
from dataclasses import dataclass
from typing import Protocol, Sequence, runtime_checkable

from .errors import AsrUnavailable

FIXTURE_MODEL_VERSION = "fixture-only"
FUNASR_MODEL = "paraformer-zh-streaming"
FUNASR_MODEL_REVISION = "v2.0.4"


@runtime_checkable
class Backend(Protocol):
    """One recogniser shared by all speech segments of one stream."""

    name: str
    model_version: str

    def warmup(self) -> str:
        """Load whatever is needed and return the model version."""

    def reset(self) -> None:
        """Discard the state of the previous speech segment."""

    def accept(self, pcm: bytes) -> str:
        """Feed one complete 16 kHz s16le block; return the interim text."""

    def finish(self) -> str:
        """Finish the current segment and return its final text."""

    def close(self) -> None:
        """Release per-stream resources (shared models stay loaded)."""


@dataclass(frozen=True, slots=True)
class Utterance:
    """One scripted utterance for the fixture backend."""

    partials: tuple[str, ...]
    final: str


DEFAULT_SCRIPT: tuple[Utterance, ...] = (
    Utterance(("我想", "我想先说一件事"), "我想先说一件事。"),
    Utterance(("我有点乱", "我有点乱，想慢一点"), "我有点乱，想慢一点。"),
    Utterance(("那我们从", "那我们从上次停下的地方接着聊"), "那我们从上次停下的地方接着聊。"),
)


class FixtureBackend:
    """Deterministic transcripts for offline integration and tests."""

    name = "fixture"
    model_version = FIXTURE_MODEL_VERSION

    def __init__(
        self,
        script: Sequence[Utterance] | None = None,
        *,
        partial_every_blocks: int = 10,
    ) -> None:
        self._script = tuple(script) if script is not None else DEFAULT_SCRIPT
        self._cadence = max(1, int(partial_every_blocks))
        self._utterance_index = 0
        self._blocks = 0
        self._partial_index = 0

    def warmup(self) -> str:
        return self.model_version

    def reset(self) -> None:
        self._blocks = 0
        self._partial_index = 0

    def accept(self, pcm: bytes) -> str:
        if not self._script:
            return ""
        self._blocks += 1
        if self._blocks % self._cadence:
            return ""
        utterance = self._script[self._utterance_index % len(self._script)]
        if self._partial_index >= len(utterance.partials):
            return ""
        text = utterance.partials[self._partial_index]
        self._partial_index += 1
        return text

    def finish(self) -> str:
        if not self._script:
            return ""
        utterance = self._script[self._utterance_index % len(self._script)]
        self._utterance_index = (self._utterance_index + 1) % len(self._script)
        return utterance.final

    def close(self) -> None:
        return None


_MODEL_CACHE: dict[tuple[str, str, str], object] = {}
_MODEL_LOAD_LOCK = threading.Lock()
_MODEL_INFER_LOCK = threading.Lock()


def _shared_model(key: tuple[str, str, str], loader) -> object:
    with _MODEL_LOAD_LOCK:
        if key not in _MODEL_CACHE:
            _MODEL_CACHE[key] = loader()
        return _MODEL_CACHE[key]


def _missing_modules() -> list[str]:
    return [
        name
        for name in ("funasr", "numpy")
        if importlib.util.find_spec(name) is None
    ]


class FunAsrBackend:
    """Streaming Paraformer adapter (FunASR ``paraformer-zh-streaming``)."""

    name = "funasr"

    def __init__(
        self,
        *,
        model: str = FUNASR_MODEL,
        model_revision: str = FUNASR_MODEL_REVISION,
        device: str | None = None,
        chunk_size: tuple[int, int, int] = (0, 10, 5),
        encoder_chunk_look_back: int = 4,
        decoder_chunk_look_back: int = 1,
    ) -> None:
        missing = _missing_modules()
        if missing:
            raise AsrUnavailable(
                "FunASR 后端缺少依赖 "
                + ", ".join(missing)
                + "；按 requirements-asr.txt 安装，或先用 VOICE_ASR_BACKEND=fixture。",
                retryable=False,
                fatal=True,
            )
        self._model = model
        self._model_revision = model_revision
        self._device = device or os.environ.get("VOICE_ASR_DEVICE", "cpu")
        self._chunk_size = tuple(chunk_size)
        self._encoder_chunk_look_back = encoder_chunk_look_back
        self._decoder_chunk_look_back = decoder_chunk_look_back
        # FunASR's streaming example: chunk_size[1] * 960 samples at 16 kHz.
        self._chunk_samples = max(1, self._chunk_size[1] * 960)
        self._chunk_bytes = self._chunk_samples * 2
        self._loaded = None
        self._buffer = bytearray()
        self._cache: dict[str, object] = {}
        self._text = ""
        self._primed = False

    @property
    def model_version(self) -> str:
        return f"{self._model}@{self._model_revision}"

    def warmup(self) -> str:
        self._ensure_model()
        if not self._primed:
            # The first generate() pays lazy kernel/allocator setup (seconds on
            # MPS).  Do it here, before the session starts feeding, with one
            # chunk of silence so the first real utterance is not dropped by
            # the backlog guard.
            try:
                self._infer(b"\x00\x00" * self._chunk_samples, final=False)
            finally:
                self.reset()
            self._primed = True
        return self.model_version

    def _ensure_model(self) -> None:
        if self._loaded is not None:
            return
        key = (self._model, self._model_revision, self._device)

        def load():
            from funasr import AutoModel

            return AutoModel(
                model=self._model,
                model_revision=self._model_revision,
                device=self._device,
                disable_update=True,
                disable_pbar=True,
                log_level="ERROR",
            )

        try:
            self._loaded = _shared_model(key, load)
        except AsrUnavailable:
            raise
        except Exception as exc:  # model download / incompatible runtime
            raise AsrUnavailable(
                f"加载 FunASR 模型 {self.model_version} 失败：{exc}",
                retryable=False,
                fatal=True,
            ) from exc

    def reset(self) -> None:
        self._buffer.clear()
        self._cache.clear()
        self._text = ""

    def accept(self, pcm: bytes) -> str:
        self._ensure_model()
        self._buffer.extend(pcm)
        while len(self._buffer) >= self._chunk_bytes:
            chunk = bytes(self._buffer[: self._chunk_bytes])
            del self._buffer[: self._chunk_bytes]
            self._infer(chunk, final=False)
        return self._text

    def finish(self) -> str:
        if not self._buffer and not self._cache:
            return ""
        self._ensure_model()
        chunk = bytes(self._buffer)
        self._buffer.clear()
        self._infer(chunk, final=True)
        text = self._text
        self._text = ""
        return text

    def _infer(self, chunk: bytes, *, final: bool) -> None:
        import numpy as np

        samples = np.frombuffer(chunk, dtype="<i2").astype(np.float32) / 32768.0
        try:
            with _MODEL_INFER_LOCK:
                result = self._loaded.generate(
                    input=samples,
                    cache=self._cache,
                    is_final=final,
                    chunk_size=list(self._chunk_size),
                    encoder_chunk_look_back=self._encoder_chunk_look_back,
                    decoder_chunk_look_back=self._decoder_chunk_look_back,
                )
        except Exception as exc:  # a transient decode failure may be retried
            raise AsrUnavailable(
                f"FunASR 解码失败：{exc}", retryable=True, fatal=False
            ) from exc
        if not result:
            return
        part = (result[0].get("text") or "").strip()
        if not part:
            return
        # FunASR's streaming example concatenates per-chunk text, while some
        # builds return the whole sentence so far. Support both shapes.
        if part.startswith(self._text):
            self._text = part
        elif not self._text.endswith(part):
            self._text = f"{self._text}{part}"

    def close(self) -> None:
        self.reset()


def create_backend(name: str, **kwargs) -> Backend:
    """Build a backend by name (``fixture`` or ``funasr``)."""

    if name == "fixture":
        allowed = {"script", "partial_every_blocks"}
        unknown = set(kwargs) - allowed
        if unknown:
            raise TypeError(f"fixture backend does not accept {sorted(unknown)}")
        return FixtureBackend(**kwargs)
    if name == "funasr":
        return FunAsrBackend(**kwargs)
    raise ValueError(f"unknown ASR backend {name!r}; use 'fixture' or 'funasr'")


def warmup(backend: str | Backend | None = None, **kwargs) -> dict[str, str]:
    """Load a backend ahead of the first utterance (call from app startup)."""

    if isinstance(backend, str) or backend is None:
        name = backend or os.environ.get("VOICE_ASR_BACKEND", "fixture")
        backend = create_backend(name, **kwargs)
    version = backend.warmup()
    return {"backend": backend.name, "model_version": version}
