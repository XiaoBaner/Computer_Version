# 王皓 · 语音（ASR / 合成 / 打断）

这个目录是这个人的全部工作，联调时整体挂到应用里即可。合同里的两个导入路径
**保持不变**：仓库根目录的 `voice/` 与 `web/audio/` 是一行转发，陈尧武、陈跃权的
代码照旧写 `from voice.asr import open_stream` 和 `/web/audio/index.js`。

```
wanghao/
  voice/
    asr/             流式识别（FunASR paraformer-zh-streaming）
    tts.py           流式合成（CosyVoice，含语调与 speech_rate）
    live_server.py   联调台服务：麦克风 → 识别 → 回复 → 合成 → 播放 → 打断
    README.md        接口、参数、实测与未验证项
  web/audio/         浏览器采集与播放（startCapture / createPlayer）
  tools/
    asr/feed_wav.py  把 wav 当麦克风喂进去，逐行打印该发的帧
    voice/           云端渲染、试听页导出、首包探针
  tests/voice/       11 项标准库测试
  assets/voice/final/  小卡 / 红红 / 小U 三种音色各 13 句成品 + manifest
  docs/              调参记录、音源与录制说明、中期报告
  requirements-asr.txt 识别环境锁定
```

## 怎么跑

```bash
# 只验证模块（不需要模型和网络）
python3 -m pytest wanghao/tests/voice -q

# 真识别联调台（FunASR 本地，不联网）
.venv-asr/bin/python -m wanghao.voice.live_server
# 浏览器打开 http://127.0.0.1:8420/wanghao/tools/voice/live.html

# 云端合成（需要控制台导出的 CSV，运行期读取，不落盘）
.venv-asr/bin/python -m wanghao.voice.live_server --creds ~/Desktop/xxx-apiKey-xxx.csv
```

## 我交付的四个入口

| 入口 | 签名 | 谁用 |
| --- | --- | --- |
| `voice.asr.open_stream` | `(session_id, on_transcript, *, on_speech, on_error)` → `feed/close` | 陈尧武 |
| `voice.tts.synthesize` | `(text, voice_style, cancelled, *, voice, speech_rate)` → `Iterator[(pcm, sr)]` | 陈尧武 |
| `web/audio` `startCapture` | `({onAudio, onInterrupt, getActiveTurn})` → `stopCapture()` | 陈跃权 |
| `web/audio` `createPlayer` | `({onPlayback, onLevel})` → `queueAudio/stopTurn/dispose` | 陈跃权 |

语音在系统里是配角：视觉出判断，语音给内容、`speech_features` 佐证，以及摄像头
不可用时的兜底（见 `voice/README.md` 的「视觉为主，语音佐证」）。

## 还没做完

- 云端 TTS 首包 P95 与 `speech_rate` 真变速未测（等凭据）；
- 双向语音验收：普通接话 20 次、有效打断 20 次尚无数据；
- 8 种语调还差 130 条未渲染（`voice-tuning.md` 有依据）；
- `docs/model-lock.md` 未补。
