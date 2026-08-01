#!/usr/bin/env python3
"""Generate narration WAVs for the Two Capsules maps.

Speaks each script through the ReadAloud Inflect worker over its Unix socket,
then writes narrate/manifest.json for player.html.

    python3 tools/gen_narration.py            # everything missing
    python3 tools/gen_narration.py --lesson 02 --force
    python3 tools/gen_narration.py --manifest-only
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "narrate" / "scripts"
AUDIO = ROOT / "narrate" / "audio"
MANIFEST = ROOT / "narrate" / "manifest.json"

SOCK = Path(
    os.environ.get("READALOUD_INFLECT_SOCK")
    or Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "readaloud-inflect.sock"
)


def speak(text: str, out: Path, *, speed: float = 1.0, variation: float = 0.4, seed: int = 11) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".partial.wav")
    if tmp.exists():
        tmp.unlink()

    req = {
        "text": text.strip(),
        "output": str(tmp.resolve()),
        "speed": speed,
        "variation": variation,
        "seed": seed,
    }

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(240)
    try:
        s.connect(str(SOCK))
        s.sendall((json.dumps(req) + "\n").encode("utf-8"))
        resp = s.recv(8192).decode("utf-8", errors="replace").strip()
    finally:
        s.close()

    if not resp.startswith("ok"):
        raise RuntimeError(f"Inflect failed for {out.name}: {resp}")
    if not tmp.exists() or tmp.stat().st_size < 44:
        raise RuntimeError(f"missing wav {tmp}")
    tmp.replace(out)


def scripts() -> list[Path]:
    return sorted(SCRIPTS.glob("*.json"))


def clips_for(script: dict) -> list[tuple[str, str]]:
    out = [(f"ch-{c['id']}", c["text"]) for c in script["chapters"]]
    for k, v in (script.get("nodes") or {}).items():
        if isinstance(v, str):
            out.append((f"node-{k}-short", v))
        else:
            out.append((f"node-{k}-short", v["short"]))
            if v.get("deep"):
                out.append((f"node-{k}-deep", v["deep"]))
    return out


def encode(wav: Path) -> Path:
    """Transcode to Opus and drop the WAV. ~20x smaller, still speech-clean."""
    ogg = wav.with_suffix(".opus")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
         "-c:a", "libopus", "-b:a", "28k", "-ac", "1", "-application", "voip", str(ogg)],
        check=True)
    wav.unlink()
    return ogg


def gen(path: Path, *, force: bool, speed: float) -> int:
    script = json.loads(path.read_text(encoding="utf-8"))
    lesson = script["id"]
    made = 0
    for clip_id, text in clips_for(script):
        dest = AUDIO / lesson / f"{clip_id}.wav"
        final = dest.with_suffix(".opus")
        # The spoken text is stored beside the audio. Editing a chapter shifts block
        # ids, so a clip that already exists may now belong to different words —
        # stale audio silently attached to new text is worse than no audio at all.
        stamp = dest.with_suffix(".txt")
        fresh = stamp.exists() and stamp.read_text(encoding="utf-8") == text
        if final.exists() and fresh and not force and final.stat().st_size > 0:
            continue
        if not re.search(r"[A-Za-z]{2}", text):
            continue
        print(f"  synth {lesson}/{clip_id}  ({len(text)} chars)", flush=True)
        t0 = time.time()
        try:
            speak(text, dest, speed=speed)
        except RuntimeError as e:
            print(f"    !! skipped: {e}", flush=True)
            if dest.with_suffix(".partial.wav").exists():
                dest.with_suffix(".partial.wav").unlink()
            continue
        final = encode(dest)
        stamp.write_text(text, encoding="utf-8")
        print(f"    -> {final.stat().st_size:,} bytes in {time.time() - t0:.1f}s", flush=True)
        made += 1
    return made


def build_manifest() -> dict:
    lessons = []
    for path in scripts():
        script = json.loads(path.read_text(encoding="utf-8"))
        lesson = script["id"]

        def audio_for(clip_id: str):
            rel = f"audio/{lesson}/{clip_id}.opus"
            return rel if (ROOT / "narrate" / rel).exists() else None

        lessons.append({
            "id": lesson,
            "title": script["title"],
            "blurb": script.get("blurb", ""),
            "map": script["map"],
            "chapters": [{
                "id": c["id"],
                "title": c.get("title") or c["id"],
                "focus": c.get("focus"),
                "text": c["text"],
                "audio": audio_for(f"ch-{c['id']}"),
            } for c in script["chapters"]],
            "nodes": {
                k: {
                    "short": v if isinstance(v, str) else v["short"],
                    "deep": None if isinstance(v, str) else v.get("deep"),
                    # bare type names; explore.html resolves them against reference/search.json
                    "types": [] if isinstance(v, str) else (v.get("types") or []),
                    # a node may continue into another map
                    "goto": None if isinstance(v, str) else v.get("goto"),
                    "gotoLabel": None if isinstance(v, str) else v.get("gotoLabel"),
                    "audio": audio_for(f"node-{k}-short"),
                    "audioDeep": audio_for(f"node-{k}-deep"),
                }
                for k, v in (script.get("nodes") or {}).items()
            },
        })
    return {"version": 1, "voice": "ReadAloud Inflect", "lessons": lessons}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lesson", default="all", help="01..04 or all")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--manifest-only", action="store_true")
    args = ap.parse_args()

    if not args.manifest_only:
        if not SOCK.exists():
            print(f"Inflect socket missing: {SOCK}", file=sys.stderr)
            print("Start the ReadAloud Inflect worker, or pass --manifest-only.", file=sys.stderr)
            return 2
        total = 0
        for path in scripts():
            if args.lesson != "all" and path.stem != args.lesson:
                continue
            print(f"== {path.stem} ==")
            total += gen(path, force=args.force, speed=args.speed)
        print(f"{total} clip(s) written")

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    man = build_manifest()
    MANIFEST.write_text(json.dumps(man, indent=2) + "\n", encoding="utf-8")
    have = sum(1 for l in man["lessons"] for c in l["chapters"] if c["audio"])
    print(f"wrote {MANIFEST.relative_to(ROOT)} — {len(man['lessons'])} lessons, {have} narrated chapters")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
