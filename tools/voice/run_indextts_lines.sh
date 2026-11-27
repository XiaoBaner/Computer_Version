#!/bin/bash
# 用定下来的情绪（喜 0.45 + 静 0.35）把演示里真正要说的每一句都生成一遍。
set -e
cd /Users/haowang/Desktop/Computer_Version/.claude/worktrees/cv-division-discussion-41814d

~/.cache/indextts-venv/bin/python -u tools/voice/indextts_lines.py \
  --src /Users/haowang/.cache/indextts-src/src \
  --model-dir /Users/haowang/.cache/modelscope/indextts2.5 \
  --ref assets/voice/refs/sucaiB/ref-B.wav \
  --lines assets/voice/demos/sucaiB/lines.txt \
  --out-dir assets/voice/demos/sucaiB/lines
