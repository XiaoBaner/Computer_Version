"""Contract and behaviour tests for voice/asr (stdlib + pytest only)."""

from __future__ import annotations

import importlib.util
import json
import struct
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]  # wanghao/tests/voice → 仓库根
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from replay_contract import validate_message  # noqa: E402
from wanghao.voice.asr import AsrUnavailable, create_backend, open_stream  # noqa: E402
from wanghao.voice.asr.errors import AsrError  # noqa: E402

CATALOG = json.loads((ROOT / "contracts" / "events.v1.json").read_text("utf-8"))


def pcm(ms: int, amplitude: int = 0) -> bytes:
    samples = 16000 * ms // 1000
    return struct.pack("<h", amplitude) * samples


class Events:
    def __init__(self) -> None:
        self.condition = threading.Condition()
        self.transcripts: list[tuple[str, str, bool]] = []
        self.speech: list[tuple[bool, int, float]] = []
        self.errors: list[tuple[str, str, bool]] = []

    def on_transcript(self, input_id: str, text: str, is_final: bool) -> None:
        with self.condition:
            self.transcripts.append((input_id, text, is_final))
            self.condition.notify_all()

    def on_speech(self, active: bool, t_ms: int, level: float) -> None:
        with self.condition:
            self.speech.append((active, t_ms, level))
            self.condition.notify_all()

    def on_error(self, code: str, message: str, retryable: bool) -> None:
        with self.condition:
            self.errors.append((code, message, retryable))
            self.condition.notify_all()

    def wait_for(self, predicate, timeout: float = 3.0) -> bool:
        deadline = time.monotonic() + timeout
        with self.condition:
            while not predicate(self):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.condition.wait(remaining)
            return True

    def snapshot(self):
        with self.condition:
            return (
                list(self.transcripts),
                list(self.speech),
                list(self.errors),
            )


def feed_blocks(stream, start_seq: int, count: int, ms: int = 20, amplitude: int = 0):
    seq = start_seq
    t_ms = start_seq * ms
    for _ in range(count):
        stream.feed(pcm(ms, amplitude), seq, t_ms)
        seq += 1
        t_ms += ms
    return seq, t_ms


def test_fixture_emits_interim_then_one_final_with_valid_frames():
    events = Events()
    with open_stream(
        "S1",
        events.on_transcript,
        backend="fixture",
        min_speech_ms=60,
        pre_roll_ms=60,
        max_segment_ms=600,
        partial_every_blocks=5,
    ) as stream:
        feed_blocks(stream, 0, 35)  # 700 ms of "speech" (fixture VAD is open)
        assert stream.flush(3.0)
        assert events.wait_for(lambda e: any(final for _, _, final in e.transcripts))

        transcripts, _, errors = events.snapshot()
        finals = [item for item in transcripts if item[2]]
        assert len(finals) == 1
        final_id, final_text, _ = finals[0]
        assert final_text == "我想先说一件事。"
        assert final_id.startswith("A")
        assert any(
            input_id == final_id and not is_final and text
            for input_id, text, is_final in transcripts
        )
        assert not errors

        for input_id, text, is_final in transcripts:
            frame = {
                "v": 1,
                "type": "transcript",
                "session_id": "S1",
                "input_id": input_id,
                "text": text,
                "is_final": is_final,
            }
            validate_message("server", frame, CATALOG)

        assert stream.backend_name == "fixture"
        assert stream.model_version == "fixture-only"
        assert stream.stats["finals"] == 1


def test_energy_vad_splits_segments_and_allocates_new_input_ids():
    events = Events()
    with open_stream(
        "S1",
        events.on_transcript,
        backend="fixture",
        vad_threshold=0.01,
        min_speech_ms=60,
        pre_roll_ms=60,
        end_silence_ms=200,
        max_segment_ms=5000,
        partial_every_blocks=5,
    ) as stream:
        seq, t_ms = feed_blocks(stream, 0, 25, amplitude=3000)  # 500 ms voiced
        feed_blocks(stream, seq, 30)  # 600 ms silence -> endpoint
        assert stream.flush(3.0)
        assert events.wait_for(lambda e: len([t for t in e.transcripts if t[2]]) == 1)

        start = events.snapshot()[0][-1][0]
        # Keep the same seq numbering the browser would produce (seq continues).
        seq = 55
        t_ms = 55 * 20
        for _ in range(25):
            stream.feed(pcm(20, 3000), seq, t_ms)
            seq += 1
            t_ms += 20
        for _ in range(30):
            stream.feed(pcm(20, 0), seq, t_ms)
            seq += 1
            t_ms += 20
        assert stream.flush(3.0)
        assert events.wait_for(lambda e: len([t for t in e.transcripts if t[2]]) == 2)

        transcripts, _, _ = events.snapshot()
        finals = [item for item in transcripts if item[2]]
        assert [item[1] for item in finals] == [
            "我想先说一件事。",
            "我有点乱，想慢一点。",
        ]
        assert finals[0][0] != finals[1][0]
        assert start != finals[1][0]


def test_sequence_gap_resets_segment_and_reports_audio_gap():
    events = Events()
    with open_stream(
        "S1",
        events.on_transcript,
        on_error=events.on_error,
        backend="fixture",
        max_segment_ms=5000,
        partial_every_blocks=5,
        min_speech_ms=60,
    ) as stream:
        feed_blocks(stream, 0, 10)
        assert stream.flush(3.0)
        assert events.wait_for(lambda e: bool(e.transcripts))
        first_id = events.snapshot()[0][0][0]

        feed_blocks(stream, 20, 10)  # seq jumps from 9 to 20
        assert stream.flush(3.0)
        assert events.wait_for(lambda e: bool(e.errors))
        assert events.wait_for(
            lambda e: any(t[0] != first_id for t in e.transcripts)
        )

        _, _, errors = events.snapshot()
        assert errors[0][0] == "AUDIO_GAP"
        assert errors[0][2] is True
        assert any(t[0] != first_id for t in events.snapshot()[0])
        code, message, retryable = errors[0]
        validate_message(
            "server",
            {
                "v": 1,
                "type": "session.error",
                "session_id": "S1",
                "code": code,
                "message": message,
                "retryable": retryable,
            },
            CATALOG,
        )


def test_duplicate_and_stale_sequence_numbers_are_ignored():
    events = Events()
    with open_stream(
        "S1",
        events.on_transcript,
        backend="fixture",
        max_segment_ms=5000,
        partial_every_blocks=5,
        min_speech_ms=60,
    ) as stream:
        feed_blocks(stream, 0, 15)
        assert stream.flush(3.0)
        before = events.snapshot()

        stream.feed(pcm(20), 14, 14 * 20)
        stream.feed(pcm(20), 3, 3 * 20)
        assert stream.flush(3.0)
        assert events.snapshot()[0] == before[0]
        assert events.snapshot()[2] == before[2]


class GatedBackend:
    name = "gated"
    model_version = "test-gated"

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def warmup(self) -> str:
        return self.model_version

    def reset(self) -> None:
        return None

    def accept(self, pcm: bytes) -> str:
        self.entered.set()
        self.release.wait(5.0)
        return ""

    def finish(self) -> str:
        return "结束。"

    def close(self) -> None:
        return None


def test_backlog_drops_pending_audio_without_blocking_feed():
    events = Events()
    backend = GatedBackend()
    stream = open_stream(
        "S1",
        events.on_transcript,
        on_error=events.on_error,
        backend=backend,
        vad_threshold=0.0,
        min_speech_ms=20,
        pre_roll_ms=20,
        max_segment_ms=10000,
        max_backlog_ms=200,
    )
    try:
        stream.feed(pcm(20), 0, 0)
        assert backend.entered.wait(2.0)
        assert stream.flush(0.05) is False  # worker is busy

        started = time.monotonic()
        seq, t_ms = feed_blocks(stream, 1, 40)  # 800 ms: far beyond the 200 ms cap
        assert time.monotonic() - started < 1.0  # feed never blocks on inference

        backend.release.set()
        assert stream.flush(3.0)
        assert events.wait_for(lambda e: bool(e.errors))
        _, _, errors = events.snapshot()
        assert errors[0][0] == "AUDIO_GAP"
        assert "积压" in errors[0][1]
        assert stream.stats["dropped_blocks"] > 0
    finally:
        backend.release.set()
        stream.close()


class FailingBackend:
    name = "failing"
    model_version = "test-failing"

    def warmup(self) -> str:
        return self.model_version

    def reset(self) -> None:
        return None

    def accept(self, pcm: bytes) -> str:
        raise AsrUnavailable("模型崩溃", retryable=False, fatal=True)

    def finish(self) -> str:
        return ""

    def close(self) -> None:
        return None


def test_fatal_backend_failure_disables_stream_but_stays_quiet():
    events = Events()
    with open_stream(
        "S1",
        events.on_transcript,
        on_error=events.on_error,
        backend=FailingBackend(),
        vad_threshold=0.0,
        min_speech_ms=20,
        pre_roll_ms=20,
        max_segment_ms=1000,
    ) as stream:
        feed_blocks(stream, 0, 10)
        assert stream.flush(3.0)
        assert events.wait_for(lambda e: bool(e.errors))
        assert events.wait_for(lambda e: stream.available is False)
        assert events.snapshot()[2][0][0] == "ASR_UNAVAILABLE"
        assert stream.stats["available"] is False

        feed_blocks(stream, 10, 20)  # ignored, but must not raise or emit more
        assert stream.flush(3.0)
        assert len(events.snapshot()[2]) == 1
        assert not events.snapshot()[0]


class FlakyBackend:
    name = "flaky"
    model_version = "test-flaky"

    def __init__(self) -> None:
        self.calls = 0

    def warmup(self) -> str:
        return self.model_version

    def reset(self) -> None:
        return None

    def accept(self, pcm: bytes) -> str:
        self.calls += 1
        if self.calls == 1:
            raise AsrError("暂时超时", retryable=True)
        return "我在"

    def finish(self) -> str:
        return "我在听。"

    def close(self) -> None:
        return None


def test_retryable_failure_reports_once_and_recovers_on_next_segment():
    events = Events()
    with open_stream(
        "S1",
        events.on_transcript,
        on_error=events.on_error,
        backend=FlakyBackend(),
        vad_threshold=0.0,
        min_speech_ms=40,
        pre_roll_ms=40,
        max_segment_ms=400,
    ) as stream:
        feed_blocks(stream, 0, 10)
        assert stream.flush(3.0)
        assert events.wait_for(lambda e: bool(e.errors))
        assert stream.available is True

        feed_blocks(stream, 10, 30)
        assert stream.flush(3.0)
        assert events.wait_for(lambda e: any(t[2] for t in e.transcripts))
        transcripts, _, errors = events.snapshot()
        assert len(errors) == 1 and errors[0][0] == "ASR_UNAVAILABLE"
        assert any(text == "我在听。" and is_final for _, text, is_final in transcripts)


def test_on_speech_reports_onset_and_end_of_segment():
    events = Events()
    with open_stream(
        "S1",
        events.on_transcript,
        on_speech=events.on_speech,
        backend="fixture",
        vad_threshold=0.01,
        min_speech_ms=60,
        pre_roll_ms=60,
        end_silence_ms=200,
        max_segment_ms=5000,
    ) as stream:
        seq, t_ms = feed_blocks(stream, 0, 15, amplitude=3000)
        feed_blocks(stream, seq, 20)
        assert stream.flush(3.0)
        assert events.wait_for(lambda e: any(not active for active, _, _ in e.speech))
        states = [active for active, _, _ in events.snapshot()[1]]
        assert states[0] is True
        assert states[-1] is False
        assert all(0.0 <= level <= 1.0 for _, _, level in events.snapshot()[1])


def test_input_validation_and_close_semantics():
    events = Events()
    with pytest.raises(ValueError):
        open_stream("S1", events.on_transcript, sample_rate=8000)
    with pytest.raises(ValueError):
        open_stream("", events.on_transcript)
    with pytest.raises(TypeError):
        open_stream("S1", None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        open_stream("S1", events.on_transcript, backend="not-a-backend")

    stream = open_stream("S1", events.on_transcript, backend="fixture")
    try:
        with pytest.raises(ValueError):
            stream.feed(b"\x00", 0, 0)
        with pytest.raises(ValueError):
            stream.feed(b"", 0, 0)
        with pytest.raises(TypeError):
            stream.feed("not-bytes", 0, 0)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            stream.feed(pcm(20), -1, 0)
        with pytest.raises(ValueError):
            stream.feed(pcm(20), 0, -1)
        with pytest.raises(ValueError):
            stream.feed(pcm(2500), 0, 0)  # a single block above the 2 s cap
        stream.feed(pcm(20), 0, 0)
        assert stream.flush(2.0)
    finally:
        stream.close()
        stream.close()  # idempotent

    with pytest.raises(RuntimeError):
        stream.feed(pcm(20), 1, 20)

    before = events.snapshot()
    time.sleep(0.1)
    assert events.snapshot() == before


def test_funasr_adapter_chunks_at_600ms_and_flushes_the_tail(monkeypatch):
    """Exercise the FunASR adapter's buffer/accumulate rules without torch."""

    import types

    import wanghao.voice.asr.backends as backends

    class FakeArray:
        def __init__(self, samples: int) -> None:
            self.samples = samples

        def astype(self, dtype):
            return self

        def __truediv__(self, value):
            return self

    fake_numpy = types.ModuleType("numpy")
    fake_numpy.float32 = "float32"
    fake_numpy.frombuffer = lambda chunk, dtype=None: FakeArray(len(chunk) // 2)

    class FakeModel:
        def __init__(self) -> None:
            self.calls: list[tuple[int, bool]] = []
            self.script: list[str] = []

        def generate(self, **kwargs):
            self.calls.append((kwargs["input"].samples, kwargs["is_final"]))
            text = self.script.pop(0) if self.script else ""
            return [{"text": text}]

    model = FakeModel()
    load_kwargs: list[dict] = []

    def auto_model(**kwargs):
        load_kwargs.append(kwargs)
        return model

    fake_funasr = types.ModuleType("funasr")
    fake_funasr.AutoModel = auto_model

    monkeypatch.setattr(backends, "_missing_modules", lambda: [])
    monkeypatch.setattr(backends, "_MODEL_CACHE", {})
    monkeypatch.setitem(sys.modules, "numpy", fake_numpy)
    monkeypatch.setitem(sys.modules, "funasr", fake_funasr)

    backend = backends.FunAsrBackend(
        model="fake-paraformer", model_revision="test", device="cpu"
    )
    assert backend.warmup() == "fake-paraformer@test"
    # One silent chunk primes lazy kernels without leaking into the transcript.
    assert model.calls == [(9600, False)]
    model.script = ["第一段", "第二段"]
    assert backend.accept(pcm(600)) == "第一段"  # exactly one 9600-sample chunk
    assert backend.accept(pcm(300)) == "第一段"  # buffered, not decoded yet
    assert backend.finish() == "第一段第二段"
    assert model.calls == [(9600, False), (9600, False), (4800, True)]
    assert load_kwargs[0]["model"] == "fake-paraformer"
    assert load_kwargs[0]["model_revision"] == "test"
    assert backend.model_version == "fake-paraformer@test"

    # A build that returns the whole sentence so far must not duplicate text.
    backend.reset()
    model.calls.clear()
    model.script = ["我想", "我想先说"]
    assert backend.accept(pcm(600)) == "我想"
    assert backend.accept(pcm(600)) == "我想先说"
    backend.close()
    assert backend.finish() == ""


def test_funasr_backend_reports_missing_dependencies():
    missing = [
        name
        for name in ("funasr", "numpy")
        if importlib.util.find_spec(name) is None
    ]
    if not missing:
        pytest.skip("FunASR dependencies are installed; adapter is not unit-tested here")
    with pytest.raises(AsrUnavailable) as info:
        create_backend("funasr")
    assert info.value.fatal is True
    assert "requirements-asr.txt" in str(info.value)
