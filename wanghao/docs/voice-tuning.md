# 语音调音手册：从“能听”到“好听”

面向王皓的语音线，也供陈尧武、陈跃权联调时对照。目标不是把参数背下来，而是用一套可复现的流程，把“自然、温柔、愿意继续聊”的声音稳定做出来。

> **一句话结论**：好听的语音 = 音色/底模 40% + 参考音频 25% + 文本与断句 15% + 参数/情感指令 10% + 后期 10%。参数不是魔法，参考音频和文本前端才是大头。

## 0. 先看这五条

1. **先定音色，再调参数**。音色不对，参数调到天亮也只是“另一个不好听的音色”。
2. **参考音频决定上限**。零样本 3–10 秒，微调 1–5 分钟；干声、无伴奏、无混响、情绪一致、转写逐字准确。
3. **短句优先**。首句 6–15 字，后续 15–25 字；按标点切，别让模型一口气念 60 字。
4. **情感靠 instruct + 文本 + 停顿**，不靠把 `temperature` 拉高。高温度带来的不是情感，是不稳定。
5. **下行用 24 kHz**。16 kHz 只用于上行 ASR；为了省带宽把播放降到 16 kHz，齿音和空气感会明显变闷。

## 1. 本项目当前的约束

现有基线见 [Demo 开发基线与验收标准](demo-development-baseline.md) 第 4.1 节和第 8 节。

- 基线模型：`Qwen3-TTS-12Hz-1.7B-CustomVoice`，备选 `CosyVoice2-0.5B`。
- 基线候选：**Serena**（温和女声，首选）、**Vivian**（明亮女声）、**Dylan**（北京男声）。
- 每个候选 8 句；定稿音色再做同一句的 4 种语调。
- 至少 3 名组员盲听；入选版本自然度中位数 ≥ 4/5，无影响句义的错读。
- 双向语音：至少 20 次有效打断 + 20 次普通接话；开口到停声 P95 ≤ 500 ms，普通结束到第一声 P95 ≤ 3 s；口型停止与声音停止时差 ≤ 120 ms。
- 对外接口：`voice.tts.synthesize(text, voice_style, cancelled) -> Iterator[tuple[bytes, int]]`；同句采样率固定。

**当前偏好与基线的冲突**：四月（`Maia`）和千雪（`Chelsie`）是 **`qwen3-tts-flash` 的云端系统音色**，本地开源 `Qwen3-TTS-12Hz-1.7B-CustomVoice` 没有这两个 speaker。若最终选四月/千雪，必须同步更新 `docs/model-lock.md`、`assets/voice/samples/` 和基线里的音色说明；不能一边写 Serena、一边上线 Maia。

| 音色 | 参数 ID | 类型 | 听感 | 建议 |
| --- | --- | --- | --- | --- |
| 四月 | `Maia` | 云端 `qwen3-tts-flash` | 知性、温柔 | 主选；云端依赖 |
| 千雪 | `Chelsie` | 云端 `qwen3-tts-flash` | 二次元虚拟女友、偏甜 | A/B；ACG 受众再考虑 |
| 苏瑶 | `Serena` | 本地 CustomVoice / 云端同名 | 温和、稳 | 本地首选；离线兜底 |
| 十三 | `Vivian` | 本地 CustomVoice | 明亮、清晰 | 清晰度对照 |
| 北京-晓东 | `Dylan` | 本地 CustomVoice | 自然北京男声 | 男声身份对照 |

> 云端 `Serena` 与本地 `Serena` 不是同一套权重，音质、条款和延迟都不同。模型版本必须写进 `model-lock`，不要只写一个名字。

## 2. 模型选型：先看“你要什么”

| 需求 | 首选 | 备选 | 说明 |
| --- | --- | --- | --- |
| 当前项目、最快出效果 | 云端 `Maia` | 云端 `Chelsie` | 免费额度 1 万字符/90 天；之后约 0.8 元/万字符，实时版约 1 元/万字符 |
| 离线、可商用、流式 | CosyVoice2-0.5B | Qwen3-TTS-1.7B | CosyVoice2 Apache-2.0，150 ms 级流式，instruct 控制情感/语速/方言 |
| 情感最丰富 | IndexTTS2 | CosyVoice2 | `emo_alpha`、情感参考音频、8 维情感向量、`duration_factor` |
| 自定义角色音色 | GPT-SoVITS | Qwen3-TTS VoiceDesign→Clone | GPT-SoVITS 1 分钟可微调；情感控制不是强项 |
| 原创音色、版权干净 | Qwen3-TTS VoiceDesign→Clone | 自己录 + GPT-SoVITS | 先设计，再克隆，音色可复用 |
| 口语节奏/停顿研究 | ChatTTS | — | 有 `[laugh]`、`[uv_break]`、`[break]`，但情感控制有限，长句稳定性一般 |

### 2.1 现成试听样例

- [`assets/voice/demos/qwen3tts/index.html`](../assets/voice/demos/qwen3tts/index.html)：**本地 Qwen3-TTS-12Hz-1.7B-CustomVoice 真模型**，Serena / Vivian / Dylan / Uncle_Fu × 正常 / 温柔 / 慢速，共 12 条。同一文本、同一采样参数，只改 instruct；已统一到约 -20 dBFS RMS。生成脚本 `tools/voice/synth_qwen_samples.py`。
- [`assets/voice/demos/qwen3tts-serena-tones/index.html`](../assets/voice/demos/qwen3tts-serena-tones/index.html)：Serena 按 §6 的四种项目语调（neutral/gentle/practice/check_in）各一条，用于核对 instruct 是否真的改变了语气。
- [`assets/voice/demos/qwen3tts-serena-short/index.html`](../assets/voice/demos/qwen3tts-serena-short/index.html)：check_in 与 practice 改成 14 字短句后的版本，验证“文本承担长短、instruct 只管语气”的做法。
- [`assets/voice/demos/edge/index.html`](../assets/voice/demos/edge/index.html)：4 个 Edge 参考音色 × 3 档调音，仅用于对照“温柔一点、慢一点”的听感方向。

两组都**不是 Maia/Chelsie 原声**（那是云端音色）；本地可复现的是 Serena/Vivian/Dylan/Uncle_Fu。试听结论要落到盲听分数，不要只看 instruct 写了什么：实测 Serena 的“温柔”档时长几乎没变，Vivian 明显变慢，说明不同音色对同一指令的敏感度不同。

## 3. 参考音频工程（最容易被低估）

### 3.1 时长与格式

- 零样本克隆：**3–10 秒**，5 秒左右最稳；Qwen3-TTS Base 官方支持 3 秒克隆。
- GPT-SoVITS 微调：**1 分钟起步**，5–10 分钟更稳；推理时另取 3–10 秒参考片段。
- 格式：24 kHz 或 48 kHz，单声道，16/24-bit；最终 TTS 下行 24 kHz。
- 电平：峰值不超过 -1 dBFS，整体约 -18 到 -23 dBFS；不要削波。
- 环境：安静、无回声、固定麦克风距离；不要 BGM、不要混响、不要多人重叠。

### 3.2 清洗顺序

1. 用 UVR5 或 Demucs 分离人声，去掉伴奏/背景音乐。
2. 用 DeepFilterNet、Resemble Enhance 或 ClearerVoice-Studio 轻降噪；**宁少勿多**。
3. 去混响要谨慎；过度去混响会让声音发干、发薄。
4. 剪掉首尾长静音，保留自然气口和短停顿；不要把句内停顿全剪掉，否则模型会学成“赶火车”。
5. 用 ASR 转写后**逐字校对**；参考文本和音频必须一致，数字、英文、多音字尤其要改对。

### 3.3 参考音频的“情绪一致性”

参考片段是什么情绪，克隆出来就容易带什么情绪。做温柔陪伴角色，参考音频就用温柔、放松、稍慢的语气录；不要用一段很兴奋的 sample 去克隆一个温柔角色。

## 4. 文本前端：断句、数字、多音字

### 4.1 断句

- 首句 6–15 字；后续每句 15–25 字；超过 30 字就切。
- 按句号、问号、逗号、破折号切；不要按固定字数硬切，容易切断语义。
- 问句保留问号；需要停顿的地方用逗号或省略号，不要用大量空格。
- 首句短，是为了首声快和打断快；长句留到后面。

### 4.2 文本正则化

- 数字、日期、时间、单位、金额、英文缩写、网址、表情符号先转成口语。
- 多音字用 `pypinyin` / `g2pW` / 自定义词典校正；“重、行、长、还、得、地、了”这些高频字要建项目词表。
- 人名、机构名、课程名单独建发音词典；试听句里的“王老师”“导师”要固定读法。
- 工具：[WeTextProcessing](https://github.com/wenet-e2e/WeTextProcessing)（文本正则化/逆正则化，约 843 stars）、[pypinyin](https://github.com/mozillazg/python-pinyin)（约 5.4k stars）。

### 4.3 口语化

把书面语改成能说出口的话：

- 书面：`请确认您明日是否方便参加组会。`
- 口语：`明天组会，你能来吗？`
- 书面：`我理解你当前可能存在较大的心理压力。`
- 口语：`听起来你最近挺累的。要不要先说说是哪件事最压你？`

## 5. 逐模型调参

### 5.1 Qwen3-TTS（当前项目主线）

本地开源模型：

- `Qwen3-TTS-12Hz-1.7B-CustomVoice`：9 个预设音色，支持 `instruct`。
- `Qwen3-TTS-12Hz-0.6B-CustomVoice`：**不支持 instruct**，别拿它调情感。
- `Qwen3-TTS-12Hz-1.7B-VoiceDesign`：用文字描述设计音色。
- `Qwen3-TTS-12Hz-1.7B-Base`：3 秒克隆，可做微调底座。

生成默认值（源码 `qwen_tts/inference/qwen3_tts_model.py`）：

```text
top_k=50, top_p=1.0, temperature=0.9, repetition_penalty=1.05
```

建议区间：

| 目标 | top_k | top_p | temperature | repetition_penalty |
| --- | ---: | ---: | ---: | ---: |
| 稳、温柔、少漂移 | 30–50 | 0.80–0.95 | 0.70–0.90 | 1.05–1.10 |
| 自然、略有起伏 | 50 | 0.95–1.00 | 0.90–1.00 | 1.05 |
| 不建议 | <20 | <0.70 | >1.10 | >1.20 |

调用示例（以官方 examples 为准，`device_map` 按实际 GPU/CPU 改）：

```python
from qwen_tts import Qwen3TTSModel

tts = Qwen3TTSModel.from_pretrained(
    "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
    device_map="cuda:0",
)

wavs, sr = tts.generate_custom_voice(
    text="我们可以慢一点。你刚才说到导师，然后停了一下。",
    language="Chinese",
    speaker="Serena",
    instruct="温柔、平稳，语速比正常略慢，句间留出约 300 毫秒的空白。",
    top_k=50,
    top_p=0.90,
    temperature=0.80,
    repetition_penalty=1.05,
)
```

**原创音色路线：VoiceDesign → Clone**（官方推荐，适合长期做“四月那种知性温柔”，又不想依赖云端）：

1. 用 VoiceDesign 生成一段目标音色的参考音频：
   `instruct="二十多岁、知性温柔的女性，普通话标准，语速稍慢，句尾自然下沉，声音干净不甜腻，像在安静房间里认真听人说话。"`
2. 把这段参考音频喂给 Base 模型的 `create_voice_clone_prompt`，固定成可复用的音色 prompt。
3. 后续所有句子都用 `generate_voice_clone(..., voice_clone_prompt=...)`，保证音色一致。

这条路比“蒸 Maia”干净：音色是你设计的，不涉及云端音色条款，也不用克隆真人。

云端路线：

- `qwen3-tts-flash`：系统音色，按字符计费；适合快速试音。
- `qwen3-tts-instruct-flash`：支持自然语言指令；要情感控制优先看它。
- `qwen3-tts-flash-realtime`：流式，适合打断；先确认 `Maia` / `Chelsie` 在实时音色子集里。
- 云端普通 flash 不吃 `instruct`，别把 instruct 文本传给不支持的模型。

### 5.2 GPT-SoVITS

适合“有一个明确目标音色、愿意微调”的场景。官方主仓现在在 [bilibili/GPT-SoVITS](https://github.com/bilibili/GPT-SoVITS)（约 64k stars）；旧 `RVC-Boss` 地址现在是低星分叉，clone 时注意。

默认值（`api_v2.py`）：

```text
top_k=15, top_p=1.0, temperature=1.0, repetition_penalty=1.35,
speed_factor=1.0, fragment_interval=0.3, sample_steps=32
```

建议：

| 目标 | top_k | top_p | temperature | 语速 | 句间 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 稳、少漏字 | 10–20 | 0.80–0.90 | 0.60–0.80 | 0.90–1.00 | 0.30–0.40 |
| 自然、有起伏 | 20–40 | 0.90–1.00 | 0.90–1.00 | 0.95–1.05 | 0.30–0.40 |
| 不建议 | <5 | <0.40 | <0.40 | >1.15 | >0.50 |

经验边界：**无参考文本时，`top_k` 不要低于 5、`top_p` 不要低于 0.4、`temperature` 不要低于 0.4**，否则容易漏字、重复、音色漂。

API 关键字段：

```json
{
  "text": "我们可以慢一点。你刚才说到导师，然后停了一下。",
  "text_lang": "zh",
  "ref_audio_path": "assets/voice/ref/gentle_05.wav",
  "prompt_text": "参考音频的逐字转写",
  "prompt_lang": "zh",
  "top_k": 15,
  "top_p": 0.9,
  "temperature": 0.8,
  "speed_factor": 0.95,
  "fragment_interval": 0.35,
  "repetition_penalty": 1.35,
  "streaming_mode": 1
}
```

训练侧：

- WebUI 顺序：填音频路径 → 切割 → 降噪（可选）→ ASR → 校对标注 → 训练。
- 先不要乱改训练默认参数；显存够再加 `batch_size`。
- 参考音频要干净、情绪一致；BGM、混响、削波会直接毁掉相似度。
- GPT-SoVITS 的强项是音色相似和稳定性，不是精细情感控制；要情感优先看 IndexTTS2/CosyVoice2。

### 5.3 CosyVoice2

Apache-2.0，0.5B，支持零样本、跨语种、instruct，流式延迟可到 150 ms 级。适合做离线兜底和“自然语言控制情感”。

四种模式：

- `inference_sft`：预训练音色。
- `inference_zero_shot`：3–10 秒参考音频 + 参考文本。
- `inference_cross_lingual`：参考音频 + 另一种语言文本。
- `inference_instruct` / `inference_instruct2`：自然语言指令控制语气、情感、语速、方言。

关键参数：

```text
speed=0.5–2.0（默认 1.0；陪伴场景建议 0.9–1.1）
stream=True（流式）
text_frontend=True（文本正则化）
instruct_text="用温柔、稍慢、句间留白的语气说<|endofprompt|>"
```

CosyVoice2 的 instruct 文本末尾要加 `<|endofprompt|>`，把“指令”和“正文/参考文本”分开；官方 example 也是这个写法。

调用示例：

```python
from cosyvoice.cli.cosyvoice import CosyVoice2
import torchaudio

cosyvoice = CosyVoice2(
    "pretrained_models/CosyVoice2-0.5B",
    load_jit=False,
    load_trt=False,
    fp16=False,
)

for i, out in enumerate(cosyvoice.inference_instruct2(
    "我们可以慢一点。你刚才说到导师，然后停了一下。",
    "用温柔、平稳、比正常略慢的语气说，句间留出约 300 毫秒。<|endofprompt|>",
    "assets/voice/ref/gentle_05.wav",
    stream=False,
    speed=0.95,
)):
    torchaudio.save(f"out_{i}.wav", out["tts_speech"], cosyvoice.sample_rate)
```

注意：

- `inference_instruct` 只对 `CosyVoice-300M-Instruct` 这类模型生效；用错模型会忽略指令。
- `inference_instruct2` 可以同时用参考音频和 instruct，适合“固定音色 + 变语气”。
- 参考音频质量决定音色相似度；instruct 决定语气，不要把两者混为一谈。

### 5.4 IndexTTS2

情感控制最强的一档，适合“支持性对话”这种需要心疼、关切、克制的场景。许可是 B 站自定义协议，课程 demo 可用，商用要先联系授权。

情感控制四种方式：

1. 情感参考音频：`emo_audio_prompt="emo_sad.wav"`。
2. 情感强度：`emo_alpha=0.0–1.0`，默认 1.0；自然一些用 **0.6**，强情绪用 0.8–0.9。
3. 8 维情感向量：`[happy, angry, sad, afraid, disgusted, melancholic, surprised, calm]`，例如 `[0, 0, 0.8, 0, 0, 0, 0, 0]`。
4. 文本情感：`use_emo_text=True`，可配 `emo_text`；建议 `emo_alpha≈0.6`，`use_random=False`。

语速：`duration_factor=0.5–2.0`，大于 1 变慢；温柔场景试 1.05–1.15。该参数在 IndexTTS2.5 上确认可用，IndexTTS2 以实际版本为准。

IndexTTS2.5 用 `use_emo_text` 时需要在构造模型时加 `use_qwen_emo=True`，否则会报 `RuntimeError`；IndexTTS2 不需要这个开关。

示例：

```python
tts.infer(
    spk_audio_prompt="assets/voice/ref/gentle_05.wav",
    text="听起来你今天挺累的。要不要先跟我说说，最让你烦的是哪一件事？",
    output_path="out.wav",
    emo_alpha=0.6,
    use_emo_text=True,
    use_random=False,
    duration_factor=1.1,
)
```

多音字用拼音标注，避免“行/重/长/还”读错；拼音控制是 IndexTTS2 的强项，试听句里要专门测。

### 5.5 其他模型一句话

- **Fish Speech**：零样本质量高、多语言；参考音频和文本同样要干净。商用看权重许可。
- **F5-TTS**：默认 `nfe_step=32, cfg_strength=2.0, sway_sampling_coef=-1.0, speed=1.0, cross_fade_duration=0.15`；`nfe_step` 高一点更稳但更慢。
- **ChatTTS**：默认 `temperature=0.3, top_P=0.7, top_K=20`；`[oral_2][laugh_0][break_4]` 能加口语感，但情感控制有限，中文长句容易飘。
- **EmotiVoice**：2000+ 音色、情感 prompt，适合学“情感标签怎么映射到声音”，工程活跃度不如前几个。

## 6. 情感指令库（直接可用）

把基线里的四种语调写成可传给 TTS 的 instruct。云端 instruct 版、本地 Qwen3-TTS、CosyVoice2 都可以按这个思路改写。

| `voice_style` | 使用场景 | instruct | 语速/停顿 |
| --- | --- | --- | --- |
| `neutral` | 普通回应、信息澄清 | 自然中文对话，常速，句尾自然收住，不要播音腔。 | 1.0；句间 150–250 ms |
| `gentle` | 用户紧张、思绪多、明确想慢一点 | 音量平稳，语速比正常略慢，句间留白，声音贴近，像在安静房间认真听人说话。 | 0.9；句间 250–450 ms |
| `practice` | 用户想演练怎么开口 | 清楚地示范一遍，关键词轻微重读，像面对面和一个人说话。 | 0.95–1.0；关键词前短停 |
| `check_in` | 用户纠正、内容需要澄清 | 平和，句子简短，保持自然的问句语调，问完停住。 | 0.95–1.0；问句后 300–500 ms |

情感适配规则：

- 用户说“紧张/乱/慢一点” → `gentle`。
- 用户说“教我怎么说/练一下” → `practice`。
- 用户纠正、语义不清 → `check_in`。
- 其他 → `neutral`。
- 不靠摄像头猜情绪；文本和声音是主证据。角色始终是 AI，不装真人。

## 7. 后期处理：宁少勿多

TTS 输出通常已经干净，后期只做“统一响度、修一点频谱、压住峰值”。每加一步都要 A/B。

推荐链：

```text
高通 80 Hz
→ 少量减 200–400 Hz（只在不浑浊时动）
→ +1–2 dB @ 2–5 kHz（存在感）
→ -2 dB @ 6–8 kHz 或轻去齿音
→ 压缩 2:1，GR 3–4 dB，attack 15 ms，release 120 ms
→ 限幅 -1 dBTP
→ 响度 -16 LUFS（网页/播客）
```

ffmpeg 单段示例（先 `brew install ffmpeg`；本机当前没有 ffmpeg）：

```bash
ffmpeg -i input.wav -af "highpass=f=80,equalizer=f=300:t=q:w=1:g=-2,equalizer=f=3500:t=q:w=1.2:g=1.5,equalizer=f=6500:t=q:w=2:g=-2,acompressor=threshold=-18dB:ratio=2:attack=15:release=120:makeup=2,loudnorm=I=-16:TP=-1.5:LRA=11,alimiter=limit=0.89" -ar 24000 -ac 1 output.wav
```

响度测量：

```bash
ffmpeg -i output.wav -af ebur128 -f null -
```

macOS 自带工具只能转码，不能做 EQ/压缩：

```bash
afconvert -f WAVE -d LEI16@24000 -c 1 input.aiff output.wav
```

实时播放侧（Web Audio）建议一条固定轻链，不要每个 chunk 单独建图：

```js
const hp = ctx.createBiquadFilter();
hp.type = "highpass";
hp.frequency.value = 80;

const presence = ctx.createBiquadFilter();
presence.type = "peaking";
presence.frequency.value = 3500;
presence.gain.value = 1.5;
presence.Q.value = 1.0;

const comp = ctx.createDynamicsCompressor();
comp.threshold.value = -18;
comp.knee.value = 6;
comp.ratio.value = 2;
comp.attack.value = 0.015;
comp.release.value = 0.12;
```

常见过度处理：

- 过度降噪：声音发干、发金属、气口消失。
- 过度 EQ：200–400 Hz 一刀切，男声变薄、女声变尖。
- 过度压缩：动态全没，听起来像客服广播。
- 响度不统一：盲听时更响的样本天然占便宜，必须统一到同一 LUFS 再比。

## 8. 评测：盲听 + 客观指标

### 8.1 盲听协议

- 3 名组员，互不讨论；候选随机编号，不知道哪个是哪个。
- 每个候选 8 句，同一批文本、同一响度、同一播放设备。
- 评分：自然度 1–5；情绪是否合适 1–5；有没有影响句义的错读；愿不愿意继续聊（是/否）。
- 入选门槛：自然度中位数 ≥ 4/5；无影响句义的错读。
- 定稿音色再做 4 种语调并排试听。

### 8.2 本项目 8 句试听集

| 试听句 | 检查点 |
| --- | --- |
| 「你想聊的，从你觉得好开口的地方开始就行。」 | 平稳开场、尾音、年龄感 |
| 「那条消息发出去以后，你一直在等回复。」 | 复述具体事件，别像朗读旁白 |
| 「我们可以慢一点。你刚才说到导师，然后停了一下。」 | 轻声、短停顿、留白 |
| 「你说的‘为难’，更多是怕说不清，还是怕对方误会？」 | 句内重音、问句语调 |
| 「试试这样开头：老师，我想跟您确认一下这周的进度。」 | 预设台词 vs 普通语气 |
| 「你提到明天下午三点，还有王老师的那条消息。」 | 人名、数字、声调 |
| 「好，我先听你补完。」 | 打断后的及时、自然 |
| 「可以，从上次没说完的开头继续。」 | 回访的连续性 |

### 8.3 客观指标

- **自然度**：UTMOS / NISQA；只做筛子，最终靠盲听。
- **可懂度**：Whisper/FunASR 转写算 CER；错读必须人工复核。
- **音质/噪声**：DNSMOS；参考音频清洗前后对比。
- **情感**：F0 范围、能量方差、语速；只能辅助，不能替代人耳。
- **工程**：首包延迟、RTF、P95；按基线记录 `t_user_onset`、`t_audio_stop`、`t_first_audio`。

工具：

- [tts-benchmark](https://github.com/navi-/tts-benchmark)：TTFB、RTF、UTMOS、NISQA。
- [speechonnxmetrics](https://github.com/TigreGotico/speechonnxmetrics)：UTMOS22、DNSMOS、NISQA-v2、STOI、SI-SDR、MCD、WER/CER，一套 numpy + onnxruntime。

### 8.4 记录模板

| 日期 | 模型/版本 | 音色 | 文本 ID | top_k | top_p | temp | rep | speed | 自然度 | 错读 | 结论 |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| 11-25 | qwen3-tts-flash | Maia | S3 | — | — | — | — | 0.9 | 4.5 | 无 | 保留 |

### 8.5 仓库内小工具

```bash
python3 tools/voice/analyze_audio.py assets/voice/samples
```

检查采样率、声道、时长、峰值、RMS、削波和静音比例；默认按 24 kHz 单声道检查。

```bash
python3 tools/voice/make_blind_test.py --input assets/voice/samples --out blind-test --seed 7
```

生成匿名 A/B/C 盲听页，评分结果可导出 JSON。`blind-test/mapping.json` 保存真实音色映射，评分结束前不要让听者打开。详细用法见 [tools/voice/README.md](../tools/voice/README.md)。

## 9. 本周执行清单

今天 11 月 24 日，周末 28–29 日联调。按下面的顺序做，不要一上来就微调模型。

1. **今天**：官方音色列表试听 `Maia`、`Chelsie`、`Serena`；用同一句比较“温柔但不腻、句尾自然、长句不飘”。先选 2 个，不要选 5 个。
2. **周二**：用 Maia 生成 8 句试听集；同一批文本再跑 Chelsie/Serena；统一响度后盲听。
3. **周三**：给入选音色做 4 种语调；`gentle` 先试 0.9 倍语速、句间 250–450 ms；记录参数。
4. **周四**：后期统一到 -16 LUFS；检查 24 kHz 下行、首包延迟、长句稳定性；把样本放进 `assets/voice/samples/`。
5. **周五**：接入 `voice.tts.synthesize`；测 TTS 失败转文字、自动播放受阻转文字、断线清理队列；准备本地 Serena 兜底和双网/热点。
6. **周六**：按基线跑 20 次普通接话 + 20 次有效打断；记录 P95、漏停、误停；陈跃权验口型/停声 ≤120 ms。
7. **周日**：全组回归，冻结版本；把模型 ID、音色、参数、采样率、许可写进 `docs/model-lock.md`。

**这周不要做**：蒸 Maia、克隆千雪、临时训 GPT-SoVITS。数据、训练、评测、接流式至少 2–3 天，还会挤掉联调时间。

## 10. 资源清单

### 项目与文档

- [Qwen3-TTS 官方仓库](https://github.com/QwenLM/Qwen3-TTS)
- [Qwen3-TTS CustomVoice 模型卡](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice)
- [CosyVoice 官方仓库](https://github.com/QwenAudio/CosyVoice)
- [IndexTTS2 官方仓库](https://github.com/index-tts/index-tts)
- [GPT-SoVITS 官方仓库](https://github.com/bilibili/GPT-SoVITS)
- [Fish Speech](https://github.com/fishaudio/fish-speech)
- [F5-TTS](https://github.com/SWivid/F5-TTS)
- [ChatTTS](https://github.com/2noise/ChatTTS)
- [EmotiVoice](https://github.com/netease-youdao/EmotiVoice)

### 教程

- [GPT-SoVITS 中文文档](https://github.com/bilibili/GPT-SoVITS/blob/main/docs/cn/README.md)
- [GPT-SoVITS 整合包部署教程（B 站）](https://www.bilibili.com/opus/894557412175904808)
- [GPT-SoVITS 调参实例（B 站）](https://www.bilibili.com/video/BV1cTWNz4EJd/)
- [CosyVoice2 情感/方言教程（B 站）](https://www.bilibili.com/video/BV1aPfTY8EmD/)
- [CosyVoice2 参数讲解（B 站）](https://www.bilibili.com/video/BV1EPkJYcEny/)
- [IndexTTS2 Mac 实测：情感向量、拼音精控（B 站）](https://www.bilibili.com/video/BV1QWWNzyEtz/)
- [IndexTTS2 全参数教程（B 站）](https://www.bilibili.com/video/BV1aCW4z3EaR/)
- [Qwen3-TTS 详解 + Mac 部署（B 站）](https://www.bilibili.com/video/BV1yPz6BJEwX/)
- [fine_tts_tutorial：调音/母带](https://zsc.github.io/fine_tts_tutorial/)
- [声音美化调音流程](https://zsc.github.io/voice_design_tutorial/html/chapter7.html)
- [TTS 音频后期链](https://kokoroweb.app/zh/blog/audio-post-processing-tts-browser-zh)

### 参考音频与评测

- [NVIDIA 零样本克隆参考音频指南](https://docs.nvidia.com/nim/speech/latest/tts/voice-cloning.html)
- [CloneTTS Reference Audio Guide](https://github.com/sipeter/CloneTTS/blob/master/docs/CloneTTS%20Reference%20Audio%20Guide.md)
- [Sarvam 参考音频准备](https://docs.sarvam.ai/api/api-guides-tutorials/voice-cloning/how-to/prepare-reference-audio)
- [tts-benchmark](https://github.com/navi-/tts-benchmark)
- [speechonnxmetrics](https://github.com/TigreGotico/speechonnxmetrics)

## 11. `model-lock` 需要记什么

最终锁定音色时，至少记录：

```text
model_id / model_version:
voice_id / voice_name:
provider: local / cloud
sample_rate:
parameters: top_k, top_p, temperature, repetition_penalty, speed, emo_alpha...
reference_audio: path + transcript + duration
postprocess: chain + LUFS + true peak
license / terms:
blind_test: listeners, sentences, median, misreads
date / owner:
```

如果最后选的是云端 `Maia` / `Chelsie`，把“云端依赖、免费额度、超时/失败转文字、离线兜底音色”一起写进去。不要让代码里写 `Serena`、演示时却播 `Maia`。

---

**最后提醒**：这份手册里的参数是起点，不是圣旨。每个音色都要用自己的 8 句试听集重新校准；盲听不过关，参数表再漂亮也没用。

---

## 附：按"声学规格"设计音色的流水线（2026-11-26）

目标不是复制某个人的嗓子，而是把"这一类声音"写成可检查的数字，再照着数字生成。四条命令串起来：

```bash
# 1. 量规格：每个语料文件夹算出音高/起伏/明亮度/气声/停顿/句长
python3 tools/voice/voice_spec.py /path/to/corpus/01 ... --limit 12 \
  --json-out assets/voice/analysis/fgo-style-spec.json

# 2. 设计：把规格翻译成中文描述，每个规格生成 N 个候选，同一把尺子打分
python3 tools/voice/design_batch.py --spec assets/voice/analysis/fgo-style-spec.json \
  --model-dir <VoiceDesign 模型目录> --out assets/voice/demos/design-batch \
  --per-profile 4 --device mps

# 3. 复量：换过尺子就重算，别沿用旧数字
python3 tools/voice/remeasure.py --dir assets/voice/demos/design-batch \
  --spec assets/voice/analysis/fgo-style-spec.json

# 4. 出试听页：每组一行，候选按与规格的距离排序
python3 tools/voice/design_batch_page.py --dir assets/voice/demos/design-batch
```

整个流程里，模型只收到**数字推出的中文描述**和一段文本，没有任何人的录音。

### 踩过的坑：全带质心量到的是"录音方式"，不是音色

语料是 64 kbps mp3，生成的是 24 kHz wav。用全带 `spectral_centroid`（0–12 kHz）量两边，
目标 2068–2606 Hz、候选 3017–3468 Hz，看起来"生成的全都太亮"。

先怀疑是码率低通，查频谱分布却发现不是：mp3 的 -40 dB 截止在 11.4 kHz，高频并没有被切掉。
差异集中在 7 kHz 以上——合成音和真实录音在高频上分配能量的方式本来就不同（齿音、气声、房间噪声
比例不一样），而质心对这一段极其敏感，整条曲线就被它拽走了。

**修法**：只在 100–7000 Hz 内算质心（`tools/voice/metrics.py:band_centroid`），单独比较语音主体
能量的分布。重算后目标 1656–2041 Hz、候选 1873–2288 Hz，从普遍 +800 Hz 收到多数 +50~300 Hz。

**教训**：一个统计量如果被某个和"音色"无关的因素主导，它量的就不是音色。先看分布，再定指标。

### 第二个坑：距离要用固定尺度，不能用相对误差

初版按 `|候选-目标| / 目标` 算距离。遇到气声目标 0.010 的规格（所罗门·Pretender），任何候选
都会在这一项上得 20 分以上，把音高、明亮度的差异全部盖住。改成按参考集各维度的离散度
（`design_search.SCALE`）归一，分数才可跨规格比较。

# 5. 换引擎：为什么不用 Qwen3-TTS，以及实时性到底差多少

## 结论先说

| 引擎 | 单句 RTF（M4 / MPS） | 3 秒回答要等 | 情绪可调 |
|---|---|---|---|
| Qwen3-TTS-12Hz-1.7B-Base | **41–54×** | 约 2 分钟 | **否**（接口只有参考音+文本） |
| IndexTTS-2.5（本地） | **12–14×** | 约 37 秒 | **是**（8 维情绪向量 / 情绪文本 / 情绪参考音） |

RTF = 合成耗时 ÷ 音频时长。1× 才是实时。

## 为什么必须换

Qwen3-TTS-Base 的 `generate_voice_clone` 没有任何情绪入口，素材B 音色包里的"平稳/关切/低落/愉悦"
只是文件名，八条音频的差别仅仅是随机种子。实测：同一句话逗号处固定停 1.02 秒（真人 0.2–0.4 秒），
音高跨度 115 Hz，怎么调配音都被听成"机器人"。

IndexTTS-2.5 把音色和情绪解耦：参考音决定"谁在说"，情绪向量决定"怎么说"。
同一参考音下带情绪的四条，音高跨度 131–145 Hz，逗号停顿自然落在 0.30–0.41 秒。

## 实时性：做不到，要按"预生成 + 逐句"设计

RTF 12–14 意味着说 1 秒要算 12–14 秒。这不是调参能解决的，是 M4 上 0.8B 自回归 + 流匹配 + 声码器
的实际速度。可行的三条路：

1. **预生成 + 缓存**（推荐，也是 `docs/demo-development-baseline.md` 里已定的"先按审核通过的短句逐句合成"）：
   演示脚本里的句子离线生成好，回放零延迟；自由回复走第 2 条并在之后复用缓存。
2. **按句合成 + 接受延迟**：3 秒句子约 37 秒。适合"回信"这种本来就在等待的场景，不适合快问快答。
3. **换硬件或换服务**：同模型在 4090 上 RTF 通常 0.3–0.5，3 秒句子 1–2 秒出。

## 情绪怎么跟用户输入走

IndexTTS-2.5 提供三个入口，按可控程度排序：

- `emo_vector`：8 维 `[喜,怒,哀,惧,厌,郁,惊,静]`。由回复生成器按话术类别选预设（开场/倾听/鼓励/收束），
  可审计、可复现。当前 11 句演示台词就按这个逐句设定，见 `assets/voice/demos/sucaiB/lines-v2.json`。
- `emo_text` + `use_qwen_emo=True`：把回复文本交给 0.6B 情绪模型自动推向量。省事，但不可控，
  且要多加载 1.2 GB。
- `emo_audio_prompt`：**直接拿用户这一轮的录音当情绪参考**，音色仍来自素材B。这是"根据 ASR 结果
  动态调节情感"最直接的实现——同一段音频既做识别，也做情绪参考；`emo_alpha` 控制强度。

## 采样与后处理的分工

- IndexTTS 输出**不要加提亮**：它 1–8 kHz 不比真人参考少（Qwen 那边才缺 5 dB）。
- 停顿超过 0.45 秒才用 `tools/voice/repair_flow.py` 剪到 0.34 秒；只剪静音，不动语音采样。
- **不要用 Praat 重合成加"情感"**：实测谐噪比 7.7 → 5.8 dB、帧间抖动 +48%，会把声音做坏。

# 6. 流式：本地能流，云端能流且能带音色，代价不同

## 本地确实能"流",但它救不了实时
IndexTTS-2.5 的 `infer(..., stream_return=True)` 返回生成器，可以边算边取块，
WebSocket 那层直接转发 PCM 就行。问题是**流式降低的是首包时间，不是总耗时**：
M4 上 RTF 12–14，一句 3 秒的话整体要 35–40 秒，首文字块本身也要数秒。
所以"流式"在 M4 上的实际体验是"先慢，后连续"，不是"说完就出声"。
要真实时只有两条路：

| 做法 | 首包 | 一句 3 秒的总耗时 | 代价 |
|---|---|---|---|
| M4 本地（现状） | 数秒 | 35–40 s | 无 |
| 4090 级 CUDA 本地 | < 1 s | 1–2 s | 要一台带 GPU 的机器 |
| 云端流式 API | 0.3–0.6 s | < 1 s | 联网 + 按字计费 + 录音出本机 |

## 云端复刻：上传一小段音频，换一个 voice id
主流国内厂商都支持"零样本/少样本声音复刻 + 流式合成"，不是训练，是把参考音登记成音色：

- **阿里云百炼 音色复刻**：`model=voice-enrollment`，传一段音频得到 voice id，
  再绑定 `target_model`（`qwen-audio-3.1-tts-flash` 主低延迟，或 CosyVoice）合成；
  支持流式输出，单账号上限 1000 个音色。音色与 target_model 绑定，换模型要重新复刻。
- **火山引擎 豆包 声音复刻 2.0**（`seed-icl-2.0`）：5–10 秒音频即可，单向流式 HTTP/WebSocket，
  第三方实测首包 300–400 ms。情绪控制靠 `context_texts`（语音指令）与情感标签
  （`[happy]` 这一类）；`tone_fidelity=true` 会把参考音的情感/韵律/口音一起还原，
  但它只支持同语种、且不支持双向流。注意 2.0 系列**不支持**旧的 `emotion` / `emotion_scale`
  参数——文档写了也可能是透传后被忽略。
- 腾讯云、讯飞、MiniMax 都有同类能力，参数名不同。

## 三条必须先摆上桌的代价
1. **录音会离开这台机器**。声音复刻是把参考音上传到厂商服务器并长期保留。素材B 是动画台词，
   厂商条款要求承诺拥有该声音的授权；上传无授权素材可能违反条款、账号受限。
   这一步必须由项目组明确同意后再做，不能默认为之。
2. **按字计费**，火山约 1.3 元/千字，音色槽位另算；课程演示的量级够用，但不是免费。
3. **依赖网络**。课堂演示断网就直接没有声音，本地模型反而是保底。

## 但云端大概率不会解决"一个字一个字"
零样本克隆会连参考音的**说话方式**一起抄走。ref-B.wav 是 7.5 秒的起誓台词：
7.46 秒里有 1.75 秒是停顿，语气是庄重的念白。用它做参考，本地和云端听到的
都会是"一字一顿的念白味"。所以换引擎之前，先把参考音本身换成连续、口语的样本试一遍；
这一步免费、十分钟能出结论。

### 实测："一个字一个字"到底差在哪
拿实验室现场录制的三条真人录音做尺子，同一把尺子量我们的输出：

| 指标 | 我们的 03/10/11 | 真人 横川/七仔/小施 | 结论 |
|---|---|---|---|
| 语速（字/有声秒） | 5.0–6.2 | 5.5 上下 | 不比人慢 |
| 音高 P5–P95 跨度 | 8.4–11.3 半音 | 5.4–13.8 半音 | 不比人平 |
| 段间凹坑密度（个/秒） | 6.9–8.5 | 7.2–8.4 | 一样 |
| **凹坑间隔的变异系数** | **0.55** | **1.02–1.45** | **差在这里** |

前三项都在人类区间内，唯一稳定分离的是节奏的**规律性**：真人两个音节之间的间隔长短很乱，
我们是几乎等距的一拍一拍——这就是"一个字一个字"的听感来源。
`tools/voice/rhythm.py` 是对着这一条做的（Praat 时长层、2.5 ms 网格，不动音高与频谱），
±20% 的随机时长只能把 0.55 推到 0.61，要追平真人的 1.0 以上幅度会大到听出醉意。
换句话说：在这套参考音上，本地已经接近天花板了。

## 云端为主：价格、以及"更像人"到底能不能调出来（2026-11-27）

### 先回答贵不贵

百炼按**输入字符**计费，输出不计费；CosyVoice 系列**创建音色本身免费**，
每个账号上限 1000 个自定义音色。

| 模型 | 价格（华北2·北京） | 备注 |
|---|---|---|
| `cosyvoice-v3-flash` | 1 元 / 万字符 | 无免费额度 |
| `cosyvoice-v3.5-flash` | 0.8 元 / 万字符 | 北京有 1 万字符 / 90 天免费额度 |
| `cosyvoice-v3.5-plus` | 1.5 元 / 万字符 | 同上有免费额度 |
| `cosyvoice-v3-plus` / `v2` | 2 元 / 万字符 | |

换算到我们自己的量：13 句台词共 **162 个字符**，整套重录一遍是
**0.013–0.024 元**。一场 20 轮的对话大约 600 字符，**0.05–0.09 元**；
一百场演示也就 **5–9 元**。所以"反复重录到满意"在经济上完全不成立为顾虑，
真正的成本是调音色的时间。

来源：<https://help.aliyun.com/zh/model-studio/cosyvoice-v3-flash>、
<https://help.aliyun.com/zh/model-studio/model-pricing>、
<https://help.aliyun.com/zh/model-studio/voice-cloning-and-voice-design>
（价格页另有一句"复刻音色收取一次性费用"，与控制台导出账单口径不一致，以实际账单为准。）

### 指令确实能把"一个字一个字"改掉

同一段参考音、同一个 voice id、同一个模型，只改 `instruction`，
用 `tools/voice/cloud_variants.py` 生成、`export_variants.py` 出页
（`~/Desktop/试听-云端更像人`）。三种句子（01 开场、10 关心、11 安抚）平均：

| 音色 | 不发指令 | 口语指令 | 场景指令 | 节奏松指令 | 真人参照 |
|---|---|---|---|---|---|
| 01 | 0.72 | 0.81 | 0.97 | **1.03** | 1.02–1.45 |
| 13 | 0.68 | 0.82 | 0.88 | **0.90** | |
| B | 1.08 | 0.90 | 1.05 | **1.05** | |

（数字是凹坑间隔的变异系数，越大越像人。本地模型在这条指标上是 0.55，
指令怎么调都在 0.7–0.8；云端一句"节奏随意一点，别那么均匀"就能推到 1.0 附近。）

要付的代价是**时长**：同样的三句，"节奏松"从 8.1 秒变成 12.9 秒，
多出来的全是停顿。听起来会不会拖沓，只能由耳朵决定——所以那一页做成
一句三个按钮加一个"连播"。

### 两条已经排除的路

* **长参考音**：把 13 的参考音从 3.4 秒接到 10 秒重新登记，
  生成结果与短参考**逐字节相同**（`md5` 一致）。对这个音色，参考长短不是变量。
  01 的长参考（11 秒）输出不同，但也只是另一版，不是"更像"。
* **指令里写语速**：早前实测过，"语速稍慢"单独出现就把 3.6 秒的句子拉到 5.7 秒。
  语速交给 `speech_rate`（几个百分点），指令只描述状态。

### 待定

`cosyvoice-v3.5-flash` / `v3.5-plus` 用 13 的参考音登记成功，三句已生成。
听感之前先看指标：v3.5 把 13 的中位音高从 214 Hz 推到 226–252 Hz，
偏移比 v3 大，音色是否还是 13 要以试听为准。价格反而更低（0.8 元/万字符）。
