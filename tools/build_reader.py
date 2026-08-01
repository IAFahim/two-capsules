#!/usr/bin/env python3
"""Turn book/*.md into reader payloads: HTML fragments + narration scripts.

    python3 tools/build_reader.py            # html + narration text
    python3 tools/build_reader.py --html-only

Writes:
    reader/<slug>.json   { title, html, blocks:[{id, text}] }
    reader/index.json    chapter list
    narrate/scripts/book-<slug>.json   (consumed by gen_narration.py)

The markdown subset is exactly what this book uses: ATX headings, paragraphs,
fenced code, mermaid fences, tables, blockquotes, unordered/ordered lists,
links, bold/italic/inline code, and horizontal rules. No dependency.
"""
from __future__ import annotations

import argparse
import html as H
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOK = ROOT / "book"
OUT = ROOT / "reader"
SCRIPTS = ROOT / "narrate" / "scripts"

# ---------------------------------------------------------------- inline

def inline(s: str) -> str:
    out, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c == "`":
            j = s.find("`", i + 1)
            if j > 0:
                out.append("<code>" + H.escape(s[i + 1:j]) + "</code>")
                i = j + 1
                continue
        if c == "[":
            m = re.match(r"\[([^\]]+)\]\(([^)]+)\)", s[i:])
            if m:
                out.append('<a href="%s">%s</a>' % (H.escape(m.group(2)), inline(m.group(1))))
                i += m.end()
                continue
        if s.startswith("**", i):
            j = s.find("**", i + 2)
            if j > 0:
                out.append("<strong>" + inline(s[i + 2:j]) + "</strong>")
                i = j + 2
                continue
        if c == "*":
            j = s.find("*", i + 1)
            if j > 0 and j != i + 1:
                out.append("<em>" + inline(s[i + 1:j]) + "</em>")
                i = j + 1
                continue
        out.append(H.escape(c))
        i += 1
    return "".join(out)


# ---------------------------------------------------------------- speech

DROP = re.compile(r"^(?:\||```|>|\s*$)")
SUBS = [
    (r"`([^`]+)`", r"\1"),
    (r"\[([^\]]+)\]\([^)]+\)", r"\1"),
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
    (r"([a-z])([A-Z])", r"\1 \2"),          # PlayerMoveSystem -> Player Move System
    (r"\s{2,}", " "),
    (r"\s+([,.;:])", r"\1"),
    (r"([,.;:]){2,}", r"\1"),
]


def speakable(text: str) -> str:
    # A blockquote can contain a fenced code block. Its contents are not speech —
    # they reach the TTS frontend as raw punctuation and produce no phonemes at all,
    # which kills the worker rather than just skipping the clip.
    s = re.sub(r"`{3}\w*.*?(?:`{3}|$)", " ", text, flags=re.S)
    for pat, rep in SUBS:
        s = re.sub(pat, rep, s)
    s = s.replace("⚡", "").replace("💀", "").replace("🔬", "").replace("✅", "yes").replace("❌", "no")
    return s.strip()


# ---------------------------------------------------------------- block parse

def render(md: str):
    """Return (html, narratable_blocks)."""
    lines = md.split("\n")
    out, blocks = [], []
    i, n = 0, len(lines)
    bid = 0

    def emit_block(kind: str, text: str, hid: str):
        nonlocal bid
        spoken = speakable(text)
        if len(spoken) < 40:
            return
        blocks.append({"id": hid, "kind": kind, "text": spoken})

    while i < n:
        line = lines[i]

        if line.startswith("```"):
            lang = line[3:].strip()
            j = i + 1
            buf = []
            while j < n and not lines[j].startswith("```"):
                buf.append(lines[j]); j += 1
            body = "\n".join(buf)
            if lang == "mermaid":
                out.append('<pre class="mermaid">%s</pre>' % H.escape(body))
            else:
                out.append('<pre class="code"><code>%s</code></pre>' % H.escape(body))
            i = j + 1
            continue

        if re.match(r"^#{1,6} ", line):
            lvl = len(line) - len(line.lstrip("#"))
            txt = line[lvl:].strip()
            bid += 1
            hid = "b%d" % bid
            out.append('<h%d id="%s" data-b="%s">%s</h%d>' % (lvl, hid, hid, inline(txt), lvl))
            emit_block("heading", txt, hid)
            i += 1
            continue

        if line.startswith("|"):
            rows = []
            while i < n and lines[i].startswith("|"):
                rows.append(lines[i]); i += 1
            cells = [[c.strip() for c in r.strip("|").split("|")] for r in rows]
            cells = [c for c in cells if not all(re.fullmatch(r":?-{2,}:?", x or "") for x in c)]
            if cells:
                head = cells[0]
                body = cells[1:]
                t = ["<table><thead><tr>"] + ["<th>%s</th>" % inline(c) for c in head] + ["</tr></thead><tbody>"]
                for r in body:
                    t += ["<tr>"] + ["<td>%s</td>" % inline(c) for c in r] + ["</tr>"]
                t.append("</tbody></table>")
                out.append("".join(t))
            continue

        if line.startswith(">"):
            buf = []
            while i < n and lines[i].startswith(">"):
                buf.append(lines[i].lstrip("> ").rstrip()); i += 1
            txt = " ".join(x for x in buf if x)
            bid += 1
            hid = "b%d" % bid
            out.append('<blockquote id="%s" data-b="%s">%s</blockquote>' % (hid, hid, inline(txt)))
            emit_block("note", txt, hid)
            continue

        if re.match(r"^\s*[-*] ", line) or re.match(r"^\s*\d+\. ", line):
            ordered = bool(re.match(r"^\s*\d+\. ", line))
            items = []
            while i < n and (re.match(r"^\s*[-*] ", lines[i]) or re.match(r"^\s*\d+\. ", lines[i]) or
                             (lines[i].startswith("  ") and lines[i].strip() and items)):
                l = lines[i]
                if re.match(r"^\s*[-*] ", l) or re.match(r"^\s*\d+\. ", l):
                    items.append(re.sub(r"^\s*(?:[-*]|\d+\.)\s+", "", l))
                else:
                    items[-1] += " " + l.strip()
                i += 1
            tag = "ol" if ordered else "ul"
            bid += 1
            hid = "b%d" % bid
            out.append('<%s id="%s" data-b="%s">%s</%s>' % (
                tag, hid, hid, "".join("<li>%s</li>" % inline(x) for x in items), tag))
            emit_block("list", " ".join(items), hid)
            continue

        if re.match(r"^-{3,}$", line.strip()):
            out.append("<hr />"); i += 1; continue

        if not line.strip():
            i += 1; continue

        buf = []
        while i < n and lines[i].strip() and not lines[i].startswith(("#", "|", ">", "```")) \
                and not re.match(r"^\s*[-*] ", lines[i]) and not re.match(r"^\s*\d+\. ", lines[i]) \
                and not re.match(r"^-{3,}$", lines[i].strip()):
            buf.append(lines[i].strip()); i += 1
        txt = " ".join(buf)
        bid += 1
        hid = "b%d" % bid
        out.append('<p id="%s" data-b="%s">%s</p>' % (hid, hid, inline(txt)))
        emit_block("para", txt, hid)

    return "\n".join(out), blocks


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--html-only", action="store_true")
    args = ap.parse_args()

    OUT.mkdir(exist_ok=True)
    SCRIPTS.mkdir(parents=True, exist_ok=True)
    index = []

    for md_path in sorted(BOOK.glob("*.md")):
        slug = md_path.stem
        md = md_path.read_text(encoding="utf-8")
        first = next((l for l in md.split("\n") if l.startswith("# ")), "# " + slug)
        title = first[2:].strip()
        html, blocks = render(md)

        (OUT / f"{slug}.json").write_text(json.dumps(
            {"slug": slug, "title": title, "html": html,
             "blocks": [{"id": b["id"], "kind": b["kind"]} for b in blocks]},
            indent=None) + "\n", encoding="utf-8")

        if not args.html_only:
            (SCRIPTS / f"book-{slug}.json").write_text(json.dumps({
                "id": f"book-{slug}",
                "title": title,
                "map": None,
                "blurb": "",
                "chapters": [{"id": b["id"], "title": b["id"], "focus": None, "text": b["text"]}
                             for b in blocks],
                "nodes": {},
            }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        index.append({"slug": slug, "title": title, "blocks": len(blocks)})
        print(f"{slug:28s} {len(blocks):3d} narratable blocks")

    (OUT / "index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    total = sum(c["blocks"] for c in index)
    print(f"\n{len(index)} chapters, {total} blocks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
