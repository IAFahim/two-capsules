#!/usr/bin/env python3
"""Join the book to the reference index.

Scans every `backticked identifier` in book/*.md, resolves it against the
generated type index, and writes the map the reference browser uses to show
"Explained in <chapter>" on a type page.

    python3 tools/link_book.py

Writes:
    reference/links.json     { "<type id>": [{slug, title}, ...] }

Prints a coverage report, and — more usefully — the identifiers the book
mentions that resolve to nothing. Those are typos, renames, or types that
never existed. It is a free correctness pass over the whole book.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOK = ROOT / "book"
REF = ROOT / "reference"

# A backticked run that looks like a C# identifier, optionally qualified or
# generic: EntityManager, Unity.Entities.World, GhostField, IJobEntity,
# NetworkStreamConnection, BlobAssetReference<GraphData>
IDENT = re.compile(r"`([A-Za-z_][A-Za-z0-9_.]*(?:<[^`>]*>)?)`")

# Things that are English, filenames, or shell — not types.
NOISE = re.compile(
    r"^(?:true|false|null|new|ref|in|out|var|this|struct|class|if|else|for|foreach|"
    r"public|private|internal|static|readonly|partial|unsafe|void|int|uint|float|bool|byte|"
    r"string|double|long|short|sizeof|default|return|using|namespace|enum|interface)$",
    re.IGNORECASE,
)


def load_index():
    rows = json.loads((REF / "search.json").read_text(encoding="utf-8"))["rows"]
    by_name = defaultdict(list)   # simple name  -> [id]
    by_fq = {}                    # Namespace.Name -> id
    for r in rows:
        tid, name, ns = r[0], r[1], r[2]
        by_name[name].append(tid)
        by_fq[(ns + "." + name) if ns else name] = tid
        # attributes are written without the Attribute suffix in prose
        if name.endswith("Attribute"):
            by_name[name[: -len("Attribute")]].append(tid)
    return by_name, by_fq


def chapters():
    out = []
    for md in sorted(BOOK.glob("*.md")):
        text = md.read_text(encoding="utf-8")
        first = next((l for l in text.split("\n") if l.startswith("# ")), "# " + md.stem)
        out.append((md.stem, first[2:].strip(), text))
    return out


def main() -> int:
    if not (REF / "search.json").exists():
        print("reference/search.json missing — run tools/extract_api.py first.")
        return 2

    by_name, by_fq = load_index()
    links: dict[str, list] = defaultdict(list)
    seen_pairs = set()
    unresolved: dict[str, set] = defaultdict(set)
    ambiguous: dict[str, int] = defaultdict(int)
    hit = miss = 0

    for slug, title, text in chapters():
        for raw in IDENT.findall(text):
            token = raw.split("<")[0]
            if NOISE.match(token) or len(token) < 3:
                continue

            tid = by_fq.get(token)
            if tid is None:
                cands = by_name.get(token.rsplit(".", 1)[-1], [])
                if len(cands) == 1:
                    tid = cands[0]
                elif len(cands) > 1:
                    # prefer an exact simple-name match, else record the collision
                    ambiguous[token] += 1
                    tid = cands[0]

            if tid is None:
                # only count things that really look like a type, not fields/methods
                if token[0].isupper():
                    unresolved[token].add(slug)
                    miss += 1
                continue

            hit += 1
            if (tid, slug) in seen_pairs:
                continue
            seen_pairs.add((tid, slug))
            links[tid].append({"slug": slug, "title": title})

    REF.mkdir(exist_ok=True)
    (REF / "links.json").write_text(json.dumps(links, indent=None) + "\n", encoding="utf-8")

    print(f"{len(links)} types linked from {len(chapters())} chapters "
          f"({len(seen_pairs)} type/chapter pairs)")
    print(f"resolved {hit} mentions, unresolved {miss}")

    if ambiguous:
        print(f"\n{len(ambiguous)} ambiguous simple names (took first match):")
        for t, n in sorted(ambiguous.items(), key=lambda x: -x[1])[:15]:
            print(f"  {t:38s} x{n}")

    if unresolved:
        print(f"\n{len(unresolved)} identifiers the book names that the index does not have —")
        print("check these: a typo, a rename, or a type from a package we do not index.")
        for t, slugs in sorted(unresolved.items(), key=lambda x: -len(x[1]))[:40]:
            where = ", ".join(sorted(slugs)[:4]) + ("…" if len(slugs) > 4 else "")
            print(f"  {t:38s} {where}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
