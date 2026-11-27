"""实时对话联调台：一条真的能说、能听、能被打断的链路。

它不替代最终产品——`/ws/session` 由陈尧武写、页面由陈跃权写。这里用**标准库**
起一个最小服务，把三段串起来跑通：

    麦克风 20 ms 块 → voice.asr（边说边出字）→ 一句回复 → voice.tts（首包即播）
                                              ↑
                                    用户插话 → 停播 + 取消合成

识别用 **PR #2 那份 `voice/asr`**（FunASR 本地流式 Paraformer，不联网、不要凭据）；
合成默认走云端复刻音色，没有凭据时退回 `assets/voice/final` 里已渲染好的成品。

上行 HTTP POST 每 20 ms 一块（裸 PCM），下行 SSE 推事件。字段按
docs/weekend-wire.md 起名，换 WebSocket 时业务逻辑不用动。

    # 真识别 + 成品配音（不需要凭据）
    .venv-asr/bin/python -m voice.live_server

    # 真识别 + 云端合成
    .venv-asr/bin/python -m voice.live_server --creds ~/Desktop/xxx-apiKey-xxx.csv

    # 什么都没装：固定脚本识别 + 成品配音，只验时序
    python3 -m voice.live_server --asr fixture
"""

from __future__ import annotations

import argparse
import base64
import json
import queue
import threading
import time
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CREDS: str | None = None
TTS_MODE = "clips"      # clips | cloud
SESSIONS: dict[str, "Session"] = {}

# 演示用的回复池：真实系统里这句话由对话模块给，这里按关键词挑一句，
# 目的是把"说完到出声"的延迟量出来，不冒充对话质量。
REPLIES = [
    ("累|辛苦|疲", "听起来今天真的很累。要不先不聊那些，就说说你现在最想放下的是什么？"),
    ("压力|考试|作业|ddl|忙", "这么多事压在一起，谁都会喘不过气。我们先挑一件最急的，只说它。"),
    ("难受|难过|烦|委屈|哭", "嗯，我在。你不用马上讲清楚，想到哪儿说到哪儿就行。"),
    ("不敢|害怕|开口|说话", "开口这件事，本来就要练。我们先从最短的一句开始，好不好？"),
    ("你好|在吗|喂|hello", "在的。今天想从哪里聊起？"),
    ("谢谢|好的|嗯|行", "不客气呀。你要是想继续说，我听着。"),
]
FALLBACK = "嗯，我听到了。你可以再多说一点，不着急。"

MOCK_ROTATE = ["10", "01", "11", "02", "09"]  # 无云端时轮流用的成品片段
# 无云端配音时按关键词挑片段，至少让"你说了累、她答辛苦"对得上
CLIP_RULES = [
    ("累|辛苦|疲|休息", "10"),
    ("你好|在吗|聊|说说", "01"),
    ("不敢|害怕|开口|说话", "02"),
    ("谢谢|好的|行", "09"),
]


def pick_clip(text: str) -> str:
    import re

    for pat, clip in CLIP_RULES:
        if re.search(pat, text):
            return clip
    return "11"


def pick_reply(text: str) -> str:
    import re

    for pat, line in REPLIES:
        if re.search(pat, text):
            return line
    return FALLBACK


VOICE_NAMES = {"B": "红红", "01": "小卡", "13": "小U"}
CLOUD_PREFIX = {"B": "p35B", "01": "p351", "13": "v35p13"}  # 云端复刻音色在 voices.json 里的键


def _cloud_voice_id(name: str) -> str:
    from voice import tts

    try:
        return tts.voice_id(CLOUD_PREFIX.get(name, name))
    except KeyError:
        return name


def _clip_audio(voice: str, clip_id: str, rate: float = 1.0) -> tuple[bytes, int]:
    """读一份已渲染好的成品，换成裸 PCM；rate<1 变慢（本地成品只能整体变速，音高会跟着降）。"""
    import audioop
    import wave

    path = ROOT / "assets" / "voice" / "final" / voice / f"{clip_id}.wav"
    with wave.open(str(path), "rb") as w:
        pcm, hz = w.readframes(w.getnframes()), w.getframerate()
    if abs(rate - 1.0) > 0.02:
        pcm, _ = audioop.ratecv(pcm, 2, 1, hz, int(hz / rate), None)
    return pcm, hz


def _clip_text(clip_id: str) -> str:
    """成品里那句话的原文，保证字幕与声音对得上。"""
    manifest = json.loads((ROOT / "assets" / "voice" / "final" / "manifest.json").read_text())
    for line in manifest["lines"]:
        if line["id"] == clip_id:
            return line["text"]
    return clip_id


class Session:
    """一个浏览器标签页：一条 ASR 流 + 一条 SSE 事件队列。"""

    def __init__(self) -> None:
        from voice import asr

        self.id = uuid.uuid4().hex[:8]
        self.events: queue.Queue[dict | None] = queue.Queue()
        self.turn_id: str | None = None
        self._cancel = threading.Event()
        self._tts_lock = threading.Lock()
        self._speaking = False
        self._clip_n = 0

        # 音色与语速：用户可以自己调，也可以交给自适应
        self.voice = "B"          # B=红红 / 01=小卡 / 13=小U
        self.rate = 0.9           # 用户设定，1.0 = 原速；默认略慢
        self.auto = True
        self._adj = 0.0           # 自适应偏移
        self._cps = None          # 用户语速的字/秒（指数滑动平均）
        self._reason = ""
        self._speech_start_ms: int | None = None
        self._last_speech_ms = 0

        self.stream = asr.open_stream(
            self.id,
            self._on_transcript,
            on_speech=self._on_speech,
            on_error=self._on_error,
            vad_threshold=0.01,   # fixture 也吃这个门限：安静时不切段、不出字
            end_silence_ms=900,   # 700 会把"你好，今天想从哪里聊起"切成两句
        )

    # -- ASR 回调（ASR 工作线程）----------------------------------------
    def _on_transcript(self, input_id: str, text: str, is_final: bool) -> None:
        self.emit({"type": "transcript", "input_id": input_id, "text": text,
                   "is_final": is_final})
        if is_final and text.strip():
            self._adapt(text)
            threading.Thread(target=self._speak, args=(text,), daemon=True).start()

    # -- 语速 -----------------------------------------------------------
    def _adapt(self, text: str) -> None:
        """用户明确要快慢就听他的；否则跟着他自己说话的节奏走。

        只做有限的偏移（±0.04），不做大幅跳变——她忽快忽慢比稍快更难受。
        """
        import re

        if re.search("慢一点|慢些|说慢|慢点|别急|有点快", text):
            self.rate = max(0.85, round(self.rate - 0.05, 2))
            self._reason = "你说慢一点"
        elif re.search("快一点|快点|说快", text):
            self.rate = min(1.1, round(self.rate + 0.05, 2))
            self._reason = "你说快一点"

        if not self.auto:
            self._adj = 0.0
            return
        chars = len(re.sub(r"[^0-9A-Za-z一-鿿]", "", text))
        secs = self._last_speech_ms / 1000
        if chars >= 2 and secs >= 0.6:
            cps = chars / secs
            self._cps = cps if self._cps is None else 0.6 * self._cps + 0.4 * cps
            if self._cps < 3.0:
                self._adj = -0.04
                self._reason = f"你刚才说得慢（{self._cps:.1f} 字/秒），我跟着慢一点"
            elif self._cps > 5.8:
                self._adj = 0.03
                self._reason = f"你刚才说得快（{self._cps:.1f} 字/秒），我稍微跟上"
            else:
                self._adj = 0.0
                self._reason = "跟着你的节奏"

    @property
    def effective_rate(self) -> float:
        return round(max(0.8, min(1.15, self.rate + self._adj)), 2)

    def _on_speech(self, active: bool, t_ms: int, level: float) -> None:
        # 120 ms 能量 VAD 比 600 ms 的首个转写快，先给页面一个"他开口了"的候选
        if active and self._speech_start_ms is None:
            self._speech_start_ms = t_ms
        elif not active and self._speech_start_ms is not None:
            self._last_speech_ms = max(0, t_ms - self._speech_start_ms)
            self._speech_start_ms = None
        self.emit({"type": "speech", "active": active, "t_ms": t_ms,
                   "level": round(level, 4)})

    def _on_error(self, code: str, message: str, retryable: bool) -> None:
        self.emit({"type": "session.error", "code": code, "message": message,
                   "retryable": retryable})

    # -- 下行 -----------------------------------------------------------
    def emit(self, event: dict) -> None:
        self.events.put(event)

    def _speak(self, heard: str) -> None:
        """合成一句并推给页面。同一时刻只允许一句在播，新句自然顶掉旧句。"""
        with self._tts_lock:
            if self._speaking:
                self._cancel.set()
            self._cancel = threading.Event()
            cancel = self._cancel
            self._speaking = True

            speed = self.effective_rate
            if TTS_MODE == "cloud":
                from voice import tts

                reply = pick_reply(heard)
                voice_id = _cloud_voice_id(self.voice)

                def source():
                    yield from tts.synthesize(reply, "gentle", cancelled=cancel.is_set,
                                              creds=CREDS, voice=voice_id,
                                              speech_rate=speed)
            else:
                clip = pick_clip(heard)
                if clip == "11" and self._clip_n:  # 没命中关键词时换着回，别老是同一句
                    clip = MOCK_ROTATE[self._clip_n % len(MOCK_ROTATE)]
                self._clip_n += 1
                reply = _clip_text(clip)

                def source():
                    pcm_all, hz = _clip_audio(self.voice, clip, speed)
                    step = hz // 10 * 2  # 100 ms
                    for off in range(0, len(pcm_all), step):
                        if cancel.is_set():
                            return
                        yield pcm_all[off:off + step], hz
                        time.sleep(0.02)  # 比实时快，模拟"下行跑在播放前面"

            turn_id = uuid.uuid4().hex[:6]
            self.turn_id = turn_id
            self.emit({"type": "rate", "value": speed, "user": self.rate,
                       "auto": self.auto, "reason": self._reason, "voice": self.voice})
            self.emit({"type": "reply.text", "turn_id": turn_id, "text": reply,
                       "voice_style": "gentle"})
            self.emit({"type": "state", "turn_id": turn_id, "state": "thinking"})

            t0 = time.perf_counter()
            seq = 0
            held: bytes | None = None  # 留一块看后面还有没有，才能给末块打 is_last
            held_rate = 24000
            try:
                for pcm, rate in source():
                    if cancel.is_set():
                        break
                    if held is not None:
                        self.emit({
                            "type": "reply.audio", "turn_id": turn_id, "sentence_id": "S1",
                            "seq": seq, "sample_rate": held_rate, "codec": "pcm_s16le",
                            "data": base64.b64encode(held).decode(), "is_last": False,
                        })
                        seq += 1
                    else:
                        self.emit({"type": "latency", "turn_id": turn_id,
                                   "first_packet_ms": round((time.perf_counter() - t0) * 1000)})
                    held, held_rate = pcm, rate
                if held is not None and not cancel.is_set():
                    self.emit({
                        "type": "reply.audio", "turn_id": turn_id, "sentence_id": "S1",
                        "seq": seq, "sample_rate": held_rate, "codec": "pcm_s16le",
                        "data": base64.b64encode(held).decode(), "is_last": True,
                    })
            except Exception as exc:  # 合成挂了就退回文字，协议第 4 节
                self.emit({"type": "session.error", "code": "TTS_UNAVAILABLE",
                           "message": str(exc), "retryable": True})
            finally:
                self._speaking = False

    def cancel(self) -> None:
        self._cancel.set()
        self.emit({"type": "state", "turn_id": self.turn_id, "state": "cancelled"})

    def close(self) -> None:
        self.cancel()
        try:
            self.stream.close()
        finally:
            self.events.put(None)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):  # noqa: ANN001
        super().__init__(*a, directory=str(ROOT), **kw)

    def log_message(self, fmt: str, *args) -> None:  # 静音，免得 20ms 一条刷屏
        if "POST /uplink" not in (fmt % args):
            super().log_message(fmt, *args)

    def _session(self, qs: str) -> Session | None:
        sid = ""
        for part in qs.split("&"):
            if part.startswith("sid="):
                sid = part[4:]
        return SESSIONS.get(sid) if sid else None

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/events"):
            return self._sse()
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if self.path.startswith("/open"):
            sess = Session()
            SESSIONS[sess.id] = sess
            return self._json({"sid": sess.id, "tts": TTS_MODE,
                               "asr_model": sess.stream.model_version,
                               "voice": sess.voice, "rate": sess.rate,
                               "auto": sess.auto, "voices": VOICE_NAMES})
        sess = self._session(self.path.split("?", 1)[-1])
        if sess is None:
            return self._json({"error": "no session"}, 404)
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n)

        if self.path.startswith("/uplink"):
            frame = json.loads(body)
            sess.stream.feed(base64.b64decode(frame["data"]), frame.get("seq", 0),
                             frame.get("t_ms", 0))
            return self._json({"ok": True})
        if self.path.startswith("/config"):
            cfg = json.loads(body or b"{}")
            if cfg.get("voice") in VOICE_NAMES:
                sess.voice = cfg["voice"]
            if "rate" in cfg:
                sess.rate = round(max(0.85, min(1.1, float(cfg["rate"]))), 2)
            if "auto" in cfg:
                sess.auto = bool(cfg["auto"])
                if not sess.auto:
                    sess._adj = 0.0
            return self._json({"voice": sess.voice, "rate": sess.rate,
                               "auto": sess.auto, "effective": sess.effective_rate})
        if self.path.startswith("/cancel"):
            sess.cancel()
            return self._json({"ok": True})
        if self.path.startswith("/close"):
            sess.close()
            SESSIONS.pop(sess.id, None)
            return self._json({"ok": True})
        return self._json({"error": "unknown"}, 404)

    def _sse(self) -> None:
        sess = self._session(self.path.split("?", 1)[-1])
        if sess is None:
            return self._json({"error": "no session"}, 404)
        self.send_response(200)
        self.send_header("content-type", "text/event-stream; charset=utf-8")
        self.send_header("cache-control", "no-cache")
        self.send_header("x-accel-buffering", "no")
        self.end_headers()
        try:
            while True:
                event = sess.events.get()
                if event is None:
                    break
                self.wfile.write(("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj: dict, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    global CREDS, TTS_MODE
    ap = argparse.ArgumentParser()
    ap.add_argument("--creds", help="控制台导出的 CSV；不给则用本地成品配音")
    ap.add_argument("--asr", default=None, choices=["funasr", "fixture"],
                    help="识别后端；默认 funasr（本机装了就用），否则 fixture")
    ap.add_argument("--port", type=int, default=8420)
    args = ap.parse_args()

    if args.creds:
        if not Path(args.creds).exists():
            raise SystemExit(f"凭据文件不存在：{args.creds}")
        CREDS = args.creds
        TTS_MODE = "cloud"

    backend = args.asr
    if backend is None:
        try:
            import funasr  # noqa: F401

            backend = "funasr"
        except ImportError:
            backend = "fixture"
    import os

    os.environ["VOICE_ASR_BACKEND"] = backend

    if backend == "funasr":
        from voice.asr import AsrUnavailable, warmup

        print("预热 FunASR（首次会加载模型，约十几秒）…", flush=True)
        try:
            info = warmup("funasr")
            print(f"ASR 就绪：{info}", flush=True)
        except AsrUnavailable as exc:
            print(f"FunASR 起不来，退回 fixture：{exc}", flush=True)
            os.environ["VOICE_ASR_BACKEND"] = "fixture"

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"联调台已起：识别 {os.environ['VOICE_ASR_BACKEND']}，配音 {TTS_MODE}\n"
          f"http://127.0.0.1:{args.port}/tools/voice/live.html", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for s in list(SESSIONS.values()):
            s.close()
        srv.server_close()


if __name__ == "__main__":
    main()
