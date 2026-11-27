#!/bin/bash
# 每一句用各自的情绪向量与语速重新生成（对照表见 lines-v2.json 里的 why 字段）
set -e
cd /Users/haowang/Desktop/Computer_Version/.claude/worktrees/cv-division-discussion-41814d

~/.cache/indextts-venv/bin/python -u tools/voice/indextts_plan.py \
  --src /Users/haowang/.cache/indextts-src/src \
  --model-dir /Users/haowang/.cache/modelscope/indextts2.5 \
  --ref assets/voice/refs/sucaiB/ref-B.wav \
  --plan assets/voice/demos/sucaiB/lines-v2.json \
  --out-dir assets/voice/demos/sucaiB/lines-v2
