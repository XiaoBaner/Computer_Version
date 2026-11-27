# 可合法用于语音克隆/训练的公开音源清单

调研时间：2026-11-26。判据只有一条：**条款是否明确允许第三方把音频用于 AI 模型训练**。没写 ≠ 允许，这类一律记为"排除"。

## 结论先行

**没有知名声优开源自己的声音。** 这是结构性的：知名度来自事务所合约，声音是商业资产。调研中确认的典型反例是季冠霖（《甄嬛传》甄嬛、《三生三世》白浅的配音）公开表示拒绝 AI 使用其声音。

能用的是三类：职业但不出名的声素材工房、声优本人签约的合成角色、以及学术语音库。

## 一、可用（条款明确允许）

### 1. あみたろの声素材工房

- 地址：https://amitaro.net/voice/voice_rule/
- 语种：日语｜规模：3408 句台词 + ITA/MANA/AMITARO 三套带标注语料 + 直播音声
- 条款原文（2026-10-23 版）：「このページの規約を守れば、音声合成モデルやAIボイスチェンジャーモデルの学習データとしても使えます。学術研究もOKです。」
- 英文条款亦写明："Training on these materials to create your own AI or voice synthesis models is permitted."
- 条件：**必须署名**（「学習データ：あみたろの声素材工房（https://amitaro.net/）」）；禁止直链；禁止转售；禁止少儿不宜与政治宗教用途
- 注意：日文条款与英文条款在"模型分发"上有冲突，日文条款声明日文版优先，以日文为准

**MANA 语料实测（2026-11-26）**：MANA Corpus 共 228 句，一句一文件，文件名即句 ID（`AISATSU_001.wav`），文本表可在 `github.com/shirowanisan/coeiroink-corpus-manager/mana-corpus/mana-corpus.txt` 取到（每行 `ID:テキスト`）。**这是本次唯一一份"带准确转写 + 明确允许 AI 训练 + 同一人同一话筒"的中文以外的单说话人语料**，日语。

同一句（QUESTION_007「高いところに行くけど、大丈夫そう？」）六种表演风格实测：

| 风格 | zip | 平均 F0 | F0 sd | 浊音比 | 说明 |
| --- | --- | ---: | ---: | ---: | --- |
| 通常 Normal | `MANAcorpus_amitaro_1.1.zip` | 297.6 Hz | 62.3 | 86.3% | 为 TTS 优化，吐字清楚 |
| 夜更かし Yofukashi | `MANAcorpus_amitaro_yofukashi.zip` | **245.1 Hz** | **72.4** | 76.6% | 自称「深夜电台口吻」，音区最低、起伏最大 |
| るんるん Runrun | `..._runrun.zip` | 341.2 Hz | 57.2 | 72.9% | 雀跃 |
| ぷんすか Punsuka | `..._punsuka.zip` | 296.1 Hz | 81.5 | 77.1% | 假装生气 |
| ささやきA/B | `..._sasayaki_A/B.zip` | 253.0 / 251.1 Hz | 50/49.5 | 44/47.5% | 耳语，浊音比例过低，不能用于克隆或微调 |

下载地址形如 `https://amitaro.net/download/corpus/MANAcorpus_amitaro_yofukashi.zip`（需带 Referer `https://amitaro.net/voice/corpus-list/mana-corpus/`）。试听样音在 `https://amitaro.net/download/corpus/samples/MANA_yofukashi_QUESTION_007.wav`。

注：ささやき系列的 zip 内文件名就是句子文本本身（Shift-JIS/cp932 编码，`unzip` 里显示成 `?`，需按 cp932 解码）；MANA 主系列的 48k/44.1k 目录里另有 62 条追加录音。

### 2. KOEBASE（声べーす！）— 海野あなご

- 地址：https://www.koebase.com/guidelines.php
- 语种：日语｜规模：单条 35 秒
- 条款：平台按条目标注许可证；该条由职业声优本人标注「商用利用可（CC BY 4.0）、AI学習可」
- 条件：CC BY 4.0，署名
- 价值：这是本次唯一一个"职业声优本人明确标注允许 AI 学习"的样本

### 3. つくよみちゃんコーパス

- 地址：https://tyc.rei-yumesaki.net/material/corpus/
- 语种：日语｜规模：Vol.1 100 句（96 kHz/24-bit），1500 句版需邮件申请
- 条款：「本品や、本品の声質を用いて合成された音声を外部に公開しない場合は、何の制限もありません」；公开使用需署名；「本品の声質を再現した音声合成ソフトを配布・販売することも可能です」
- 条件：**用在「つくよみちゃん」以外的角色上属于【要相談案件】**，须先邮件取得同意；禁止再分发语料本体

### 4. AI声優 ITAコーパス（朱花・金苗）

- 地址：https://aiseiyou.com/
- 语种：日语｜规模：ITA 语料
- 条款：【利用可能範囲】含「音声データを学習元とした音声合成モデル/ライブラリの作成」
- 条件：**训出的模型不能作为 AI声優 以外角色的声音分发**；以 AI声優 角色名义分发须事先商谈

### 5. MMVC 官方配布音声（ずんだもん 等）

- 地址：https://github.com/isletennos/MMVC_Trainer
- 语种：日语
- 条款：允许训练，但**限定在 MMVC 工具内使用**
- 条件：不可导出为通用模型

### 6. SHAREVOX 引擎条款

- 地址：https://www.sharevox.app/terms
- 语种：日语
- 性质：卖的是引擎，输出音频的使用有明确规定；未提供原始素材用于外部训练

### 7. AISHELL-3（中文，本项目当前在用）

- 地址：https://openslr.org/93/ ；ModelScope 镜像 `speech_tts/AISHELL-3`
- 语种：普通话｜规模：218 人 / 约 85 小时 / 44.1 kHz
- 许可：开源语音库，面向语音合成研究
- 注意：镜像 zip 内只有 wav 与 interval 标注，**没有文本转写**，做微调需自行 ASR 或另找 content.txt
- 用法：19 GB 整包不必下载，可用 `tools/voice/remote_zip.py` 按需取单个说话人

### 8. CSMSC（标贝中文标准女声）

- 数据说明书：https://weixinxcxdb.oss-cn-beijing.aliyuncs.com/gw/标贝开源数据产品说明书_【中文标准女声音库】.pdf
- 语种：普通话｜规模：单女声 10000 句 / 48 kHz
- 条款原文：「本数据仅支持非商用！」「科研，可用于语音合成系统模型训练」
- 状态：官方 zip 下载链接当前 404，需另找镜像

## 二、排除（条款未允许或明确禁止）

| 来源 | 原因 |
| --- | --- |
| 雛乃木まや フリー音声 | 条款只字未提 AI，未写 ≠ 允许 |
| 栗巣みゆ フリー素材 | 同上 |
| 声のプロ集団オンエアー | 同上（2024-11-05 版条款） |
| りりぃの声 / こんとどぅふぇ | 同上 |
| 甘声屋さん | 条款明确禁止 |
| MoeSpeech 数据集 | 声称开源，核实为未经同意的收集 |
| 季冠霖 | 本人公开拒绝 AI 使用其声音 |

## 三、对我们的意义

1. **中文项目优先用 AISHELL-3 或 CSMSC**：不存在跨语种口音问题
2. **日语素材可以用，但要接受跨语种风险**：克隆到中文时音色可迁移，口音需实测
3. **署名是硬性成本**：あみたろ、つくよみちゃん、KOEBASE 都要求署名，报告与演示页里要写
4. **"知名声优"这条路不存在**，写进中期报告比含糊带过更有说服力

## 四、复现本次调研的方法

```bash
# 取某个说话人的全部录音（zip 内路径需先用 list 查）
~/.cache/qwen3tts-venv3/bin/python tools/voice/remote_zip.py list \
  --url "<zip 直链>" --match "SSB0038/wav/" --limit 5

~/.cache/qwen3tts-venv3/bin/python tools/voice/remote_zip.py fetch \
  --url "<zip 直链>" --match "SSB0038/wav/.*\.wav$" --out assets/voice/refs/aishell3_0038
```
