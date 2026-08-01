#!/usr/bin/env python3
"""God-tier narration pipeline for Two Capsules.

Why the old path felt gappy and rough
-------------------------------------
* One Inflect call per whole block. Long paragraphs (1k+ chars) stall or kill
  the worker; short ones finish. The run looks random.
* No priority. Book 42 synths before finishing a half-done earlier chapter.
* No post-trim. Each clip starts/ends with model silence → audible seams in
  the reader and the map tour.
* Worker death aborted the whole process; poison clips blocked the queue forever.
* Playback waited a fixed 450–900 ms between clips even when audio already had
  padding, and never preloaded the next file.

What this does instead
----------------------
1. PLAN  — rank every missing/stale clip (finish partials, maps before books,
           earlier chapters, short-before-long). Write narrate/plan.json + PLAN.md.
2. PREP  — speakable() pass, sentence-segment anything over ~320 chars.
3. SYNTH — segment-by-segment over the Inflect socket; restart worker on fail;
           quarantine poison clips so they never block the queue.
4. FINISH — silence-trim, Opus encode, stamp text, rebuild manifest.
5. PLAY  — explore/read preload + short join (handled in the HTML side).

    python3 tools/gen_narration.py --plan              # write the plan, exit
    python3 tools/gen_narration.py                     # execute the plan
    python3 tools/gen_narration.py --lesson book-07-core
    python3 tools/gen_narration.py --force --limit 20
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
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "narrate" / "scripts"
AUDIO = ROOT / "narrate" / "audio"
MANIFEST = ROOT / "narrate" / "manifest.json"
PLAN_JSON = ROOT / "narrate" / "plan.json"
PLAN_MD = ROOT / "narrate" / "PLAN.md"
POISON = ROOT / "narrate" / ".poison.json"

SOCK = Path(
    os.environ.get("READALOUD_INFLECT_SOCK")
    or Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "readaloud-inflect.sock"
)
SHARE = Path.home() / ".local" / "share" / "readaloud"
WORKER_PY = SHARE / "inflect_worker.py"
WORKER_PYTHON = SHARE / "inflect-venv" / "bin" / "python"

# Long monologues die or drag; segment at sentence boundaries under this size.
SEGMENT_CHARS = 320
# Stable voice identity across the whole atlas.
VOICE_SPEED = 1.0
VOICE_VARIATION = 0.35
VOICE_SEED = 11


# --------------------------------------------------------------------------- speakable
# Keep in lockstep with tools/build_reader.py speakable() so re-synth of old
# scripts still matches what the reader expects on screen.

SUBS = [
    (r"`([^`]+)`", r"\1"),
    (r"\[([^\]]+)\]\([^)]*\)", r"\1"),
    (r"\*\*([^*]+)\*\*", r"\1"),
    (r"\*([^*]+)\*", r"\1"),
    (r"→|->", " then "),
    (r"←|<-", " back to "),
    (r"\s*—\s*|\s*–\s*", ", "),
    (r"(\d+)\s*ms\b", r"\1 milliseconds"),
    (r"(\d+)\s*Hz\b", r"\1 hertz"),
    (r"(\d+)\s*KiB\b", r"\1 kibibytes"),
    (r"(\d+)\s*MB\b", r"\1 megabytes"),
    (r"(\d+)\s*kbps\b", r"\1 kilobits per second"),
    (r"\bfloat2\b", "float two"),
    (r"\bfloat3\b", "float three"),
    (r"\basmdef\b", "assembly definition"),
    (r"\bECS\b", "E C S"),
    (r"\bECB\b", "entity command buffer"),
    (r"\bRTT\b", "round trip time"),
    (r"\bMTU\b", "M T U"),
    (r"\bRPC(s)?\b", r"remote procedure call\1"),
    (r"\bUXML\b", "U X M L"),
    (r"\bJWT\b", "J W T"),
    (r"\bGPU\b", "G P U"),
    (r"\bUDP\b", "U D P"),
    (r"\bIPC\b", "I P C"),
    (r"\bURP\b", "U R P"),
    (r"\bMPPM\b", "multiplayer play mode"),
    (r"\bSIMD\b", "simmed"),
    (r"\bDMA\b", "D M A"),
    (r"\bRUNPATH\b", "run path"),
    (r"\bLD_LIBRARY_PATH\b", "L D library path"),
    (r"([a-z])([A-Z])", r"\1 \2"),
    (r"\s{2,}", " "),
    (r"\s+([,.;:])", r"\1"),
    (r"([,.;:]){2,}", r"\1"),
]


def speakable(text: str) -> str:
    s = re.sub(r"`{3}\w*.*?(?:`{3}|$)", " ", text, flags=re.S)
    for pat, rep in SUBS:
        s = re.sub(pat, rep, s)
    for ch, rep in (
        ("⚡", ""), ("💀", ""), ("🔬", ""), ("✅", "yes"), ("❌", "no"),
        ("→", " then "), ("←", " back to "), ("×", " times "), ("·", " "),
    ):
        s = s.replace(ch, rep)
    s = re.sub(r"[^\S\n]+", " ", s)
    return s.strip()


def sentences(text: str) -> list[str]:
    """Split on sentence enders, keep abbreviations from shattering too hard."""
    text = speakable(text)
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", text)
    out: list[str] = []
    buf = ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if not buf:
            buf = p
        elif len(buf) + 1 + len(p) <= SEGMENT_CHARS:
            buf = buf + " " + p
        else:
            out.append(buf)
            buf = p
    if buf:
        out.append(buf)
    # Hard-cap any remaining monsters (code-ish blobs that never punctuate).
    final: list[str] = []
    for p in out:
        while len(p) > SEGMENT_CHARS:
            cut = p.rfind(" ", 0, SEGMENT_CHARS)
            if cut < SEGMENT_CHARS // 3:
                cut = SEGMENT_CHARS
            final.append(p[:cut].strip())
            p = p[cut:].strip()
        if p:
            final.append(p)
    return final


# --------------------------------------------------------------------------- inventory

@dataclass
class Clip:
    lesson: str
    clip_id: str          # ch-intro / node-foo-short
    text: str
    kind: str             # map-ch | map-node | book-ch
    lesson_ord: int       # order within atlas for priority
    chars: int
    status: str           # ok | missing | stale | poison
    priority: float = 0.0
    reason: str = ""

    @property
    def key(self) -> str:
        return f"{self.lesson}/{self.clip_id}"

    @property
    def opus(self) -> Path:
        return AUDIO / self.lesson / f"{self.clip_id}.opus"

    @property
    def stamp(self) -> Path:
        return AUDIO / self.lesson / f"{self.clip_id}.txt"


def load_poison() -> dict:
    try:
        return json.loads(POISON.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_poison(p: dict) -> None:
    POISON.parent.mkdir(parents=True, exist_ok=True)
    POISON.write_text(json.dumps(p, indent=2) + "\n", encoding="utf-8")


def lesson_order(lesson_id: str) -> int:
    """Lower = earlier in the atlas / book."""
    if lesson_id == "00-world":
        return 0
    if lesson_id.startswith("book-"):
        m = re.match(r"book-(\d+)", lesson_id)
        return 1000 + (int(m.group(1)) if m else 999)
    m = re.match(r"^(\d+)", lesson_id)
    return 10 + (int(m.group(1)) if m else 500)


def inventory() -> list[Clip]:
    poison = load_poison()
    clips: list[Clip] = []
    for path in sorted(SCRIPTS.glob("*.json")):
        script = json.loads(path.read_text(encoding="utf-8"))
        lid = script["id"]
        lord = lesson_order(lid)
        is_book = lid.startswith("book-")

        for c in script.get("chapters") or []:
            text = c["text"]
            cid = f"ch-{c['id']}"
            key = f"{lid}/{cid}"
            status = classify(lid, cid, text, poison)
            clips.append(Clip(
                lesson=lid, clip_id=cid, text=text,
                kind="book-ch" if is_book else "map-ch",
                lesson_ord=lord, chars=len(text), status=status,
            ))

        for k, v in (script.get("nodes") or {}).items():
            items = []
            if isinstance(v, str):
                items = [("short", v)]
            else:
                items = [("short", v["short"])]
                if v.get("deep"):
                    items.append(("deep", v["deep"]))
            for suffix, text in items:
                cid = f"node-{k}-{suffix}"
                status = classify(lid, cid, text, poison)
                clips.append(Clip(
                    lesson=lid, clip_id=cid, text=text,
                    kind="map-node", lesson_ord=lord, chars=len(text), status=status,
                ))
    return clips


def classify(lesson: str, clip_id: str, text: str, poison: dict) -> str:
    key = f"{lesson}/{clip_id}"
    if key in poison:
        return "poison"
    opus = AUDIO / lesson / f"{clip_id}.opus"
    stamp = AUDIO / lesson / f"{clip_id}.txt"
    if not opus.exists() or opus.stat().st_size == 0:
        return "missing"
    if not stamp.exists() or stamp.read_text(encoding="utf-8") != text:
        return "stale"
    return "ok"


def score(clip: Clip, lesson_progress: dict[str, tuple[int, int]]) -> tuple[float, str]:
    """Higher = do sooner. God-tier ordering rules, in priority order."""
    if clip.status == "ok":
        return (-1e9, "done")
    if clip.status == "poison":
        return (-1e8, "poison")

    reasons = []
    s = 0.0

    # 1. Maps before books — explore is the front door.
    if clip.kind.startswith("map"):
        s += 10_000
        reasons.append("map")
    else:
        s += 1_000
        reasons.append("book")

    # 2. Finish partial lessons before starting empty ones (no half-narrated chapters).
    #    "Almost done" (has some ok) beats "all stale" which still needs a full pass.
    done, total = lesson_progress.get(clip.lesson, (0, 1))
    if 0 < done < total:
        s += 5_000
        reasons.append("finish-partial")
    elif done == 0 and total > 0:
        # all-stale lessons: still prefer earlier ones, but not above real partials
        reasons.append("fresh-or-stale-lesson")

    # 3. Earlier atlas / book chapters first (reader walks 00 → 44).
    s += max(0, 2_000 - clip.lesson_ord)

    # 4. Short before long — cheap wins, fail-fast on poison, better ETA feel.
    s += max(0, 800 - clip.chars) / 2.0
    if clip.chars > 600:
        reasons.append("long")
    elif clip.chars < 160:
        reasons.append("short")

    # 5. Stale beats missing slightly (text edited — audio lies).
    if clip.status == "stale":
        s += 50
        reasons.append("stale")

    return (s, "+".join(reasons) or "work")


def plan(clips: list[Clip]) -> list[Clip]:
    # progress per lesson among non-poison
    prog: dict[str, tuple[int, int]] = {}
    by_lesson: dict[str, list[Clip]] = {}
    for c in clips:
        by_lesson.setdefault(c.lesson, []).append(c)
    for lid, group in by_lesson.items():
        total = sum(1 for c in group if c.status != "poison")
        done = sum(1 for c in group if c.status == "ok")
        prog[lid] = (done, total)

    work = [c for c in clips if c.status in ("missing", "stale")]
    for c in work:
        c.priority, c.reason = score(c, prog)
    work.sort(key=lambda c: (-c.priority, c.lesson_ord, c.chars, c.clip_id))
    return work


def write_plan(all_clips: list[Clip], work: list[Clip]) -> None:
    maps = [c for c in all_clips if c.kind.startswith("map")]
    books = [c for c in all_clips if c.kind.startswith("book")]
    summary = {
        "total": len(all_clips),
        "ok": sum(1 for c in all_clips if c.status == "ok"),
        "missing": sum(1 for c in all_clips if c.status == "missing"),
        "stale": sum(1 for c in all_clips if c.status == "stale"),
        "poison": sum(1 for c in all_clips if c.status == "poison"),
        "map_ok": sum(1 for c in maps if c.status == "ok"),
        "map_total": len(maps),
        "book_ok": sum(1 for c in books if c.status == "ok"),
        "book_total": len(books),
        "queue": len(work),
        "queue_chars": sum(c.chars for c in work),
        # rough: ~35 chars/s neural synth on CPU for this model
        "eta_minutes_rough": round(sum(c.chars for c in work) / 35 / 60, 1),
    }

    # per-lesson rollup for the markdown
    lessons: dict[str, dict] = {}
    for c in all_clips:
        L = lessons.setdefault(c.lesson, {
            "kind": "book" if c.lesson.startswith("book-") else "map",
            "ok": 0, "missing": 0, "stale": 0, "poison": 0, "total": 0,
        })
        L["total"] += 1
        L[c.status] = L.get(c.status, 0) + 1

    PLAN_JSON.write_text(json.dumps({
        "summary": summary,
        "lessons": lessons,
        "queue": [
            {
                "priority": round(c.priority, 1),
                "reason": c.reason,
                "lesson": c.lesson,
                "clip": c.clip_id,
                "chars": c.chars,
                "status": c.status,
                "segments": len(sentences(c.text)),
            }
            for c in work
        ],
    }, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Narration plan",
        "",
        f"Generated by `tools/gen_narration.py --plan`.",
        "",
        "## Summary",
        "",
        f"| | count |",
        f"|---|---:|",
        f"| total clips | {summary['total']} |",
        f"| ready | {summary['ok']} |",
        f"| missing | {summary['missing']} |",
        f"| stale (text moved) | {summary['stale']} |",
        f"| poison (unsynthesizable) | {summary['poison']} |",
        f"| maps ready | {summary['map_ok']}/{summary['map_total']} |",
        f"| book ready | {summary['book_ok']}/{summary['book_total']} |",
        f"| **queue** | **{summary['queue']}** (~{summary['eta_minutes_rough']} min) |",
        "",
        "## Ordering rules (highest first)",
        "",
        "1. **Maps before books** — explore is the front door.",
        "2. **Finish partial lessons** — never leave a chapter half-voiced.",
        "3. **Earlier atlas/book order** — reader walks 00 → 44.",
        "4. **Short before long** — momentum + fail-fast on poison text.",
        "5. **Stale before missing** — lying audio is worse than silence.",
        "6. **Segment long blocks** — sentences ≤ "
        f"{SEGMENT_CHARS} chars, silence-trimmed, concatenated.",
        "",
        "## Lessons",
        "",
        "| lesson | kind | ready | missing | stale | poison |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for lid, L in sorted(lessons.items(), key=lambda kv: lesson_order(kv[0])):
        if L["missing"] or L["stale"] or L["poison"]:
            lines.append(
                f"| `{lid}` | {L['kind']} | {L['ok']}/{L['total']} "
                f"| {L['missing']} | {L['stale']} | {L['poison']} |"
            )
    lines += ["", f"Full queue: `{PLAN_JSON.relative_to(ROOT)}` ({len(work)} items).", ""]
    PLAN_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {PLAN_JSON.relative_to(ROOT)} and {PLAN_MD.relative_to(ROOT)}")
    print(
        f"queue {summary['queue']} clips · ~{summary['eta_minutes_rough']} min · "
        f"maps {summary['map_ok']}/{summary['map_total']} · "
        f"book {summary['book_ok']}/{summary['book_total']}"
    )


# --------------------------------------------------------------------------- worker

def worker_ping() -> bool:
    if not SOCK.exists():
        return False
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(str(SOCK))
        s.sendall(b'{"cmd":"ping"}\n')
        ok = s.recv(64).decode().strip().startswith("ok")
        s.close()
        return ok
    except Exception:
        return False


def ensure_worker() -> bool:
    if worker_ping():
        return True
    if not WORKER_PYTHON.exists() or not WORKER_PY.exists():
        print("Inflect worker not installed — run ReadAloud --install-inflect", file=sys.stderr)
        return False
    # kill stale
    subprocess.run(["bash", "-lc",
                    f"if [ -f {SHARE}/inflect-worker.pid ]; then kill $(cat {SHARE}/inflect-worker.pid) 2>/dev/null; fi"],
                   check=False)
    try:
        if SOCK.exists():
            SOCK.unlink()
    except OSError:
        pass
    ready = Path(str(SOCK) + ".ready")
    try:
        if ready.exists():
            ready.unlink()
    except OSError:
        pass

    env = os.environ.copy()
    env["READALOUD_INFLECT_MODEL"] = str(SHARE / "inflect-micro-v2")
    env["READALOUD_INFLECT_SOCK"] = str(SOCK)
    env["READALOUD_INFLECT_READY"] = str(ready)
    log = open(SHARE / "inflect-worker.log", "a", encoding="utf-8")
    proc = subprocess.Popen(
        [str(WORKER_PYTHON), str(WORKER_PY)],
        env=env, stdout=log, stderr=log, start_new_session=True,
    )
    (SHARE / "inflect-worker.pid").write_text(str(proc.pid), encoding="utf-8")
    for _ in range(90):  # model load can take ~30–60s
        if ready.exists() and SOCK.exists():
            time.sleep(0.3)
            if worker_ping():
                print("  worker warm", flush=True)
                return True
        if proc.poll() is not None:
            break
        time.sleep(1)
    print("  worker failed to start — see ~/.local/share/readaloud/inflect-worker.log", file=sys.stderr)
    return False


def speak_segment(text: str, wav: Path, *, timeout: int) -> None:
    wav.parent.mkdir(parents=True, exist_ok=True)
    tmp = wav.with_suffix(".partial.wav")
    if tmp.exists():
        tmp.unlink()
    req = {
        "text": text.strip(),
        "output": str(tmp.resolve()),
        "speed": VOICE_SPEED,
        "variation": VOICE_VARIATION,
        "seed": VOICE_SEED,
    }
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(SOCK))
        s.sendall((json.dumps(req) + "\n").encode("utf-8"))
        resp = s.recv(8192).decode("utf-8", errors="replace").strip()
    finally:
        s.close()
    if not resp.startswith("ok"):
        raise RuntimeError(resp or "empty response")
    if not tmp.exists() or tmp.stat().st_size < 44:
        raise RuntimeError("missing wav")
    tmp.replace(wav)


# Aggressive edge trim. Inflect leaves ~200–300 ms of room-tone on old clips and
# ~0–80 ms on new ones; multi-seg joins double that in the middle unless every
# part is tightened before concat.
TRIM_AF = (
    "silenceremove=start_periods=1:start_silence=0.015:start_threshold=-42dB:"
    "detection=peak,"
    "areverse,"
    "silenceremove=start_periods=1:start_silence=0.03:start_threshold=-42dB:"
    "detection=peak,"
    "areverse"
)


def tight_trim(wav_in: Path, wav_out: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav_in),
         "-af", TRIM_AF, str(wav_out)],
        check=True,
    )


def silence_trim_and_encode(wav: Path) -> Path:
    """Final trim + Opus. No tail pad — gapless Web Audio schedules sample-accurately."""
    trimmed = wav.with_name(wav.stem + ".trim.wav")
    tight_trim(wav, trimmed)
    opus = wav.with_suffix(".opus")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(trimmed),
         "-c:a", "libopus", "-b:a", "28k", "-ac", "1", "-application", "voip", str(opus)],
        check=True,
    )
    for p in (wav, trimmed):
        try:
            p.unlink()
        except OSError:
            pass
    return opus


def concat_wavs(parts: list[Path], dest: Path) -> None:
    """Join pre-trimmed parts with a 20 ms triangular acrossfade (no silence wedge)."""
    if len(parts) == 1:
        parts[0].replace(dest)
        return
    # Chain acrossfade: a+b→t1, t1+c→t2, ...
    cur = parts[0]
    temps: list[Path] = []
    try:
        for i, nxt in enumerate(parts[1:], 1):
            out = dest.with_name(f"{dest.stem}.xf{i}.wav")
            temps.append(out)
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error",
                 "-i", str(cur), "-i", str(nxt),
                 "-filter_complex", "acrossfade=d=0.02:c1=tri:c2=tri",
                 str(out)],
                check=True,
            )
            cur = out
        cur.replace(dest)
    finally:
        for p in parts + temps:
            if p == dest:
                continue
            try:
                p.unlink()
            except OSError:
                pass


def synth_clip(clip: Clip) -> None:
    segs = sentences(clip.text)
    if not segs:
        raise RuntimeError("no speakable text")
    if not re.search(r"[A-Za-z]{2}", " ".join(segs)):
        raise RuntimeError("no letters")

    work = AUDIO / clip.lesson
    work.mkdir(parents=True, exist_ok=True)
    part_paths: list[Path] = []
    try:
        for i, seg in enumerate(segs):
            raw = work / f"{clip.clip_id}.part{i}.raw.wav"
            tight = work / f"{clip.clip_id}.part{i}.wav"
            timeout = min(180, max(30, 20 + len(seg) // 8))
            speak_segment(seg, raw, timeout=timeout)
            # trim EACH segment so multi-seg clips don't accumulate Inflect pads
            tight_trim(raw, tight)
            try:
                raw.unlink()
            except OSError:
                pass
            part_paths.append(tight)
        merged = work / f"{clip.clip_id}.wav"
        concat_wavs(part_paths, merged)
        part_paths.clear()
        silence_trim_and_encode(merged)
        clip.stamp.write_text(clip.text, encoding="utf-8")
    finally:
        for p in part_paths:
            try:
                p.unlink()
            except OSError:
                pass
        for orphan in work.glob(f"{clip.clip_id}.part*"):
            try:
                orphan.unlink()
            except OSError:
                pass
        for orphan in work.glob(f"{clip.clip_id}*.partial.wav"):
            try:
                orphan.unlink()
            except OSError:
                pass


# --------------------------------------------------------------------------- manifest

def build_manifest() -> dict:
    lessons = []
    for path in sorted(SCRIPTS.glob("*.json")):
        script = json.loads(path.read_text(encoding="utf-8"))
        lesson = script["id"]

        def audio_for(clip_id: str):
            rel = f"audio/{lesson}/{clip_id}.opus"
            return rel if (ROOT / "narrate" / rel).exists() else None

        lessons.append({
            "id": lesson,
            "title": script["title"],
            "blurb": script.get("blurb", ""),
            "map": script.get("map"),
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
                    "types": [] if isinstance(v, str) else (v.get("types") or []),
                    "goto": None if isinstance(v, str) else v.get("goto"),
                    "gotoLabel": None if isinstance(v, str) else v.get("gotoLabel"),
                    "audio": audio_for(f"node-{k}-short"),
                    "audioDeep": audio_for(f"node-{k}-deep"),
                }
                for k, v in (script.get("nodes") or {}).items()
            },
        })
    return {"version": 2, "voice": "ReadAloud Inflect", "lessons": lessons}


# --------------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lesson", default="all", help="lesson id or stem, or 'all'")
    ap.add_argument("--force", action="store_true", help="redo even if stamp matches")
    ap.add_argument("--limit", type=int, default=0, help="stop after N successful synths")
    ap.add_argument("--plan", action="store_true", help="write plan only, do not synth")
    ap.add_argument("--manifest-only", action="store_true")
    ap.add_argument("--books-only", action="store_true")
    ap.add_argument("--maps-only", action="store_true")
    args = ap.parse_args()

    if args.manifest_only:
        man = build_manifest()
        MANIFEST.write_text(json.dumps(man, indent=2) + "\n", encoding="utf-8")
        have = sum(1 for l in man["lessons"] for c in l["chapters"] if c["audio"])
        print(f"wrote {MANIFEST.relative_to(ROOT)} — {len(man['lessons'])} lessons, {have} narrated chapters")
        return 0

    all_clips = inventory()
    if args.force:
        for c in all_clips:
            if c.status == "ok":
                c.status = "stale"

    work = plan(all_clips)

    if args.lesson != "all":
        work = [c for c in work if c.lesson == args.lesson or c.lesson.startswith(args.lesson)]
    if args.books_only:
        work = [c for c in work if c.kind.startswith("book")]
    if args.maps_only:
        work = [c for c in work if c.kind.startswith("map")]

    write_plan(all_clips, work)
    if args.plan:
        return 0

    if not work:
        print("nothing to do — atlas fully narrated")
        man = build_manifest()
        MANIFEST.write_text(json.dumps(man, indent=2) + "\n", encoding="utf-8")
        return 0

    if not ensure_worker():
        return 2

    poison = load_poison()
    made = 0
    failed = 0
    t_start = time.time()
    total = len(work)
    if args.limit:
        work = work[: args.limit]

    for i, clip in enumerate(work, 1):
        segs = len(sentences(clip.text))
        print(
            f"[{i}/{len(work)}] {clip.lesson}/{clip.clip_id}  "
            f"{clip.chars}c · {segs} seg · {clip.reason}",
            flush=True,
        )
        t0 = time.time()
        try:
            synth_clip(clip)
            made += 1
            dt = time.time() - t0
            rate = clip.chars / max(dt, 0.1)
            left = len(work) - i
            eta = left * (time.time() - t_start) / max(made, 1)
            print(
                f"    ok {clip.opus.stat().st_size:,} B in {dt:.1f}s "
                f"({rate:.0f} c/s) · eta {eta/60:.1f} min",
                flush=True,
            )
        except Exception as e:
            failed += 1
            print(f"    !! {e}", flush=True)
            # one restart + one retry before poison
            if ensure_worker():
                try:
                    synth_clip(clip)
                    made += 1
                    print(f"    ok after restart", flush=True)
                    continue
                except Exception as e2:
                    print(f"    !! retry failed: {e2}", flush=True)
            poison[clip.key] = {
                "error": str(e)[:200],
                "chars": clip.chars,
                "when": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            save_poison(poison)
            print(f"    quarantined → {POISON.name}", flush=True)

    man = build_manifest()
    MANIFEST.write_text(json.dumps(man, indent=2) + "\n", encoding="utf-8")
    # refresh plan after the run
    write_plan(inventory(), plan(inventory()))
    print(
        f"\ndone: {made} written, {failed} failed/poison · "
        f"{time.time() - t_start:.0f}s · manifest {MANIFEST.relative_to(ROOT)}"
    )
    return 0 if made or not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
