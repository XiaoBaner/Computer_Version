#!/usr/bin/env python3
"""List and extract individual files from a remote ZIP using HTTP range requests.

Public speech corpora are shipped as one huge zip (AISHELL-3 is 19 GB, CSMSC ~10 GB).
On a slow link you never want the whole thing, and often you need one speaker.
This reads the central directory over HTTP, then pulls only the bytes for the
entries you ask for.

Usage:
    python3 tools/voice/remote_zip.py list  --url URL [--match REGEX] [--limit N]
    python3 tools/voice/remote_zip.py fetch --url URL --match REGEX --out DIR [--limit N]

ZIP64 is supported (19 GB archives need it).
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import zlib
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

EOCD_SIG = b"PK\x05\x06"
ZIP64_EOCD_SIG = b"PK\x06\x06"
ZIP64_LOC_SIG = b"PK\x06\x07"
CDH_SIG = b"PK\x01\x02"
LFH_SIG = b"PK\x03\x04"


def http_range(url: str, start: int, end: int, retries: int = 3) -> bytes:
    """Inclusive byte range."""
    last: Exception | None = None
    for _ in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={"Range": f"bytes={start}-{end}", "User-Agent": "remote-zip/1.0"},
            )
            with urllib.request.urlopen(req, timeout=90) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001 - network flake, retry
            last = exc
    raise RuntimeError(f"range {start}-{end} failed: {last}")


def content_length(url: str) -> int:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "remote-zip/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return int(resp.headers["Content-Length"])


@dataclass
class Entry:
    name: str
    method: int
    csize: int
    usize: int
    offset: int


def read_central_directory(url: str) -> list[Entry]:
    size = content_length(url)
    tail = http_range(url, max(0, size - 65557), size - 1)

    pos = tail.rfind(EOCD_SIG)
    if pos < 0:
        raise RuntimeError("EOCD not found — not a zip, or the server ignored Range")

    (_, _, _, _, total, cd_size, cd_off, _) = struct.unpack("<IHHHHIIH", tail[pos:pos + 22])

    # ZIP64: any sentinel means the real values live in the ZIP64 records.
    if total == 0xFFFF or cd_size == 0xFFFFFFFF or cd_off == 0xFFFFFFFF:
        loc_pos = tail.rfind(ZIP64_LOC_SIG)
        if loc_pos < 0:
            raise RuntimeError("ZIP64 locator not found")
        z64_off = struct.unpack("<Q", tail[loc_pos + 8:loc_pos + 16])[0]
        rec = http_range(url, z64_off, z64_off + 56)
        if rec[:4] != ZIP64_EOCD_SIG:
            raise RuntimeError("ZIP64 EOCD signature mismatch")
        total, cd_size, cd_off = struct.unpack("<QQQ", rec[32:56])

    cd = http_range(url, cd_off, cd_off + cd_size - 1)
    entries: list[Entry] = []
    p = 0
    while p + 46 <= len(cd) and cd[p:p + 4] == CDH_SIG:
        method, = struct.unpack("<H", cd[p + 10:p + 12])
        csize, usize = struct.unpack("<II", cd[p + 20:p + 28])
        nlen, elen, clen = struct.unpack("<HHH", cd[p + 28:p + 34])
        offset, = struct.unpack("<I", cd[p + 42:p + 46])
        name = cd[p + 46:p + 46 + nlen].decode("utf-8", "replace")

        extra = cd[p + 46 + nlen:p + 46 + nlen + elen]
        q = 0
        while q + 4 <= len(extra):
            tag, ln = struct.unpack("<HH", extra[q:q + 4])
            body = extra[q + 4:q + 4 + ln]
            if tag == 0x0001:  # ZIP64 extended information
                r = 0
                if usize == 0xFFFFFFFF:
                    usize = struct.unpack("<Q", body[r:r + 8])[0]; r += 8
                if csize == 0xFFFFFFFF:
                    csize = struct.unpack("<Q", body[r:r + 8])[0]; r += 8
                if offset == 0xFFFFFFFF:
                    offset = struct.unpack("<Q", body[r:r + 8])[0]; r += 8
            q += 4 + ln

        entries.append(Entry(name, method, csize, usize, offset))
        p += 46 + nlen + elen + clen
    return entries


def extract(url: str, entry: Entry, out_dir: Path) -> Path:
    if entry.method not in (0, 8):
        raise RuntimeError(f"{entry.name}: compression method {entry.method} unsupported")
    head = http_range(url, entry.offset, entry.offset + 29)
    if head[:4] != LFH_SIG:
        raise RuntimeError(f"{entry.name}: bad local header")
    nlen, elen = struct.unpack("<HH", head[26:30])
    data_start = entry.offset + 30 + nlen + elen
    data = http_range(url, data_start, data_start + entry.csize - 1)
    if entry.method == 8:  # deflate, raw (no zlib header)
        data = zlib.decompressobj(-15).decompress(data)
    dest = out_dir / Path(entry.name).name
    dest.write_bytes(data)
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=["list", "fetch"])
    ap.add_argument("--url", required=True)
    ap.add_argument("--match", default="", help="regex on entry name")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    entries = read_central_directory(args.url)
    print(f"{len(entries)} entries", file=sys.stderr)
    pat = re.compile(args.match) if args.match else None
    chosen = [e for e in entries if not e.name.endswith("/") and (pat.search(e.name) if pat else True)]
    if args.limit:
        chosen = chosen[: args.limit]

    if args.action == "list":
        for e in chosen[:400]:
            print(f"{e.usize:10d}  {e.name}")
        print(f"— {len(chosen)} match", file=sys.stderr)
        return 0

    if not args.out:
        print("--out required for fetch", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    ok = 0
    for e in chosen:
        try:
            extract(args.url, e, args.out)
            ok += 1
            if ok % 20 == 0:
                print(f"  {ok}/{len(chosen)}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"  ! {e.name}: {exc}", file=sys.stderr)
    print(json.dumps({"fetched": ok, "requested": len(chosen)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
