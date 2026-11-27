#!/bin/bash
# IndexTTS-2.5：同一个参考音、同一句话，只改情绪设定。
# 第一次运行会自动下载辅助模型（w2v-bert-2.0 / campplus / bigvgan，约 2.5 GB），
# 之后就走本地缓存。
set -e
cd /Users/haowang/Desktop/Computer_Version/.claude/worktrees/cv-division-discussion-41814d

~/.cache/indextts-venv/bin/python -u tools/voice/indextts_batch.py \
  --src /Users/haowang/.cache/indextts-src/src \
  --model-dir /Users/haowang/.cache/modelscope/indextts2.5 \
  --ref assets/voice/refs/sucaiB/ref-B.wav \
  --text "今天也辛苦了，要不要先休息一下？" \
  --out-dir assets/voice/demos/sucaiB/it2
