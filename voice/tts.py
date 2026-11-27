"""兼容层：`voice.tts` → `wanghao.voice.tts`（合同路径不变）。"""

from wanghao.voice.tts import *  # noqa: F401,F403
from wanghao.voice.tts import STYLE_PROMPT, SAMPLE_RATE, load_creds, synthesize, synthesize_text, voice_id  # noqa: E402,F401
