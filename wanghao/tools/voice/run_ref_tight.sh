#!/bin/bash
set -e
cd /Users/haowang/Desktop/Computer_Version/.claude/worktrees/cv-division-discussion-41814d
~/.cache/indextts-venv/bin/python -u tools/voice/indextts_plan.py \
  --src /Users/haowang/.cache/indextts-src/src \
  --model-dir /Users/haowang/.cache/modelscope/indextts2.5 \
  --ref assets/voice/refs/sucaiB/ref-B-tight.wav \
  --plan assets/voice/demos/sucaiB/lines-ref-tight.json \
  --out-dir assets/voice/demos/sucaiB/lines-ref-tight
