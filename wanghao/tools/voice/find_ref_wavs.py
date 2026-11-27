#!/usr/bin/env python3
"""List .wav reference clips shipped in open-source TTS repos, with their license.

Cloning locks the timbre to whatever reference you feed it, so the reference is
the whole voice decision. This scans the official example assets of the major
open TTS projects (via the GitHub trees API) so we can pick a candidate whose
license and timbre we can actually verify, instead of inventing one from text.

Example:
    python3 tools/voice/find_ref_wavs.py QwenAudio/CosyVoice bilibili/IndexTTS
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request

API = "https://api.github.com/repos/{repo}"


def gh_token() -> str:
    """Reuse the gh CLI login so we get its 5000 req/h instead of 60."""
    try:
        return subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=10, check=True
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


TOKEN = gh_token()


def get(url: str) -> dict | list | None:
    headers = {"User-Agent": "ref-wav-scan/1.0"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        print(f"  ! {url}: {exc}", file=sys.stderr)
        return None


def license_of(repo: str) -> str:
    data = get(API.format(repo=repo) + "/license")
    if isinstance(data, dict) and data.get("license"):
        return data["license"].get("spdx_id") or "unknown"
    return "unknown"


def wavs_of(repo: str) -> list[tuple[str, int]]:
    data = get(API.format(repo=repo) + "/git/trees/main?recursive=1")
    if not isinstance(data, dict) or "tree" not in data:
        data = get(API.format(repo=repo) + "/git/trees/master?recursive=1")
    if not isinstance(data, dict) or "tree" not in data:
        return []
    skip = ("test", "ci/", ".github/", "node_modules/")
    return [
        (node["path"], node.get("size", 0))
        for node in data["tree"]
        if node.get("type") == "blob"
        and node["path"].lower().endswith(".wav")
        # 3 s of 24 kHz float32 is ~340 KB; anything under 3 MB is plausibly a
        # few-second prompt clip and also plausibly a full song, so keep both
        # and let the duration check downstream decide.
        and 20_000 <= node.get("size", 0) <= 3_000_000
        and not any(part in node["path"].lower() for part in skip)
    ]


def main() -> int:
    repos = sys.argv[1:]
    if not repos:
        print(__doc__)
        return 2
    for repo in repos:
        print(f"=== {repo}  license={license_of(repo)}")
        found = wavs_of(repo)
        if not found:
            print("  (no .wav found)")
            continue
        for path, size in sorted(found, key=lambda p: p[1]):
            print(f"  {size / 1024:7.0f} KB  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
