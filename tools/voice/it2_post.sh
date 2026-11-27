#!/bin/bash
# Take whatever IndexTTS-2.5 produced in it2/ and put it through the same
# pipeline the Qwen takes got: measure, tighten the comma pause to 0.34 s,
# restore the 1.2-6 kHz presence the generator loses, then export for listening.
set -e
cd /Users/haowang/Desktop/Computer_Version/.claude/worktrees/cv-division-discussion-41814d
PY=~/.cache/qwen3tts-venv3/bin/python
SRC=assets/voice/demos/sucaiB/it2
OUT=/Users/haowang/Desktop/试听-素材B-IndexTTS2

rm -rf "$OUT"; mkdir -p "$OUT"
for f in "$SRC"/*.wav; do
  base=$(basename "$f" .wav)
  cp "$f" "$OUT/${base}-原样.wav"
  $PY tools/voice/repair_flow.py "$f" --out "/tmp/it2_${base}.wav" --gap 0.34
  $PY tools/voice/polish.py "/tmp/it2_${base}.wav" --out "$OUT/${base}-接紧提亮.wav" --presence 4
done
$PY tools/voice/naturalness.py "$OUT"/*.wav
