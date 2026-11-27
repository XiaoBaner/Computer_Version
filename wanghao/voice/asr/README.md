# 流式中文 ASR（王皓）

对应[六人模块交接第 3 节](../../docs/weekend-modules.md#3-王皓--陈尧武陈跃权)里的：

```python
stream = voice.asr.open_stream(session_id, on_transcript)
stream.feed(pcm, seq, t_ms)   # 16 kHz、单声道、s16le、有序块
stream.close()
```

本模块负责服务端这一段：语音分段、语音 `input_id`、临时/最终转写、序号缺口与
2 秒积压处理、识别失败时的文字兜底。它**不**决定话轮、不生成 `turn_id`，也不直接
发 WebSocket 帧；这些由陈尧武的 `app/` 把回调结果转成 `transcript` /
`session.error` 事件。

## 1. 先跑起来（不需要模型）

```sh
python3 -m pytest tests/voice -q
python3 tools/asr/feed_wav.py --wav 你的16k单声道.wav --backend fixture
# 用真实录音检查 VAD 分段（fixture 的文本仍是固定脚本，不是真实识别）
python3 tools/asr/feed_wav.py --wav 你的16k单声道.wav --backend fixture --vad-threshold 0.01
```

`fixture` 是标准库替身：任意 PCM 都按语音处理，每 2 秒切一段，按固定脚本产生两次
临时转写和一次最终转写。它让前端、Flask 和离线样例在下载模型之前就能接通全链路。
静音样例也能产生转写，便于对照契约。

工具会把 Flask 应当发出的帧逐行打印成 JSONL：

```json
{"v":1,"type":"transcript","session_id":"S1","input_id":"A88b3ad8ff22f4094","text":"我想先说一件事。","is_final":true}
```

需要真实的 16 kHz 单声道 16-bit WAV；其他格式先转换：

```sh
afconvert -f WAVE -d LEI16@16000 -c 1 input.aiff output.wav
ffmpeg -i input.wav -ar 16000 -ac 1 -c:a pcm_s16le output.wav
```

## 2. 接进 Flask

```python
from voice.asr import open_stream

def on_transcript(input_id: str, text: str, is_final: bool) -> None:
    send({"v": 1, "type": "transcript", "session_id": sid,
          "input_id": input_id, "text": text, "is_final": is_final})
    if is_final:
        # 每个正式输入只入队一次；临时转写只更新字幕，不触发回复生成。
        enqueue_final_input(input_id, text)

def on_error(code: str, message: str, retryable: bool) -> None:
    send({"v": 1, "type": "session.error", "session_id": sid,
          "code": code, "message": message, "retryable": retryable})

def on_speech(active: bool, t_ms: int, level: float) -> None:
    # 只作为“可能要插话”的候选；仍需结合当前播放回合确认。
    mark_possible_barge_in(active, t_ms, level)

stream = open_stream(sid, on_transcript,
                     on_speech=on_speech, on_error=on_error)

# 收到 input.audio，Base64 解码后：
stream.feed(pcm_bytes, seq, t_ms)

# 连接或逻辑会话结束时（王皓/陈尧武共同确认生命周期）：
stream.close()
```

要点：

- 每个逻辑会话持有一份 `stream`，重连沿用；`close()` 会丢弃未处理音频和当前分段。
- `feed()` 只入队，不做推理，不会阻塞 WebSocket 收包线程。回调都在**一个工作线程**
  上执行；回调里只应向发送队列投递，不要同步调用 `flush()`/`close()`。
- 临时转写 `is_final=false` 直接覆盖字幕；最终转写 `is_final=true` 每段最多一次。
  同一段的两次临时转写和最终转写共用同一个 `input_id`。
- 语音 ID 由服务端生成，形如 `A<16 hex>`，与浏览器文字输入（如 `I1`）区分。
- `on_speech` 来自 120 ms 能量 VAD，比 FunASR 的 600 ms 首个临时结果快，适合先做
  “用户开口”候选；是否停声仍由前端播放状态和集成层决定。
- 迟到/重复/回退的 `seq` 被忽略；跳号会重置该段并回调 `AUDIO_GAP`，集成层发
  `session.error` 并让用户重说。积压超过 2 秒时丢弃未完成旧段，同样报
  `AUDIO_GAP`。
- 识别不可用时只回调一次 `ASR_UNAVAILABLE`，用户继续用 `input.text`；致命失败后
  `stream.available` 为 `False`，后续音频不再产生转写。

## 3. 默认参数与调整

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `sample_rate` | `16000` | 只接受契约中的 16 kHz；块长任意，常见 20 ms（640 字节） |
| `vad_threshold` | fixture `0.0` / 真实 `0.01` | 归一化 RMS；安静房间可降到 0.006，嘈杂环境先测底噪 |
| `min_speech_ms` | `120` | 连续有声达到该时长才开段，用于 `on_speech` 候选 |
| `pre_roll_ms` | `150` | 开段时补送的前置音频，避免吞掉第一个字 |
| `end_silence_ms` | `700` | 尾静音达到该值才给最终转写；句中的短停顿仍属同一段 |
| `max_segment_ms` | fixture `2000` / 真实 `20000` | 超长连续说话强制切段，防缓存无限增长 |
| `max_backlog_ms` | `2000` | 上行音频积压上限，与网络接口一致 |
| `partial_every_blocks` | `10` | 仅 fixture：每 10 块输出一次临时转写 |

这些是首轮工程值。周日按演示电脑实测调整，并把改动和样本数写进联调记录。能量 VAD
不理解语义，句中换气、外放回声和轻声应和需要在集成层用播放状态、转写文本一起判断。

## 4. 真实后端 FunASR

基线模型是 `paraformer-zh-streaming`，适配参数沿用 FunASR 流式示例：

- `model_revision="v2.0.4"`，600 ms 解码块（`chunk_size=(0, 10, 5)`），
  `encoder_chunk_look_back=4`、`decoder_chunk_look_back=1`。
- 16 kHz 音频按 10 × 960 样本一块送入；同一进程内模型只加载一次，多会话推理串行。
- 首个临时结果约在 600 ms 之后，因此插话候选以 `on_speech` 为准。

安装（同一项目 `.venv`；先装 PyTorch，再装 ASR 依赖）：

```sh
.venv/bin/python -m pip install torch==2.14.1
.venv/bin/python -m pip install -r requirements-asr.txt
.venv/bin/python -m pip check
VOICE_ASR_BACKEND=funasr .venv/bin/python tools/asr/feed_wav.py --wav sample.wav
```

`requirements-asr.txt` 固定 `funasr==1.4.29`、`modelscope==1.40.3`、
`soundfile==0.14.0`、`kaldi-native-fbank==1.23.3`。这条流式 Paraformer 路径不依赖
torchaudio，且当前索引没有 2.14.x 的 torchaudio；FunASR 需要 torchaudio 或
kaldi-native-fbank 之一提供 fbank，前者装不上时用后者（有 arm64 wheel），不要为了
torchaudio 降级 torch。首次运行若提示缺少 fbank 后端，先装
`kaldi-native-fbank==1.23.3` 再重试。M4 上可用 `VOICE_ASR_DEVICE=mps` 切 MPS；
默认仍是 CPU，MPS 的收益和风险见下面的实测。

启动时预热，避免第一句话等模型下载/加载，并跑一段静音让惰性 kernel / 显存分配先
完成（MPS 首次推理可达数秒，不预热会触发下面的 2 秒积压保护）；失败时让 bootstrap
把 `features.asr` 置为 `false`，文字入口仍可用：

```python
from voice.asr import AsrUnavailable, warmup
try:
    print(warmup("funasr"))  # {"backend": "funasr", "model_version": "paraformer-zh-streaming@v2.0.4"}
except AsrUnavailable as exc:
    features["asr"] = False
    log.warning("ASR disabled: %s", exc)
```

### 本机实测（2026-11-25，M4 / 24 GB）

环境：Homebrew Python 3.11.16 的独立 `.venv-asr`，`torch==2.14.1`、
`funasr==1.4.29`、`modelscope==1.40.3`、`soundfile==0.14.0`、
`kaldi-native-fbank==1.23.3`。模型首次下载约 881 MB，缓存路径：

```text
~/.cache/modelscope/models/iic--speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online/snapshots/v2.0.4/model.pt
sha256 4fdfb48ed4471777c9a511e96a2acae17f77cac9d709cc756634622769192a64
```

测试音频由 `say -v Tingting` 合成，再用 `afconvert` 转成 16 kHz 单声道 s16le：
1.9 s 单句（“我想先说一件事”）与 11.4 s 三句。识别结果正确，流式 Paraformer
不带标点，所以三句最终文本是一整串无标点汉字。

| 设备 | 音频 | 模式 | 推理 RTF | 首个临时 | 最终（相对开口） | 丢块 |
| --- | --- | --- | --- | --- | --- | --- |
| CPU | 1.9 s | 快送 | 0.51 | — | — | 0 |
| MPS | 1.9 s | 快送 | 0.56 | — | — | 0 |
| CPU | 11.4 s | 快送 | 0.65 | — | — | 0 |
| MPS | 11.4 s | 快送 | 0.17 | — | — | 0 |
| CPU | 1.9 s | 实时 | 见注 | 1107.5 ms | 3650.3 ms | 0 |
| MPS | 1.9 s | 实时 | 见注 | 734.9 ms | 2905.7 ms | 0 |
| CPU | 11.4 s | 实时 | 见注 | 1037.6 ms | 14565.8 ms | 0 |
| MPS | 11.4 s | 实时 | 见注 | 690.4 ms | 14082.8 ms | 0 |

注：实时模式的墙钟时间由 20 ms 喂块节奏主导，工具打印的 `wall_ratio≈1.18` 不能当作
推理吞吐；推理吞吐看“快送”行（`wall_ratio` 此时接近 RTF）。`最终`包含 700 ms 尾静音，
长音频还包含工具补的 2.2 s 尾静音，因此不能直接和“用户说完到首声 3 s”预算相减。

结论与风险：

- 短句上 CPU 与 MPS 差不多；11.4 s 连续语音 MPS 明显更快（0.17 vs 0.65）。但 MPS
  首次推理有数秒初始化成本，**必须**先 `warmup("funasr")`；预热前实测首句直接触发
  `AUDIO_GAP`（100 块被丢，无最终结果）。预热现在会跑一块 600 ms 静音。
- 11.4 s 实时跑没有触发 2 秒积压；但能量 VAD 没有在句号处切段，整段 11.4 s 作为
  一个分段，最终结果直到文件结束约 3 s 后才出现。长回复/连续说话会显著推迟最终
  转写；周日要按真实麦克风和说话习惯决定是否缩短 `end_silence_ms`、调低
  `max_segment_ms`，或在前端提示用户一句一句说。
- CPU 单独运行 RTF 0.65，余量不大；同时跑 Qwen3-4B、TTS、Chrome/MediaPipe 时若
  RTF 超过 1，连续说话会让积压增长并在 2 s 处丢段。周日必须在演示电脑上按真实
  负载复测 CPU/MPS 和失败情况，不能把这张表的数字当成演示机保证。
- `say` 合成音没有环境噪声、回声和远场，VAD 阈值 0.01 与真实房间底噪仍需实测。
- 两条带 BGM 的中文短视频台词（6.5 s / 7.5 s，文件名即台词）实测：把各段最终
  文本拼起来，字准确率约 **91.5%**（CER 8.7% / 8.3%），两处错误都是同音词
  「起誓」→「气势 / 启示」。默认 `vad_threshold=0.01` 只从后半句开始识别；降到
  **0.002–0.008** 才能抓全整句。带背景音乐、轻声开头或远场素材要先按底噪重标阈值。
- 同一素材在 `end_silence_ms=700` 下会分成 2–3 个 final；调到 1000 ms 以上能合成
  一条，但第二句 CER 会升到 12.5%。集成层若要求“一句话一个 input”，需要在短窗口
  内合并 final，或在准确率与整句性之间取舍。
- 给 `generate` 临时加 `hotword="起誓"` 没有改变结果，说明这条流式模型不吃热词；
  修同音词需要换上下文模型或做文本后处理，不能只加参数。
- 模型许可、发行方和最终模型锁记录尚未写入 `docs/model-lock.md`（该文件与 TTS
  共用，等语音模块一起确认后补）；当前只固定了版本、revision 和权重哈希。

## 5. 测试覆盖

`tests/voice/test_asr.py` 只依赖标准库和 pytest：

- 同一段两次临时转写 + 一次最终转写，ID 一致，帧通过 `contracts/events.v1.json` 校验；
- 能量 VAD 在句末切段，新段分配新 ID；
- `seq` 跳号重置并报 `AUDIO_GAP`，重复/回退序号忽略；
- 超过 2 秒积压时 `feed()` 不阻塞，丢旧段并报 `AUDIO_GAP`；
- 致命后端失败只报一次 `ASR_UNAVAILABLE` 并禁用；可重试失败在下一段恢复；
- `on_speech` 的开口/收段顺序与电平范围；
- 非法 PCM/序号/采样率、重复 `close()` 与关闭后 `feed()` 的行为；
- FunASR 适配器的 600 ms 分块、尾块 `is_final`、预热静音块与累计文本去重（假模块，无需 torch）；
- 本机缺 FunASR 依赖时给出可执行的安装提示。

本机结果：`python3 -m pytest tests/voice -q` → `11 passed`（2026-11-25）。

## 6. 尚未覆盖

- 说话人分离、中文标点恢复与热词表未接入；Paraformer 的热词参数可在拿到项目词表后加。
- 真实麦克风/房间底噪、回声、远场与多人同时说话未测；当前用 `say` 合成音。
- 与 Qwen3-4B、TTS、Chrome/MediaPipe 同时运行的 RTF、积压和失败率未测，周日补。
- 浏览器采集与播放（`web/audio/`）不在本模块；这里只接收已解码的 PCM 块。
- `on_speech` 不替代前端的回声消除和播放状态判断。
