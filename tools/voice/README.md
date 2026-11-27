# 语音试听小工具

五个只依赖 Python 标准库的小工具（生成器额外依赖 qwen-tts 环境），配合 [语音调音手册](../../docs/voice-tuning.md) 使用。现成样例见 [`assets/voice/demos/qwen3tts/index.html`](../../assets/voice/demos/qwen3tts/index.html)（本地真模型）和 [`assets/voice/demos/edge/index.html`](../../assets/voice/demos/edge/index.html)（Edge 参考音色）。

## 1. 检查 WAV 样本

```bash
python3 tools/voice/analyze_audio.py assets/voice/samples
```

检查采样率、声道、时长、峰值、RMS、削波和静音比例。默认按 24 kHz 单声道检查；不符合会标 `CHECK`，退出码为 1。

只支持 16-bit PCM WAV。Qwen3-TTS 的 `soundfile` 输出可能是 float32/24-bit，其他格式先用 `afconvert` 或 `ffmpeg` 转：

```bash
afconvert -f WAVE -d LEI16@24000 -c 1 input.aiff output.wav
```

```bash
ffmpeg -i input.wav -ar 24000 -ac 1 -c:a pcm_s16le output.wav
```

## 2. 统一响度

```bash
python3 tools/voice/normalize_wav.py assets/voice/demos/edge \
  --out assets/voice/demos/edge/normalized --force
```

把 16-bit PCM WAV 统一到目标 RMS（默认 -20 dBFS）并限制峰值（默认 -1 dBFS）。这是快速 A/B 用的 RMS 归一化，不是 LUFS；正式交付仍按手册用 `ffmpeg loudnorm` 或 `pyloudnorm` 做到 -16 LUFS。

## 3. 生成盲听页面

输入目录按候选分文件夹，每个候选放同一组句子：

```text
assets/voice/samples/
  Maia/
    01.wav ... 08.wav
  Chelsie/
    01.wav ... 08.wav
  Serena/
    01.wav ... 08.wav
```

生成：

```bash
python3 tools/voice/make_blind_test.py \
  --input assets/voice/samples \
  --out blind-test \
  --seed 7
```

打开 `blind-test/index.html`，让 3 名组员独立打分。每句的候选顺序会随机化，页面只显示 A/B/C，不显示音色名。

评分完成后点“下载结果 JSON”，把 JSON 交给组织者。`blind-test/mapping.json` 保存 A/B/C 对应的真实音色，**评分结束前不要让听者打开**。

## 4. 用本地 Qwen3-TTS 生成样音

需要 `qwen-tts` + `torch`；macOS 上用带 `_lzma` 的 Python（Homebrew 的可以，pyenv 某些构建不行，`librosa → pooch` 会导入失败）。

```bash
python3 tools/voice/synth_qwen_samples.py \
  --model-dir ~/.cache/modelscope/models/Qwen--Qwen3-TTS-12Hz-1.7B-CustomVoice/snapshots/master \
  --out assets/voice/demos/qwen3tts \
  --speakers Serena,Vivian,Dylan,Uncle_Fu \
  --styles normal,gentle,slow \
  --device mps
```

1.7B 才有 instruct；0.6B 的 CustomVoice 会忽略 instruct，做不了语气控制。M4/MPS/bfloat16 约 20 倍实时（11 秒音频 2–6 分钟）。脚本会写 `meta.json`，记录 instruct、采样参数、seed 和逐条耗时。

MPS 上若报 `Passed CPU tensor to MPS op`，是包装器缓存了旧的 `model.device`；脚本里已在 `.to(device)` 后同步。

## 5. 生成命名试听页

`<speaker>-<style>.wav` 布局，页面显示真实音色名（不做匿名，用于调音对比）：

```bash
python3 tools/voice/make_audition_page.py \
  --dir assets/voice/demos/qwen3tts \
  --title "Qwen3-TTS 本地音色试听"
```

如果目录里有 `meta.json`，页面会自动带出测试文本。

## 6. 建议流程

1. 生成 3 个候选 × 8 句，统一到同一响度（-16 LUFS 左右）。
2. `analyze_audio.py` 检查格式、峰值、静音。
3. `make_blind_test.py` 生成匿名页面。
4. 3 人盲听，自然度中位数 ≥ 4/5，且无影响句义的错读。
5. 定稿音色再做 4 种语调的并排试听。
6. 把模型、音色、参数、参考音频、许可写进 `docs/model-lock.md`。
