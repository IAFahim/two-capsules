#!/usr/bin/env python3
"""Re-trim every existing .opus clip — kill residual lead/trail room-tone.

Does NOT re-synth. Decode → aggressive silenceremove → Opus. Safe to run while
gen_narration.py is synthesizing new files (skips anything mtime < 2s old).

    python3 tools/retighten_audio.py              # all
    python3 tools/retighten_audio.py --lesson 00-world
    python3 tools/retighten_audio.py --dry-run
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIO = ROOT / "narrate" / "audio"

TRIM_AF = (
    "silenceremove=start_periods=1:start_silence=0.015:start_threshold=-42dB:detection=peak,"
    "areverse,"
    "silenceremove=start_periods=1:start_silence=0.03:start_threshold=-42dB:detection=peak,"
    "areverse"
)


def retighten(opus: Path, *, dry: bool) -> tuple[bool, str]:
    if time.time() - opus.stat().st_mtime < 2:
        return False, "busy"
    # stay on the same filesystem as the opus (Path.replace is a rename — fails /tmp → home)
    wav = opus.with_suffix(".retight.in.wav")
    tight = opus.with_suffix(".retight.out.wav")
    out = opus.with_suffix(".retight.opus")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(opus), str(wav)],
            check=True,
        )
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
             "-af", TRIM_AF, str(tight)],
            check=True,
        )
        probe = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(tight)],
            text=True,
        ).strip()
        dur = float(probe or 0)
        if dur < 0.12:
            return False, f"too short after trim ({dur:.2f}s)"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(tight),
             "-c:a", "libopus", "-b:a", "28k", "-ac", "1",
             "-application", "voip", str(out)],
            check=True,
        )
        before = opus.stat().st_size
        if dry:
            return True, f"dry {before}→{out.stat().st_size} B dur={dur:.2f}s"
        out.replace(opus)
        return True, f"{before}→{opus.stat().st_size} B dur={dur:.2f}s"
    except Exception as e:
        return False, str(e)[:160]
    finally:
        for p in (wav, tight, out):
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lesson", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    files = sorted(AUDIO.glob("*/*.opus") if not args.lesson else (AUDIO / args.lesson).glob("*.opus"))
    if args.limit:
        files = files[: args.limit]
    ok = fail = skip = 0
    for i, f in enumerate(files, 1):
        good, msg = retighten(f, dry=args.dry_run)
        if msg == "busy":
            skip += 1
            continue
        if good:
            ok += 1
            if i % 25 == 0 or args.dry_run:
                print(f"[{i}/{len(files)}] {f.parent.name}/{f.name}  {msg}", flush=True)
        else:
            fail += 1
            print(f"[{i}/{len(files)}] FAIL {f.parent.name}/{f.name}  {msg}", flush=True)
    print(f"done: {ok} retightened, {fail} failed, {skip} skipped(busy)")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
