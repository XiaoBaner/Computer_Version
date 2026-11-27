"""流式 TTS：一句已审核的台词进去，音频块陆续出来。

为什么必须流式：全双工的耳朵等不了整句。整句合成要等整句算完才出声，实测首包
会拖到 2 秒以上；这边用百炼的 WebSocket 流式接口，首包通常在 700–900 ms，
之后按语速连续下行，浏览器那边可以边收边播、随时被打断。

对外只有一个函数，签名按 docs/weekend-modules.md 第 3 节：

    synthesize(text, voice_style, cancelled) -> Iterator[(bytes, int)]

产出的是**裸 PCM**（s16le、单声道）和它的采样率，不含 WAV 头——协议里的
reply.audio.data 要的就是这个，由陈尧武适配 base64 与分片编号。

凭据：与云端页面同一套，读控制台导出的 CSV；不写进仓库、不打印。
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Callable, Iterator

STATE = Path.home() / ".cache" / "voice-cloud" / "voices.json"

SAMPLE_RATE = 24000  # 下行统一 24 kHz，浏览器按这个数解码

# 语调 → 指令。合同要求的四种在前，后面四种是给"音调更丰富"加的扩展。
STYLE_PROMPT = {
    "neutral": "",
    "gentle": "声音再软一点，气声多一点，像在哄人",
    "practice": "语气像陪练，带一点鼓励，轻快但耐心",
    "check_in": "带着心疼，声音低下来，像真的在担心他",
    "light": "语气轻快一点，带一点笑",
    "steady": "声音稳住，不急，给他靠得住的感觉",
    "soft": "很轻，几乎耳语，靠近一点说",
    "warm": "带一点笑意，温暖但不过分甜",
}


def load_creds(csv_path: str | Path | None = None) -> tuple[str, str]:
    """返回 (api_key, api_host)。不打印、不落盘。"""
    import os

    if csv_path:
        fields = {}
        for line in Path(csv_path).read_text(encoding="utf-8").splitlines():
            if "," in line:
                k, v = line.split(",", 1)
                fields[k.strip()] = v.strip().strip('"')
        if fields.get("apiKey") and fields.get("apiHost"):
            return fields["apiKey"], fields["apiHost"]
    key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    host = os.environ.get("DASHSCOPE_API_HOST", "").strip()
    if key and host:
        return key, host
    raise SystemExit("没有凭据：传 --creds <控制台导出的 csv>，或设置 DASHSCOPE_API_KEY/HOST")


def voice_id(key: str = "B") -> str:
    """从 voices.json 取某个人设的复刻音色 id。"""
    import json

    state = json.loads(STATE.read_text())
    for k in (key, key.lower(), key.upper()):
        if k in state:
            return state[k]["voice"]
    raise KeyError(f"voices.json 里没有 {key}；现有 {list(state)}")


def _instruction(voice_style: str, extra: str = "") -> str:
    bit = STYLE_PROMPT.get(voice_style, "")
    if extra:
        bit = f"{bit}，{extra}" if bit else extra
    return bit


def synthesize(
    text: str,
    voice_style: str = "neutral",
    cancelled: Callable[[], bool] | None = None,
    *,
    voice: str | None = None,
    model: str = "cosyvoice-v3.5-plus",
    creds: str | Path | None = None,
    extra: str = "",
    speech_rate: float = 1.0,
    timeout_s: float = 20.0,
    first_packet_timeout_s: float = 3.0,
) -> Iterator[tuple[bytes, int]]:
    """把一句台词流式合成出来。

    每次产出 ``(pcm_bytes, sample_rate)``，pcm 为 s16le 单声道裸数据。
    ``cancelled()`` 返回 True 时立即停止拉取并关闭会话——被打断的句子不该再占带宽。

    首包超过 ``first_packet_timeout_s`` 抛 TimeoutError，交给集成层按
    ``TTS_UNAVAILABLE`` 转文字（协议第 4 节）。
    """
    import dashscope
    from dashscope.audio.tts_v2 import AudioFormat, SpeechSynthesizer

    key, host = load_creds(creds)
    dashscope.api_key = key
    dashscope.base_websocket_api_url = f"wss://{host}/api-ws/v1/inference"

    chunks: queue.Queue[bytes | None] = queue.Queue()
    errors: list[BaseException] = []

    class _Cb:
        def on_open(self) -> None:  # noqa: D401
            pass

        def on_data(self, data: bytes) -> None:
            chunks.put(data)

        def on_complete(self) -> None:
            chunks.put(None)

        def on_error(self, message) -> None:  # noqa: ANN001
            errors.append(RuntimeError(str(message)))
            chunks.put(None)

        def on_close(self) -> None:
            pass

    kw = dict(format=AudioFormat.PCM_24000HZ_MONO_16BIT, callback=_Cb())
    prompt = _instruction(voice_style, extra)
    if prompt:
        kw["instruction"] = prompt
    if abs(speech_rate - 1.0) > 1e-3:
        # 0.5–2.0 是 CosyVoice 的合法区间；变速在服务端做，音高不变，
        # 比在浏览器里改 playbackRate 好（后者会把音色一起拖低）。
        kw["speech_rate"] = max(0.5, min(2.0, speech_rate))
    synth = SpeechSynthesizer(model=model, voice=voice or voice_id(), **kw)

    writer = threading.Thread(target=lambda: synth.streaming_call(text), daemon=True)
    writer.start()

    got_first = False
    try:
        while True:
            try:
                data = chunks.get(timeout=first_packet_timeout_s if not got_first else timeout_s)
            except queue.Empty:
                if not got_first:
                    raise TimeoutError("TTS 首包超时") from None
                break
            if data is None:
                break
            if cancelled and cancelled():
                break
            got_first = True
            yield data, SAMPLE_RATE
    finally:
        try:
            synth.streaming_complete()
        except Exception:
            pass
        if errors and not got_first:
            raise errors[0]


def synthesize_text(text: str, voice_style: str = "neutral", **kw) -> bytes:
    """便利函数：一次拿完整段 PCM（测试和离线预生成用）。"""
    return b"".join(chunk for chunk, _ in synthesize(text, voice_style, **kw))
