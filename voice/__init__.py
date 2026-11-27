"""王皓负责的语音模块：复刻音色、流式合成、流式识别、联调台。

    voice/tts.py          synthesize(text, voice_style, cancelled) -> Iterator[(pcm, sr)]
    voice/asr/            open_stream(session_id, on_transcript) -> stream（FunASR 流式识别）
    voice/live_server.py  标准库联调台：麦克风 → 识别 → 回复 → 播报 → 可打断

凭据一律运行期从控制台导出的 CSV 读取，不写进仓库、不打印。
"""
