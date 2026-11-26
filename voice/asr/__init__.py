"""Streaming ASR entry point (docs/weekend-modules.md section 3)."""

from .backends import (
    DEFAULT_SCRIPT,
    FIXTURE_MODEL_VERSION,
    FUNASR_MODEL,
    FUNASR_MODEL_REVISION,
    Backend,
    FixtureBackend,
    FunAsrBackend,
    Utterance,
    create_backend,
    warmup,
)
from .errors import AsrError, AsrUnavailable
from .stream import (
    ASR_UNAVAILABLE,
    AUDIO_GAP,
    SAMPLE_RATE,
    AsrStream,
    open_stream,
)

__all__ = [
    "ASR_UNAVAILABLE",
    "AUDIO_GAP",
    "AsrError",
    "AsrStream",
    "AsrUnavailable",
    "Backend",
    "DEFAULT_SCRIPT",
    "FIXTURE_MODEL_VERSION",
    "FUNASR_MODEL",
    "FUNASR_MODEL_REVISION",
    "FixtureBackend",
    "FunAsrBackend",
    "SAMPLE_RATE",
    "Utterance",
    "create_backend",
    "open_stream",
    "warmup",
]
