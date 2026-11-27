# 「普绪喀的回信」语音合成技术路线：中期报告

**汇报人**：王皓（语音线）
**日期**：2026-11-25
**范围**：语音合成（TTS）从选型到当前状态的全部实验记录

---

## 摘要

本阶段的目标是给对话系统配一副**听起来像真人、情绪不假、语速自然**的中文女声。为此在本地部署了 Qwen3-TTS 系列三个 1.7B 模型（CustomVoice / VoiceDesign / Base），并对照了云端 qwen3-tts-flash 与若干开源仓库的示例音源，共完成 6 轮实验、约 60 条样音。

三条可复现的结论：

1. **文字描述造音色不可控**：同一句描述下微调措辞，平均基频（F0）漂移可达 70 Hz，相当于换了一个人。
2. **零样本克隆能锁死音色**：以同一段参考音生成三条不同台词，F0 落在 290.3 / 257.2 / 290.0 Hz（参考 285.6 Hz）。
3. **参考音决定的不只是音色，还有语速**：参考音每秒 4.0 字，克隆输出 3.3–3.8 字/秒；参考音 2.1 字/秒，输出 2.6–3.2 字/秒。

结论直接决定了下一步：**停止用文字描述造声音，改为"合法参考音克隆 + 单说话人微调"**。

---

## 1. 问题定义与约束

| 项 | 要求 | 来源 |
| --- | --- | --- |
| 语言 | 普通话 | 项目设定 |
| 性别/音色 | 女声，中低音区，温暖不甜腻 | 用户听感反馈 |
| 语速 | 接近正常对话（5–6 字/秒为上限，不低于 4 字/秒） | 用户反馈"语速太慢" |
| 情感 | 平稳、关切、低落三类为主；不允许"播音腔" | 用户反馈"做作""情感不连续" |
| 部署 | 本地推理，可离线 | 课程要求 |
| 交付格式 | 24 kHz 单声道 16-bit WAV | 系统接口约定 |

排除项：不使用来源不明的商业动画/游戏配音作为训练或克隆素材。

---

## 2. 实验环境

### 2.1 硬件与系统

| 项 | 值 |
| --- | --- |
| 机型 | Apple Silicon（M4） |
| 内存 | 24 GB 统一内存 |
| 系统 | macOS 27.2.1（Darwin 27.2.0） |
| 加速后端 | PyTorch MPS，bfloat16 |
| 实测生成速度 | 约 20× 实时（3.3 秒音频耗时 93 秒，含模型加载） |

### 2.2 软件栈

| 组件 | 版本 |
| --- | --- |
| Python | 3.11.16（Homebrew `/opt/homebrew/bin/python3.11`） |
| 虚拟环境 | `~/.cache/qwen3tts-venv3` |
| PyTorch | 2.15.0（MPS 可用） |
| Transformers | 4.57.3 |
| qwen-tts | 0.1.1 |
| accelerate | 1.12.0 |
| ModelScope | 1.40.3 |
| librosa | 0.11.0 |
| NumPy | 2.4.6 |
| 音频工具 | 系统自带 `afconvert`（未安装 ffmpeg / sox） |

> 环境选择说明：系统 pyenv 的 Python 3.11.11 缺少 `_lzma` 模块，librosa 依赖链（pooch）会直接导入失败，因此改用 Homebrew 的 3.11.16 建独立 venv。虚拟环境不继承系统 site-packages，避免 numpy/sklearn 的 ABI 冲突（曾出现 `numpy.dtype size changed`）。

### 2.3 模型清单

全部通过 ModelScope 下载（Hugging Face 在本机网络不可达，`curl` 返回 000）。

| 模型 | 体积 | 用途 | 许可 |
| --- | ---: | --- | --- |
| Qwen3-TTS-12Hz-1.7B-CustomVoice | 4.2 GB | 9 个预设音色，支持 instruct | Apache-2.0 |
| Qwen3-TTS-12Hz-1.7B-VoiceDesign | 4.2 GB | 文字描述生成音色 | Apache-2.0 |
| Qwen3-TTS-12Hz-1.7B-Base | 4.2 GB | 零样本克隆 + 微调底座 | Apache-2.0 |
| IndexTTS-2.5 | 5.1 GB | 情感可控方案（仅下载，未启用） | 研究用途 |
| speech_paraformer-large（ASR） | 848 MB | 合成结果回读校验 | Apache-2.0 |

### 2.4 代码仓库

| 仓库 | 许可 | 在本项目中的角色 |
| --- | --- | --- |
| QwenLM/Qwen3-TTS | Apache-2.0 | 推理（`qwen-tts` 包）与官方微调脚本（`finetuning/sft_12hz.py`、`prepare_data.py`、`dataset.py`） |
| QwenAudio/CosyVoice | Apache-2.0 | 参考音 `asset/zero_shot_prompt.wav` |
| SWivid/F5-TTS | MIT | 参考音 `basic_ref_zh.wav` |
| OpenBMB/VoxCPM | Apache-2.0 | 参考音 `reference_speaker.wav` |
| index-tts/index-tts | 研究许可 | 情感可控备选，仅评估未启用 |
| amitaro.net（声优素材工房） | 站点自有条款，明确允许 AI 训练、需署名 | 微调数据候选 |

### 2.5 自研工具

| 脚本 | 作用 |
| --- | --- |
| `tools/voice/synth_qwen_samples.py` | CustomVoice 批量生成，支持逐条指定 speaker / instruct / 采样参数 |
| `tools/voice/synth_voicedesign.py` | VoiceDesign 按描述批量生成 |
| `tools/voice/lock_voice.py` | Base 零样本克隆，支持 `--x-vector-only` |
| `tools/voice/synth_dashscope.py` | 云端 qwen3-tts-flash 调用，仅从环境变量读密钥 |
| `tools/voice/normalize_wav.py` | 归一化到 −20 dBFS RMS、峰值 ≤ −1 dBFS |
| `tools/voice/measure_voice.py` | librosa pyin 测 F0 均值/标准差、浊音比例 |
| `tools/voice/analyze_audio.py` | 时长、语速、停顿统计 |
| `tools/voice/make_audition_page.py` | 生成命名试听页（横向对比多音色） |
| `tools/voice/make_blind_test.py` | 生成盲听页（隐藏候选名） |
| `tools/voice/segment_takes.py` | 真人录音自动切句 |
| `tools/voice/find_ref_wavs.py` | 扫描开源仓库中的参考音并核对许可 |

---

## 3. 技术路线与实验记录

四个阶段，按时间顺序。每阶段的失败原因直接决定下一阶段的做法。

### 3.0 实验轮次总表

| 轮次 | 模型 | 方法 | 关键参数 | 样本 | 产出目录 | 结论 |
| --- | --- | --- | --- | ---: | --- | --- |
| R1 | Qwen3-TTS-1.7B-CustomVoice | 预设音色 + instruct | top_k 50, top_p 0.9, T 0.8 | 9 音色 × 3 句 | `demos/qwen3tts` | 音色可用，念稿感重 |
| R2 | CustomVoice (Serena) | 四种语调对照 | T 0.7–0.9 | 12 | `demos/qwen3tts-serena-tones` | 语调可调，音色仍甜 |
| R3 | CustomVoice (Serena) | 短句重跑 | T 0.75 | 6 | `demos/qwen3tts-serena-short` | 短句更自然，长句拖 |
| R4 | 1.7B-VoiceDesign | 文字描述造音色 | T 0.9, top_p 0.95 | 10 | `demos/voicedesign`、`demos/linwan-*` | 音色随措辞漂移 39–119 Hz |
| R5 | 云端 qwen3-tts-flash | 平台音色对照 | 默认 | 6 | `demos/cloud` | 自然度基准，不可离线 |
| R6 | 1.7B-Base | 零样本克隆（3 组参考） | T 0.75, top_p 0.9, seed 11 | 6 | `demos/refclone`、`ref-F5`、`ref-VoxCPM` | 音色锁定，语速随参考 |
| R7 | 1.7B-Base + 后处理 | 保持音高变速 | 1.12× / 1.22× | 3 | `demos/warm-fast` | 音色不变，语速可控 |
| R8 | 0.6B/1.7B-Base | 单说话人微调（计划） | lr 2e-5, bs 2, epoch 3 | 待定 | — | 见 `voice-finetune-plan.md` |

### 3.1 阶段一：预设音色 + 自然语言指令（CustomVoice）

**做法**：用 9 个预设音色（Serena、Vivian、Uncle_Fu、Dylan、Eric、Ryan、Aiden、Ono_Anna、Sohee），配合中文 instruct 控制情绪与语速。

**参数**：`top_k=50, top_p=0.90, temperature=0.80, repetition_penalty=1.05`。

**示例指令**：
> 温柔、平稳，语速比正常略慢，句间留出约 300 毫秒的空白。

**结果**：音色本身可用，但被评价为"豆包感拉满"——句尾音高走向高度一致，句子之间的停顿是等长的，情绪像是贴在句子表面而不是从内容里长出来的。

**结论**：预设音色的音色库是固定的 9 个，无法调出目标音色；instruct 能改语气，但改不动"念稿感"。

### 3.2 阶段二：文字描述生成音色（VoiceDesign）

**做法**：`generate_voice_design(text, instruct)`，用一段人物描述代替音色选择。

**示例描述**：
> 27 岁女性，中音区，声音暖，语速接近正常，像在安静房间里和朋友说话，不表演，不做作，不像客服。

**测量结果**（同一模型、同一随机种子、只改描述措辞）：

| 版本 | 平均 F0 | 变化 |
| --- | ---: | --- |
| 初版描述 | 206 Hz | — |
| 改写文本、不改音色描述 | 276 Hz | +70 Hz |
| 加入场景潜台词 | 245 Hz | +39 Hz |
| 描述里改成"语速正常偏快" | 325 Hz | +119 Hz |

**结论**：VoiceDesign 中**音色与语速、情绪共用同一段描述**，改任何一项都会牵动音色。这不是参数问题，是接口设计问题。四次实验里没有一次能在保持音色的前提下改动语速。

### 3.3 阶段三：零样本克隆（Base）

**做法**：`create_voice_clone_prompt(ref_audio, ref_text)` 建立音色 prompt，再对每条台词调用 `generate_voice_clone`。

**参数**：`temperature=0.75, top_p=0.9, seed=11`。

**三组参考音对照**（同一组台词：11 字短句 / 24 字长句）：

| 参考音 | 来源 | 时长 | 参考 F0 | 克隆 F0（短/长） | 参考语速 | 克隆语速（短/长） |
| --- | --- | ---: | ---: | --- | ---: | --- |
| CosyVoice 示例 | Apache-2.0 | 3.48 s | 285.6 Hz | 290.3 / 257.2 | 4.0 字/秒 | 3.35 / 3.75 |
| F5-TTS 中文示例 | MIT | 6.76 s | 263.5 Hz | 227.9 / 257.8 | 2.1 字/秒 | 2.59 / 3.16 |
| VoxCPM 示例 | Apache-2.0 | 12.80 s | 374.3 Hz | 335.7 / 367.2 | 偏快 | 3.35 / 4.05 |

**结论**：

1. **音色锁得住**：同一参考音的三条输出 F0 落在 ±33 Hz 内，远好于 VoiceDesign 的 ±70–119 Hz。
2. **语速是继承的**：最慢的参考（2.1 字/秒）产出最慢的克隆（2.6 字/秒），最快的参考产出 4.1 字/秒。**参考音同时决定了音色和语速**，这解释了为什么改 prompt 治不好"语速太慢"。
3. **跨语种风险**：以上参考音均为日语母语者，克隆到中文时音色可迁移，但存在轻微口音残留的可能，需在验收时专门听。

### 3.4 阶段四：工程化后处理

**归一化**：所有交付音频统一到 −20 dBFS RMS、峰值 ≤ −1 dBFS。听觉上响度一致，才能做 A/B。

**变速**：为解决"语速太慢"，对照了两种做法。

| 文件 | 做法 | 时长 | 平均 F0 | 音色是否保持 |
| --- | --- | ---: | ---: | --- |
| 原始 | — | 7.84 s | 252 Hz | — |
| 原生加速 | 改 instruct 为"语速正常偏快" | 6.88 s | **325 Hz** | 否（+73 Hz） |
| 变速 1.12× | 相位声码器时间伸缩 | 7.00 s | 252 Hz | 是 |
| 变速 1.22× | 同上 | 6.43 s | 252 Hz | 是 |

**结论**：模型原生提速会换人；保持音高的时间伸缩不改音色，1.1–1.25× 之间听不出处理痕迹，超过 1.3× 开始出现金属感。

### 3.5 云端对照（DashScope）

为验证"本地开源模型的差距在哪里"，用云端 `qwen3-tts-flash` 生成同一组台词，音色为 Maia（四月）与 Chelsie（千雪）。

**结果**：云端音色的自然度明显高于本地 VoiceDesign，但 1）模型不支持 instruct，情绪不可控；2）音色属于平台资产，无法离线部署；3）按字符计费。因此仅作为听感基准，不进入最终方案。

### 3.6 阶段五（进行中）：单说话人微调

见 [`voice-finetune-plan.md`](./voice-finetune-plan.md)。核心结论：官方 `finetuning/sft_12hz.py` 支持单说话人微调，数据要求 24 kHz 单声道 + 逐字准确文本 + 全数据集统一参考音；1.7B 全参在 24 GB 内存上受限，先以 0.6B 全参跑通链路，再评估 1.7B + LoRA。

---

## 4. 关键工程问题与修复

| 问题 | 现象 | 原因 | 修复 |
| --- | --- | --- | --- |
| pyenv Python 不可用 | `ModuleNotFoundError: No module named '_lzma'` | Python 编译时未链接 liblzma，librosa→pooch 导入失败 | 改用 Homebrew Python 3.11.16 建独立 venv |
| 依赖 ABI 冲突 | `numpy.dtype size changed` | 虚拟环境继承系统 site-packages，numpy/sklearn 版本错配 | 建干净 venv，不继承系统包 |
| MPS 张量错误 | `RuntimeError: Passed CPU tensor to MPS op` | 模型包装类在 `.to("mps")` 之前缓存了 `device` 属性 | 手动同步 `model.device = torch.device("mps")` |
| 模型下载失败 | `curl` 返回 000 | Hugging Face 在本机网络不可达 | 改用 ModelScope `snapshot_download` |
| 云端指令模型不可用 | HTTP 404 `Model not exist` | 该账号未开通 `qwen3-tts-instruct-flash` | 降级用 `qwen3-tts-flash`，instruct 不生效并如实记录 |
| 音频处理工具缺失 | `sox: command not found` | 系统未安装 ffmpeg/sox | 统一用系统自带 `afconvert` 做格式转换 |

---

## 5. 测量方法

所有"好听/不好听"的判断都落在这三个可复现的量上：

| 指标 | 工具 | 定义 | 用途 |
| --- | --- | --- | --- |
| 平均 F0 / F0 标准差 | librosa `pyin` | 逐帧基频，取浊音帧统计 | 判断音色是否漂移（同性别人声 ±15 Hz 内视为同一音色） |
| 语速 | 字数 ÷ 有效语音时长 | 只算汉字，不含标点 | 判断节奏是否接近自然对话（4.5–5.5 字/秒为常态） |
| 时长 / 停顿分布 | `analyze_audio.py` | 静音段 > 150 ms 计为停顿 | 判断"念稿感"（等长停顿是合成音的典型特征） |

主观评价采用盲听：同一组 8 句，隐藏候选名，只问"哪条最像同一个人在自然说话"，不问"哪个更好听"。

---

## 6. 结论与下一步

### 已确认

1. **文字描述生成音色的路线不适合本项目**：音色与语速/情绪耦合，无法在保持音色的前提下调节表达。
2. **零样本克隆是当前最可靠的手段**：音色稳定，5 秒参考即可切换；但语速与语气被动继承参考音。
3. **表现力的上限由数据决定**：克隆与微调都不能凭空生成情绪，只能复制参考音/训练数据里本来就有的表达。

### 下一步（两周内）

| 周次 | 任务 | 交付物 |
| --- | --- | --- |
| 第 1 周 | 确定合法参考音（授权素材或团队真人录音） | 参考音 1 条 + 授权说明 |
| 第 1 周 | 微调链路跑通（0.6B，100 条小样本） | 微调 checkpoint + 链路记录 |
| 第 2 周 | 全量微调（800 条以上） | 训练日志 + 三方盲听结果 |
| 第 2 周 | 接入对话系统，做打断与流式延迟测试 | 端到端演示 |

### 风险

- 若参考音只有日语素材，跨语种克隆的中文口音需专门评估；
- 24 GB 内存限制 1.7B 全参微调，0.6B 的效果可能不足以支撑长句；
- 微调无法解决"演法不自然"，最终质量仍取决于录音本身。

---

## 附录 A：交付文件

```
assets/voice/
├── refs/                     参考音（含来源说明）
├── demos/clone-sweep/        三组参考音克隆对照（试听页 + 9 条音频）
├── demos/refclone-compare/   真人参考 vs 克隆 vs 旧版对照
├── demos/warm-fast/          变速对照（含测量表）
├── demos/qwen3tts*/          CustomVoice 与 VoiceDesign 各轮样音
└── demos/cloud/              云端音色对照

tools/voice/                  11 个自研脚本，见 §2.5
docs/voice-tuning.md          调参与模型选型笔记
docs/voice-finetune-plan.md   微调方案
docs/voice-recording-kit.md   真人录音规范与授权模板
```

## 附录 B：复现一条样音

```bash
~/.cache/qwen3tts-venv3/bin/python tools/voice/lock_voice.py \
  --model-dir ~/.cache/modelscope/models/Qwen--Qwen3-TTS-12Hz-1.7B-Base/snapshots/master \
  --ref-audio assets/voice/refs/cosyvoice_zero_shot_prompt.wav \
  --ref-text "希望你以后能够做的比我还好呦。" \
  --plan-json assets/voice/demos/refsweep-lines.json \
  --out assets/voice/demos/<输出目录> \
  --device mps
```
