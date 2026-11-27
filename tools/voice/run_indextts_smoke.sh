#!/bin/bash
# First IndexTTS-2.5 run: downloads the auxiliary models (~2.5 GB) into the
# model dir on first start, then synthesises one short line.
set -e
cd /Users/haowang/Desktop/Computer_Version/.claude/worktrees/cv-division-discussion-41814d

~/.cache/indextts-venv/bin/python -u tools/voice/indextts_run.py \
  --src /Users/haowang/.cache/indextts-src/src \
  --model-dir /Users/haowang/.cache/modelscope/indextts2.5 \
  --ref assets/voice/refs/sucaiB/ref-B.wav \
  --text "今天也辛苦了，要不要先休息一下？" \
  --lang ZH \
  --out assets/voice/demos/sucaiB/it2/smoke.wav
