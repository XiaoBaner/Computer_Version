#!/bin/bash
set -e
cd /Users/haowang/Desktop/Computer_Version/.claude/worktrees/cv-division-discussion-41814d
~/.cache/indextts-venv/bin/python -u tools/voice/packs_batch.py \
  --src /Users/haowang/.cache/indextts-src/src \
  --model-dir /Users/haowang/.cache/modelscope/indextts2.5 \
  --packs-root assets/voice/packs \
  --plan assets/voice/demos/sucaiB/lines-v2.json \
  --out-root assets/voice/demos/packs
