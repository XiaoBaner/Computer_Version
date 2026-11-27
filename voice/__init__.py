"""兼容层（一行转发）。

王皓的实现在 `wanghao/voice/`。合同里的导入路径 `voice.asr` / `voice.tts` 保持
不变，陈尧武的 `app/` 不用改。改完实现记得顺手确认这里还能 import。
"""

from wanghao.voice import asr, tts  # noqa: F401

__all__ = ["asr", "tts"]
