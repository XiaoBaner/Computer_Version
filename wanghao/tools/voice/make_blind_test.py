#!/usr/bin/env python3
"""Build a static blind-listening page from candidate folders.

Expected input layout:
    samples/
      Maia/01.wav ... 08.wav
      Chelsie/01.wav ... 08.wav
      Serena/01.wav ... 08.wav

Usage:
    python3 tools/voice/make_blind_test.py --input assets/voice/samples --out blind-test

The generated index.html never contains candidate names; mapping.json does.
Keep mapping.json away from listeners until scoring is finished.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
from datetime import datetime, timezone
from html import escape
from pathlib import Path

AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".ogg", ".flac"}


def label_for(index: int) -> str:
    """0 -> A, 1 -> B, ... 25 -> Z, 26 -> AA."""
    chars: list[str] = []
    value = index + 1
    while value:
        value, remainder = divmod(value - 1, 26)
        chars.append(chr(ord("A") + remainder))
    return "".join(reversed(chars))


def discover(input_dir: Path) -> tuple[list[str], dict[str, list[Path]]]:
    candidates = sorted(path for path in input_dir.iterdir() if path.is_dir())
    if len(candidates) < 2:
        raise SystemExit(f"{input_dir} needs at least two candidate folders")

    files: dict[str, list[Path]] = {}
    for candidate in candidates:
        audio = sorted(
            path for path in candidate.iterdir() if path.suffix.lower() in AUDIO_EXTS
        )
        if not audio:
            raise SystemExit(f"{candidate} has no audio files")
        files[candidate.name] = audio

    counts = {name: len(items) for name, items in files.items()}
    if len(set(counts.values())) != 1:
        detail = ", ".join(f"{name}={count}" for name, count in counts.items())
        raise SystemExit(f"candidate folders must contain the same number of files: {detail}")

    return [candidate.name for candidate in candidates], files


def build_html(title: str, sentence_count: int, labels: list[str], rows: list[str]) -> str:
    template = """<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { color-scheme: light dark; }
  body { margin: 0 auto; max-width: 1100px; padding: 28px 20px 60px;
         font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  h1 { font-size: 24px; margin: 0 0 6px; }
  .lede { color: #666; margin: 0 0 20px; }
  .sentence { border-top: 1px solid #d8d8d8; padding: 18px 0 8px; }
  .sentence h2 { font-size: 17px; margin: 0 0 12px; }
  .candidates { display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); }
  .candidate { border: 1px solid #d8d8d8; border-radius: 10px; padding: 14px; }
  .candidate h3 { margin: 0 0 8px; font-size: 15px; }
  audio { width: 100%; margin-bottom: 10px; }
  label.field { display: block; margin: 8px 0; }
  label.field output { font-weight: 600; margin-left: 6px; }
  input[type=range] { width: 100%; }
  textarea { width: 100%; min-height: 44px; box-sizing: border-box; }
  .row { display: flex; gap: 14px; align-items: center; flex-wrap: wrap; }
  .actions { position: sticky; bottom: 0; background: Canvas; padding: 14px 0;
             border-top: 1px solid #d8d8d8; margin-top: 24px; }
  button { font: inherit; padding: 10px 16px; border-radius: 8px; cursor: pointer; }
  .hint { color: #666; font-size: 13px; }
  @media (prefers-color-scheme: dark) {
    .lede, .hint { color: #aaa; }
    .sentence, .actions { border-color: #444; }
    .candidate { border-color: #444; }
  }
</style>
<body>
<h1>__TITLE__</h1>
<p class="lede">同一副耳机或音箱，固定音量。每句先听 A/B/C，再独立打分；不要和旁边的人讨论。自然度和情绪合适度都是 1–5，5 最好。</p>
__ROWS__
<div class="actions">
  <button id="download" type="button">下载结果 JSON</button>
  <span class="hint">完成后把 JSON 交给组织者；评分结束前不要打开 mapping.json。</span>
</div>
<script>
function collect() {
  const sentences = [];
  document.querySelectorAll("section.sentence").forEach((section) => {
    const candidates = [];
    section.querySelectorAll(".candidate").forEach((card) => {
      candidates.push({
        label: card.dataset.label,
        file: card.dataset.file,
        naturalness: Number(card.querySelector(".naturalness").value),
        emotion: Number(card.querySelector(".emotion").value),
        misread: card.querySelector(".misread").checked,
        continue_chat: card.querySelector(".continue").value,
        comment: card.querySelector(".comment").value.trim()
      });
    });
    sentences.push({ index: Number(section.dataset.sentence), candidates });
  });
  return {
    schema_version: 1,
    created_at: new Date().toISOString(),
    sentence_count: __SENTENCE_COUNT__,
    candidate_count: __LABEL_COUNT__,
    sentences
  };
}
document.getElementById("download").addEventListener("click", () => {
  const blob = new Blob([JSON.stringify(collect(), null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "blind-results-" + new Date().toISOString().replace(/[:.]/g, "-") + ".json";
  link.click();
  URL.revokeObjectURL(url);
});
</script>
</body>
</html>
"""
    return (
        template.replace("__TITLE__", escape(title))
        .replace("__SENTENCE_COUNT__", str(sentence_count))
        .replace("__LABEL_COUNT__", str(len(labels)))
        .replace("__ROWS__", "\n".join(rows))
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a static blind-listening page.")
    parser.add_argument("--input", required=True, type=Path, help="folder containing one subfolder per candidate")
    parser.add_argument("--out", required=True, type=Path, help="output folder (must not contain index.html)")
    parser.add_argument("--title", default="语音盲听", help="page title")
    parser.add_argument("--seed", type=int, default=None, help="random seed for reproducible order")
    args = parser.parse_args()

    if not args.input.is_dir():
        raise SystemExit(f"input folder not found: {args.input}")
    args.out.mkdir(parents=True, exist_ok=True)
    if (args.out / "index.html").exists():
        raise SystemExit(f"{args.out}/index.html already exists; choose a new output folder")

    names, files = discover(args.input)
    sentence_count = len(next(iter(files.values())))
    labels = [label_for(index) for index in range(len(names))]
    rng = random.Random(args.seed)

    audio_dir = args.out / "audio"
    audio_dir.mkdir(exist_ok=True)
    mapping: dict = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": str(args.input),
        "candidates": names,
        "sentences": {},
    }
    rows: list[str] = []

    for sentence_index in range(sentence_count):
        order = list(range(len(names)))
        rng.shuffle(order)
        cards: list[str] = []
        sentence_map: dict[str, dict[str, str]] = {}

        for label, candidate_index in zip(labels, order):
            candidate = names[candidate_index]
            source = files[candidate][sentence_index]
            anonymous = f"{label}_{sentence_index + 1:02d}{source.suffix.lower()}"
            shutil.copy2(source, audio_dir / anonymous)
            sentence_map[label] = {"candidate": candidate, "file": f"audio/{anonymous}"}

            cards.append(
                f"""<article class="candidate" data-label="{label}" data-file="audio/{anonymous}">
  <h3>候选 {label}</h3>
  <audio controls preload="metadata" src="audio/{anonymous}"></audio>
  <label class="field">自然度 <output>4</output>
    <input class="naturalness" type="range" min="1" max="5" step="1" value="4"
           oninput="this.previousElementSibling.value=this.value">
  </label>
  <label class="field">情绪合适度 <output>4</output>
    <input class="emotion" type="range" min="1" max="5" step="1" value="4"
           oninput="this.previousElementSibling.value=this.value">
  </label>
  <div class="row">
    <label><input class="misread" type="checkbox"> 有影响句义的错读</label>
    <label>愿意继续聊
      <select class="continue">
        <option value="yes">是</option>
        <option value="unsure" selected>不确定</option>
        <option value="no">否</option>
      </select>
    </label>
  </div>
  <label class="field">备注
    <textarea class="comment" placeholder="哪里不自然？尾音、停顿、情绪、错读……"></textarea>
  </label>
</article>"""
            )

        rows.append(
            f"""<section class="sentence" data-sentence="{sentence_index + 1}">
  <h2>第 {sentence_index + 1} 句</h2>
  <div class="candidates">
    {''.join(cards)}
  </div>
</section>"""
        )
        mapping["sentences"][str(sentence_index + 1)] = sentence_map

    (args.out / "mapping.json").write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    html = build_html(args.title, sentence_count, labels, rows)
    (args.out / "index.html").write_text(html, encoding="utf-8")

    print(f"wrote {args.out / 'index.html'}")
    print(f"wrote {args.out / 'mapping.json'} ({len(names)} candidates x {sentence_count} sentences)")
    print("open index.html in a browser; keep mapping.json private until scoring is done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
