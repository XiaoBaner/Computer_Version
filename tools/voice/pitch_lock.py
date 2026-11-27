#!/usr/bin/env python3
"""Pin a clone's median pitch back onto its reference clip.

Measured with Praat, both engines drift away from the reference they were given:
IndexTTS moves by up to +41 Hz on the bright voices (it lifts pitch when it adds
emotion), and CosyVoice lands 14–37 Hz low on nearly every pack. At 290 Hz that
is more than a semitone — enough to stop being "that voice".

This applies one global ratio to the whole pitch tier (Praat PSOLA, 2.5 ms grid),
which leaves the spectral envelope — the formants that carry vocal identity —
alone, and leaves within-sentence movement untouched, so the emotion stays and
only the centre moves.

Two practical notes learned the hard way:
  * files written by the cloud SDK and by IndexTTS carry headers Praat misreads
    ("File too small"), after which it grinds for minutes; every clip is first
    rewritten through librosa/soundfile as clean PCM16.
  * librosa's pYIN took minutes of CPU on the same 3 s clips; Praat's pitch
    tracker answers in milliseconds.

Usage:
  python tools/voice/pitch_lock.py --ref packs/08/ref.wav \
      --in-dir in/08 --out-dir out/08
"""

from __future__ import annotations

import argparse
import math
import sys
import tempfile
from pathlib import Path


def _clean(path: Path, tmpdir: Path) -> Path:
    """Rewrite through soundfile so Praat sees a plain PCM16 file."""
    import librosa
    import soundfile as sf

    y, sr = librosa.load(str(path), sr=None, mono=True)
    out = tmpdir / (path.stem + ".clean.wav")
    sf.write(str(out), y, sr, subtype="PCM_16")
    return out


def _praat_f0(snd) -> float:
    import numpy as np
    from parselmouth.praat import call

    pitch = call(snd, "To Pitch", 0.0, 70.0, 600.0)
    v = pitch.selected_array["frequency"]
    v = v[v > 0]
    return float(np.median(v)) if v.size else 0.0


def median_f0(path: Path, tmpdir: Path | None = None) -> float:
    import parselmouth
    import tempfile as tf

    with tf.TemporaryDirectory() as d:
        clean = _clean(path, Path(d))
        return _praat_f0(parselmouth.Sound(str(clean)))


def lock(src: Path, dst: Path, ratio: float, dt: float = 0.0025) -> dict:
    import numpy as np
    import parselmouth
    import soundfile as sf
    from parselmouth.praat import call

    with tempfile.TemporaryDirectory() as d:
        clean = _clean(src, Path(d))
        snd = parselmouth.Sound(str(clean))
        manip = call(snd, "To Manipulation", dt, 70.0, 600.0)
        pt = call(manip, "Extract pitch tier")
        call(pt, "Formula", f"self * {ratio:.6f}")
        call([manip, pt], "Replace pitch tier")
        res = call(manip, "Get resynthesis (overlap-add)")
        y = res.values[0] if res.values.ndim > 1 else res.values
        y = np.asarray(y, dtype=np.float32)
        peak = float(np.abs(y).max()) or 1.0
        if peak > 0.89:
            y = y * (0.89 / peak)
        dst.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(dst), y, int(res.sampling_frequency))
    return {"ratio": round(ratio, 4), "dur": round(len(y) / res.sampling_frequency, 3)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ref", required=True, type=Path)
    ap.add_argument("--in-dir", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--dt", type=float, default=0.0025)
    args = ap.parse_args()

    ref_f0 = median_f0(args.ref)
    if ref_f0 <= 0:
        raise SystemExit(f"{args.ref}: 没有测到基频")
    print(f"参考音中位 F0 = {ref_f0:.0f} Hz", flush=True)

    files = sorted(p for p in args.in_dir.glob("*.wav") if not p.name.startswith("00-"))
    for src in files:
        out_f0 = median_f0(src)
        if out_f0 <= 0:
            print(f">> skip {src.name}（无基频）", flush=True)
            continue
        ratio = ref_f0 / out_f0
        lock(src, args.out_dir / src.name, ratio, args.dt)
        print(f">> {src.name:<9} {out_f0:5.0f} -> {ref_f0:5.0f} Hz "
              f"({12 * math.log2(ratio):+.2f} 半音)", flush=True)
    print(f"{len(files)} 条 -> {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
