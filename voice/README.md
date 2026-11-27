# voice · 服务端的"听"和"说"

实时对话拆开是四段：**采集/打断/播放**在浏览器（`web/audio`），**流式识别**和
**流式合成**在服务端（这里），**WebSocket 传输**归陈尧武，**页面**归陈跃权。

```
voice/asr/            识别（PR #2，FunASR 本地流式 Paraformer）
  __init__.py         open_stream / warmup / 错误类型
  stream.py           分段、input_id、临时/最终转写、积压与 seq 缺口
  backends.py         funasr / fixture 两个后端
  README.md           参数、实测表、风险
voice/tts.py          合成：synthesize(text, voice_style, cancelled) -> Iterator[(pcm, sr)]
voice/live_server.py  联调台：把三段串起来，浏览器点一下就能对话
tools/asr/feed_wav.py 把一段 wav 当麦克风喂进去，逐行打印 Flask 该发的帧
tools/voice/live.html 联调台的页面
tools/voice/stream_probe.py  量首包延迟
```

识别不在这里重新实现——`voice/asr/` 是「ASR 任务处理」那个会话的产物，
按 `docs/weekend-modules.md` 第 3 节交付，本目录只负责把它和合成、页面接起来。

## 联调台怎么跑

```bash
# 真识别（FunASR 本地，不联网）+ 本地成品配音
/Users/haowang/Desktop/Computer_Version/.claude/worktrees/blue-saber-console-ui-3042d9/.venv-asr/bin/python \
    -m voice.live_server

# 真识别 + 云端复刻音色
…/.venv-asr/bin/python -m voice.live_server --creds ~/Desktop/xxx-apiKey-xxx.csv

# 没装 FunASR：固定脚本识别，只验时序
python3 -m voice.live_server --asr fixture
```

浏览器打开 `http://127.0.0.1:8420/tools/voice/live.html`，点「开始对话」。
安静时不出字；说完停顿 900 ms 才出最终转写并触发回复；她说话时你开口会停声。

页面右上角会写明当前是哪个识别模型、配音走云端还是本地成品。

## 视觉为主，语音佐证

本项目的决策权在视觉（这是计算机视觉课程，贡献点也在可见面部动作上），语音模块
不抢主位，只提供三样东西：

1. **内容**：流式转写，给视觉判定补上"他在说什么"，也决定回复文字；
2. **佐证**：每段一条 `speech_features` 事件——`speech_ms`、`chars`、
   `chars_per_sec`、`level_peak`、`input_id`。唇动与语音是否一致、语气是否支持
   视觉给出的判断，由融合层决定怎么加权；
3. **兜底**：摄像头拒绝、低光、遮挡时，`on_speech` 的能量 VAD 和转写仍能维持
   话轮，页面按第 8 节降级路径走。

优先级按 `docs/demo-development-baseline.md` 第 6 节：视觉线索先出判断，语音
不覆盖视觉已确认的话轮，只在视觉缺证据时补齐。语气真正听得见的地方是语调与语速
（`voice_style` + `speech_rate`），由融合层的情绪判断驱动。

## 音色和语速

会话里可调，发 `POST /config?sid=…`，字段 `voice`（`B` 红红 / `01` 小卡 / `13` 小U）、
`rate`（0.85–1.10，默认 0.90）、`auto`（是否跟随用户语速）。

- **用户调速**：云端走 `SpeechSynthesizer(speech_rate=…)`，服务端变速，音高不变；
  本地成品只能整体重采样，音高会跟着降（没有凭据时的临时办法）。
- **自适应**：从 ASR 的 `on_speech` 起止时刻和最终转写字数算出用户语速
  （字/秒，指数滑动平均），慢于 3 字/秒转 −0.04，快于 5.8 字/秒转 +0.03，
  夹在 0.80–1.15；用户明确说「慢一点 / 快一点」直接改设定值，优先级高于自适应。
  只做小步偏移，避免她忽快忽慢。
- 每回合先发一条 `rate` 事件（`value` 实际语速、`user` 用户设定、`reason` 依据），
  页面据此显示"实际 0.94 · 你刚才说得慢（2.8 字/秒），我跟着慢一点"。

## 已验证（2026-11-27，M4）

| 项 | 结果 |
| --- | --- |
| 真识别整条链（麦克风帧 → FunASR → 回复 → 成品音频 → 播放回执） | 通过 |
| 用 `assets/voice/final/B/01.wav` 当输入 | 「你好今天想从哪里聊起」，首个转写 910 ms |
| 分句 | `end_silence_ms=700` 会切成「你好」+「今天想从哪里聊起」两句，联调台改 900 合成一句 |
| 上行 20 ms 分帧、末块 `is_last`、取消打断 | 通过 |
| 云端合成首包延迟 | **未测**，等凭据（`tools/voice/stream_probe.py` 已备好） |

## 凭据

一律运行期从百炼控制台导出的 CSV 读（`--creds`），不写进仓库、不打印、不复制。
也可以走环境变量 `DASHSCOPE_API_KEY` / `DASHSCOPE_API_HOST`。
