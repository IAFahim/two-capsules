#!/usr/bin/env python3
"""Extract the complete public/internal API surface of the DOTS + BovineLabs package
stack into reference/*.json.

    python3 tools/extract_api.py                    # uses ../vex-ee-3/Library/PackageCache
    python3 tools/extract_api.py --cache /path/to/PackageCache
    PACKAGE_CACHE=/path/to/PackageCache python3 tools/extract_api.py

Writes (all regenerated from scratch, idempotent):
    reference/packages.json     one row per package
    reference/<id>.json         one shard per package: every type record
    reference/search.json       compact [id, name, ns, kind, pkg, roles] rows
    reference/attributes.json   every attribute application site in the corpus
    reference/stats.json        honest self-report of the parse run

LICENSING
    Unity packages are under the Unity Companion License and publicly documented,
    so their XML <summary> text is included.  BovineLabs packages are commercial:
    only signature + file + line are emitted and `doc` is forced to None.  This is
    enforced by the per-package `license` flag in PACKAGES below -- see Scanner.doc_above().

This is a line/brace scanner, not a C# compiler.  Everything it cannot classify is
counted and sampled into stats.json rather than silently dropped.
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reference"

# id, package name, display, license
PACKAGES = [
    ("entities", "com.unity.entities", "Unity Entities", "unity"),
    ("netcode", "com.unity.netcode", "Netcode for Entities", "unity"),
    ("entities-graphics", "com.unity.entities.graphics", "Entities Graphics", "unity"),
    ("core", "com.bovinelabs.core", "BovineLabs Core", "commercial"),
    ("grove", "com.bovinelabs.grove", "BovineLabs Grove", "commercial"),
    ("canopy", "com.bovinelabs.canopy", "BovineLabs Canopy", "commercial"),
    ("nerve", "com.bovinelabs.nerve", "BovineLabs Nerve", "commercial"),
    ("anchor", "com.bovinelabs.anchor", "BovineLabs Anchor", "commercial"),
]

SHARD_LIMIT = 12 * 1024 * 1024

# Directories never scanned.  `~` suffixed folders are invisible to Unity's asset
# pipeline (docs, source-generator projects, samples) so they are not shipped API.
SKIP_DIR_RE = re.compile(r"~$|^Tests$|\.Tests$|\.PerformanceTests$|^Documentation$")


def skip_path(rel: Path) -> bool:
    return any(SKIP_DIR_RE.search(p) for p in rel.parts[:-1])


# ------------------------------------------------------------------ tokenizing

def clean_source(src: str):
    """Blank comments / literals / preprocessor, preserving offsets and newlines.

    Returns (clean_text, {line_no: doc_comment_text}, literal_spans).  Blanking keeps every brace
    that is real code and removes every brace that is not, which is what the scanner
    below relies on.
    """
    out = list(src)
    docs: dict[int, list[str]] = {}
    lits: list[tuple[int, int]] = []  # blanked string/char literal spans
    directives: list[tuple[int, int, str]] = []  # (start, end_of_line, keyword)
    n = len(src)
    i = 0
    line = 1
    fresh = True  # only whitespace seen so far on this line

    def blank(a: int, b: int) -> None:
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            fresh = True
            i += 1
            continue
        if c in " \t\r\ufeff":
            i += 1
            continue
        if fresh and c == "#":
            j = src.find("\n", i)
            j = n if j < 0 else j
            kw = re.match(r"#\s*(if|elif|else|endif)\b", src[i:j])
            if kw:
                directives.append((i, j, kw.group(1)))
            blank(i, j)
            i = j
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            j = n if j < 0 else j
            if src.startswith("///", i) and not src.startswith("////", i):
                docs.setdefault(line, []).append(src[i + 3: j])
            blank(i, j)
            i = j
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            line += src.count("\n", i, j)
            blank(i, j)
            i = j
            fresh = False
            continue
        if c in "\"'" or (c in "@$" and i + 1 < n and src[i + 1] in "\"@$"):
            j, line = skip_literal(src, i, line)
            blank(i, j)
            lits.append((i, j))
            i = j
            fresh = False
            continue
        fresh = False
        i += 1

    dropped = balance_conditionals(out, directives)
    for a, b in dropped:  # literals inside a dropped branch are gone too
        lits = [(x, y) for x, y in lits if y <= a or x >= b]
    return "".join(out), {k: "\n".join(v) for k, v in docs.items()}, lits


def balance_conditionals(out: list, directives: list) -> list:
    """Keep every `#if` branch when each one is a self-contained statement; else
    keep only the first.

    A branch holding whole declarations is brace-balanced and ends on `;`, `}` or
    `,`, so both the runtime and the UNITY_EDITOR API normally stay in the index.
    A branch that is a fragment of one declaration -- `#if` picking between two
    constructor signatures, or between an access modifier and an attribute -- would
    otherwise be concatenated with its siblings into nonsense, so only the first
    survives.  Returns the spans that were blanked.
    """
    def complete(a: int, b: int) -> bool:
        seg = "".join(out[a:b])
        if seg.count("{") != seg.count("}"):
            return False
        seg = seg.strip()
        return not seg or seg[-1] in ";},"

    dropped: list[tuple[int, int]] = []
    stack: list[list] = []
    for start, end, kw in directives:
        if kw == "if":
            stack.append([end, []])  # [current branch start, completed branches]
            continue
        if not stack:
            continue
        top = stack[-1]
        top[1].append((top[0], start))
        top[0] = end
        if kw != "endif":
            continue
        stack.pop()
        branches = top[1]
        if len(branches) > 1 and not all(complete(a, b) for a, b in branches):
            for a, b in branches[1:]:
                dropped.append((a, b))
                for k in range(a, b):
                    if out[k] != "\n":
                        out[k] = " "
    return dropped


def skip_literal(src: str, i: int, line: int):
    """Return (end_offset_exclusive, new_line) for the literal starting at i."""
    n = len(src)
    verbatim = False
    interp = False
    while i < n and src[i] in "@$":
        verbatim = verbatim or src[i] == "@"
        interp = interp or src[i] == "$"
        i += 1
    if i >= n or src[i] not in "\"'":
        return i, line
    quote = src[i]
    i += 1
    depth = 0  # interpolation hole depth
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            if not verbatim and depth == 0:
                return i, line  # unterminated; bail at EOL
            i += 1
            continue
        if verbatim:
            if c == quote:
                if i + 1 < n and src[i + 1] == quote:
                    i += 2
                    continue
                if depth == 0:
                    return i + 1, line
                i += 1
                continue
        else:
            if c == "\\":
                i += 2
                continue
            if c == quote and depth == 0:
                return i + 1, line
        if interp:
            if c == "{":
                if i + 1 < n and src[i + 1] == "{":
                    i += 2
                    continue
                depth += 1
            elif c == "}":
                if depth:
                    depth -= 1
                elif i + 1 < n and src[i + 1] == "}":
                    i += 2
                    continue
            elif depth and c in "\"'":
                i, line = skip_literal(src, i, line)
                continue
        i += 1
    return n, line


# ------------------------------------------------------------------ small utils

WS = re.compile(r"\s+")


def norm(s: str) -> str:
    return WS.sub(" ", s).strip()


def skip_quoted(s: str, i: int) -> int:
    """Index just past the literal starting at i (which is a quote char)."""
    q = s[i]
    i += 1
    while i < len(s):
        if s[i] == "\\":
            i += 2
            continue
        if s[i] == q:
            return i + 1
        i += 1
    return i


def split_top(s: str, sep: str = ",") -> list[str]:
    """Split on `sep` at bracket depth 0 (parens, brackets, braces, angle)."""
    parts, buf, depth, ang = [], [], 0, 0
    i = 0
    while i < len(s):
        c = s[i]
        if c in "\"'":
            j = skip_quoted(s, i)
            buf.append(s[i:j])
            i = j
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif (c == "<" and depth == 0 and (i == 0 or s[i - 1] != "<")
              and i + 1 < len(s) and s[i + 1] not in "<= "):
            ang += 1  # a generic list, not a shift or a comparison
        elif c == ">" and depth == 0 and ang:
            ang -= 1
        if c == sep and depth == 0 and ang == 0:
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def match_bracket(s: str, i: int, open_c: str, close_c: str) -> int:
    """Index just past the bracket that opens at i, or -1."""
    depth = 0
    while i < len(s):
        if s[i] in "\"'":
            i = skip_quoted(s, i)
            continue
        if s[i] == open_c:
            depth += 1
        elif s[i] == close_c:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def split_attrs(pending: str):
    """Peel leading [..] groups. Returns (attrs, assembly_attrs, rest)."""
    attrs: list[str] = []
    asm: list[str] = []
    s = pending.lstrip()
    while s.startswith("["):
        end = match_bracket(s, 0, "[", "]")
        if end < 0:
            break
        body = s[1:end - 1].strip()
        target = ""
        m = re.match(r"^(assembly|module|return|field|method|param|property|event|type)\s*:\s*(.*)$", body, re.S)
        if m:
            target, body = m.group(1), m.group(2)
        for a in split_top(body):
            (asm if target in ("assembly", "module") else attrs).append(norm(a))
        s = s[end:].lstrip()
    return attrs, asm, s


EMBEDDED_ATTR = re.compile(r"\[([^\[\]]*)\]\s*")


def peel_embedded(rest: str):
    """Last resort for `public [Attr] sealed class X` -- a couple of Unity files
    switch the access modifier inside an `#if`, leaving an attribute stranded
    between modifiers.  Returns (attrs, rest_without_them)."""
    found: list[str] = []

    def take(m):
        found.extend(norm(a) for a in split_top(m.group(1)))
        return ""

    return found, norm(EMBEDDED_ATTR.sub(take, rest))


def attr_name(a: str) -> str:
    m = re.match(r"^([\w.]+)", a)
    if not m:
        return a
    name = m.group(1)
    return name.rsplit(".", 1)[-1]


TAG = re.compile(r"<[^>]+>")
CREF = re.compile(r'<(?:see|seealso|paramref|typeparamref)\s+(?:cref|name)\s*=\s*"([^"]*)"\s*/?>')


def summary_of(raw: str) -> str | None:
    if not raw:
        return None
    m = re.search(r"<summary>(.*?)</summary>", raw, re.S | re.I)
    text = m.group(1) if m else raw
    text = CREF.sub(lambda x: x.group(1).split(".")[-1], text)
    text = TAG.sub(" ", text)
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    text = norm(text)
    return text or None


# ------------------------------------------------------------------ declarations

MOD_WORDS = ("public", "private", "protected", "internal", "static", "sealed", "abstract",
             "partial", "readonly", "unsafe", "ref", "new", "file", "virtual", "override",
             "extern", "async", "volatile", "const", "fixed", "required", "sealed")
MOD_RE = re.compile(r"^((?:(?:%s)\s+)*)" % "|".join(sorted(set(MOD_WORDS), key=len, reverse=True)))
TYPE_KW = re.compile(r"^(record\s+struct|record\s+class|record|class|struct|interface|enum|delegate)\b")
IDENT = re.compile(r"^([A-Za-z_@][A-Za-z0-9_]*)")


def parse_type_decl(rest: str):
    """Parse a type declaration body (attributes already peeled). None if not one."""
    mm = MOD_RE.match(rest)
    mods = mm.group(1).split()
    tail = rest[mm.end():].lstrip()
    km = TYPE_KW.match(tail)
    if not km:
        return None
    kind_raw = norm(km.group(1))
    tail = tail[km.end():].lstrip()

    if kind_raw == "delegate":
        # `delegate TReturn Name<T>(args) where ...`
        p = tail.find("(")
        if p < 0:
            return None
        head = tail[:p].rstrip()
        gen = None
        if head.endswith(">"):
            depth = 0
            k = len(head) - 1
            while k >= 0:
                if head[k] == ">":
                    depth += 1
                elif head[k] == "<":
                    depth -= 1
                    if depth == 0:
                        break
                k -= 1
            if k >= 0:
                gen = head[k:]
                head = head[:k]
        nm = re.search(r"([A-Za-z_@][A-Za-z0-9_]*)\s*$", head)
        if not nm:
            return None
        params = tail[p:]
        wi = params.find(" where ")
        constraints = norm(params[wi:]) if wi >= 0 else None
        return dict(kind="delegate", mods=mods, name=nm.group(1).lstrip("@"),
                    generics=gen, bases=[], constraints=constraints,
                    sigtail=norm(params[:wi] if wi >= 0 else params))

    nm = IDENT.match(tail)
    if not nm:
        return None
    name = nm.group(1).lstrip("@")
    tail = tail[nm.end():].lstrip()

    gen = None
    if tail.startswith("<"):
        e = match_bracket(tail, 0, "<", ">")
        if e < 0:
            return None
        gen = norm(tail[:e])
        tail = tail[e:].lstrip()

    if tail.startswith("("):  # record primary constructor
        e = match_bracket(tail, 0, "(", ")")
        if e > 0:
            tail = tail[e:].lstrip()

    bases: list[str] = []
    constraints = None
    wi = tail.find(" where ")
    if tail.startswith("where "):
        wi = 0
    if wi >= 0:
        constraints = norm(tail[wi:])
        tail = tail[:wi]
    tail = tail.strip()
    if tail.startswith(":"):
        bases = split_top(tail[1:])

    kind = "record" if kind_raw.startswith("record") else kind_raw
    if kind_raw == "record struct":
        mods = mods + ["struct"]
    return dict(kind=kind, mods=mods, name=name, generics=gen, bases=bases,
                constraints=constraints, sigtail=None)


ASSIGN = re.compile(r"(?<![=!<>+\-*/%&|^])=(?![=>])")


def top_assign(s: str) -> int:
    depth = 0
    for m in ASSIGN.finditer(s):
        i = m.start()
        depth = 0
        for c in s[:i]:
            if c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
        if depth == 0:
            return i
    return -1


def classify_member(rest: str, type_name: str, has_block: bool):
    """kind for a member declaration body (attributes peeled)."""
    if rest.startswith("~"):
        return "method"
    if re.search(r"\bevent\b", rest):
        return "event"
    cut = top_assign(rest)
    lam = rest.find("=>")
    head = rest[:cut] if cut >= 0 else (rest[:lam] if lam >= 0 else rest)
    paren = head.find("(")
    if paren >= 0:
        before = head[:paren]
        # ctor: modifiers then the type name then '('
        mm = MOD_RE.match(before)
        core = before[mm.end():].strip() if mm else before.strip()
        if core == type_name or core.rstrip(">").split("<")[0] == type_name:
            return "ctor"
        return "method"
    if has_block or lam >= 0 or re.search(r"\bthis\s*\[", rest):
        return "property"
    return "field"


VISIBLE = ("public", "internal", "protected")


def visible(mods: list[str], parent_kind: str | None) -> bool:
    if parent_kind == "interface":
        return True
    if "private" in mods:
        return False
    if any(m in VISIBLE for m in mods):
        return True
    # no access modifier: top level defaults to internal, nested defaults to private
    return parent_kind is None


ACCESSOR = re.compile(r"(?:(private|protected|internal|public)\s+)?\b(get|set|init|add|remove)\b\s*[{;=]")


def accessor_summary(block: str) -> str:
    seen: list[str] = []
    for m in ACCESSOR.finditer(block):
        txt = (m.group(1) + " " if m.group(1) else "") + m.group(2)
        if txt not in seen:
            seen.append(txt)
    return " { " + "".join(a + "; " for a in seen) + "}" if seen else " { }"


def top_arrow(s: str) -> int:
    depth = 0
    i = 0
    while i < len(s) - 1:
        c = s[i]
        if c in "\"'":
            i = skip_quoted(s, i)
            continue
        if c in "([{":  # `=>` never appears inside <>, so angles are ignored here
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "=" and s[i + 1] == ">" and depth <= 0:
            return i
        i += 1
    return -1


def strip_body(sig: str) -> str:
    """Drop expression bodies. Signatures are interop facts; bodies are not ours
    to copy -- this is what keeps commercial implementation code out of the index."""
    i = top_arrow(sig)
    if i < 0:
        return sig
    return sig[:i].rstrip() + " => ..."


def trim_init(sig: str) -> str:
    """Keep short initializers (constants are interop facts), drop long ones."""
    i = top_assign(sig)
    if i < 0:
        return sig
    if len(sig) - i <= 80 and top_arrow(sig[i:]) < 0:
        return sig
    return sig[:i].rstrip() + " = ..."


# ------------------------------------------------------------------ file scanner

class Scope:
    __slots__ = ("kind", "name", "rec", "body_start", "line")

    def __init__(self, kind, name, rec=None, body_start=0, line=0):
        self.kind = kind          # 'ns' | 'type' | 'member'
        self.name = name
        self.rec = rec
        self.body_start = body_start
        self.line = line


class Scanner:
    def __init__(self, pkg_id, license_, relpath, src, stats, attr_sink):
        self.pkg = pkg_id
        self.license = license_
        self.rel = relpath
        self.stats = stats
        self.attrs_out = attr_sink
        self.src = src
        self.clean, self.docs, self.lits = clean_source(src)
        self.lit_at = [a for a, _ in self.lits]
        self.starts = [0]
        for i, ch in enumerate(self.clean):
            if ch == "\n":
                self.starts.append(i + 1)
        self.types: list[dict] = []
        self.ns_file = None  # file-scoped namespace

    def line_of(self, off: int) -> int:
        return bisect.bisect_right(self.starts, off)

    def decl_start(self, a: int, b: int) -> int:
        """Offset of the declaration proper, i.e. past any leading attribute lists.
        `line` must land on `public struct Foo ...`, not on `[Serializable]`."""
        i = a
        while i < b:
            while i < b and self.clean[i] in " \t\r\n\ufeff":
                i += 1
            if i >= b or self.clean[i] != "[":
                return i
            depth = 0
            while i < b:
                if self.clean[i] == "[":
                    depth += 1
                elif self.clean[i] == "]":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                i += 1
        return a

    def text(self, a: int, b: int) -> str:
        """Clean slice with string literals put back, so attribute arguments and
        signatures read as written.  Comments stay blanked."""
        k = bisect.bisect_left(self.lit_at, a)
        if k >= len(self.lits) or self.lits[k][0] >= b:
            return self.clean[a:b]
        buf, pos = [], a
        while k < len(self.lits) and self.lits[k][0] < b:
            ls, le = self.lits[k]
            if le <= b:
                buf.append(self.clean[pos:ls])
                buf.append(self.src[ls:le])
                pos = le
            k += 1
        buf.append(self.clean[pos:b])
        return "".join(buf)

    def doc_above(self, line: int) -> str | None:
        if self.license != "unity":
            return None
        parts = []
        ln = line - 1
        while ln in self.docs:
            parts.append(self.docs[ln])
            ln -= 1
        if not parts:
            return None
        return summary_of("\n".join(reversed(parts)))

    def current_ns(self, stack) -> str:
        ns = [s.name for s in stack if s.kind == "ns"]
        if self.ns_file:
            ns = [self.ns_file] + ns
        return ".".join(ns)

    def type_id(self, stack, name) -> tuple[str, str]:
        outer = [s.rec["name"] for s in stack if s.kind == "type"]
        ns = self.current_ns(stack)
        local = "+".join(outer + [name])
        return ns, (f"{ns}.{local}" if ns else local)

    def record_attrs(self, attrs, target, line, on):
        for a in attrs:
            self.attrs_out.append((attr_name(a), a, target, self.pkg, self.rel, line, on))

    def run(self):
        clean = self.clean
        stack: list[Scope] = []
        pending_start = -1
        pending_from = 0
        n = len(clean)
        i = 0
        while i < n:
            c = clean[i]
            if c in " \t\r\n\ufeff":
                i += 1
                continue
            if pending_start < 0:
                pending_start = i
                pending_from = i
            if c == "[":
                # An attribute argument may hold a collection initializer
                # (`new[] { typeof(int) }`); those braces are not blocks.
                depth = 0
                while i < n:
                    if clean[i] == "[":
                        depth += 1
                    elif clean[i] == "]":
                        depth -= 1
                        if depth == 0:
                            break
                    i += 1
                i += 1
                continue
            if c == "{":
                self.on_open(stack, self.text(pending_from, i), pending_start, i)
                pending_start = -1
                i += 1
                continue
            if c == "}":
                self.on_close(stack, i)
                pending_start = -1
                i += 1
                continue
            if c == ";":
                self.on_semi(stack, self.text(pending_from, i), pending_start, i)
                pending_start = -1
                i += 1
                continue
            i += 1
        if stack:
            self.stats["files_unbalanced"] += 1
        return self.types

    # -- helpers -----------------------------------------------------

    def enclosing_type(self, stack):
        for s in reversed(stack):
            if s.kind == "type":
                return s
            if s.kind == "member":
                return None
            if s.kind == "ns":
                return None
        return None

    def in_member(self, stack):
        return any(s.kind == "member" for s in stack)

    def note_unparsed(self, text, line):
        self.stats["decls_unparsed"] += 1
        if len(self.stats["unparsed_samples"]) < 40:
            self.stats["unparsed_samples"].append(
                {"file": f"{self.pkg}/{self.rel}", "line": line, "text": norm(text)[:180]})

    # -- events ------------------------------------------------------

    def on_open(self, stack, raw, start_off, brace_off):
        docline = self.line_of(start_off)
        line = self.line_of(self.decl_start(start_off, brace_off))
        pending = norm(raw)
        if self.in_member(stack):
            attrs, asm, rest = split_attrs(pending)
            self.record_attrs(attrs, "local", line, None)
            stack.append(Scope("member", None, body_start=brace_off + 1, line=line))
            return
        attrs, asm, rest = split_attrs(pending)
        self.record_attrs(asm, "assembly", line, None)

        m = re.match(r"^namespace\s+([\w.@]+)$", rest)
        if m:
            stack.append(Scope("ns", m.group(1).replace("@", ""), body_start=brace_off + 1, line=line))
            return

        parent = self.enclosing_type(stack)
        td = parse_type_decl(rest)
        if td is None and "[" in rest:
            extra, stripped = peel_embedded(rest)
            td = parse_type_decl(stripped)
            if td:
                attrs = attrs + extra
        if td:
            rec = self.make_type(stack, td, attrs, line, docline)
            stack.append(Scope("type", td["name"], rec=rec, body_start=brace_off + 1, line=line))
            return

        if parent is not None:
            kind = classify_member(rest, parent.name, True)
            self.record_attrs(attrs, kind, line, parent.rec["name"])
            sc = Scope("member", None, body_start=brace_off + 1, line=line)
            sc.rec = (parent, kind, attrs, rest, line, docline)
            stack.append(sc)
            return

        if rest and not rest.startswith("using ") and rest not in ("unsafe", "static"):
            self.note_unparsed(rest, line)
        stack.append(Scope("member", None, body_start=brace_off + 1, line=line))

    def on_close(self, stack, brace_off):
        if not stack:
            self.stats["files_unbalanced"] += 1
            return
        sc = stack.pop()
        if sc.kind == "type" and sc.rec is not None and sc.rec["kind"] == "enum":
            self.enum_values(sc, brace_off)
        elif sc.kind == "member" and isinstance(sc.rec, tuple):
            parent, kind, attrs, rest, line, docline = sc.rec
            sig = strip_body(norm(rest))
            if kind == "property":
                sig += accessor_summary(self.clean[sc.body_start:brace_off])
            self.add_member(parent, kind, attrs, sig, line, docline)

    def on_semi(self, stack, raw, start_off, end_off):
        if self.in_member(stack):
            return
        docline = self.line_of(start_off)
        line = self.line_of(self.decl_start(start_off, end_off))
        pending = norm(raw)
        if not pending:
            return
        attrs, asm, rest = split_attrs(pending)
        self.record_attrs(asm, "assembly", line, None)
        if not rest:
            return
        if re.match(r"^(global\s+)?using\b|^extern\s+alias\b", rest):
            return
        m = re.match(r"^namespace\s+([\w.@]+)$", rest)
        if m:
            self.ns_file = m.group(1).replace("@", "")
            return
        parent = self.enclosing_type(stack)
        td = parse_type_decl(rest)
        if td:
            self.make_type(stack, td, attrs, line, docline)
            return
        if parent is not None:
            kind = classify_member(rest, parent.name, False)
            self.record_attrs(attrs, kind, line, parent.rec["name"])
            self.add_member(parent, kind, attrs, strip_body(trim_init(norm(rest))), line, docline)
            return
        self.note_unparsed(rest, line)

    # -- builders ----------------------------------------------------

    def make_type(self, stack, td, attrs, line, docline):
        self.stats["decls_found"] += 1
        parent = self.enclosing_type(stack)
        parent_kind = parent.rec["kind"] if parent else None
        shown = visible(td["mods"], parent_kind)
        ns, local = self.type_id(stack, td["name"])
        rec = {
            "id": f"{self.pkg}:{local}",
            "name": td["name"],
            "ns": ns,
            "kind": td["kind"],
            "mods": td["mods"],
            "generics": td["generics"],
            "bases": td["bases"],
            "attrs": attrs,
            "doc": self.doc_above(docline),
            "file": self.rel,
            "line": line,
            "roles": [],
            "members": [],
        }
        if td["constraints"]:
            rec["constraints"] = td["constraints"]
        if td["sigtail"]:
            rec["params"] = td["sigtail"]
        self.record_attrs(attrs, "type", line, td["name"])
        if shown:
            self.types.append(rec)
        else:
            self.stats["types_hidden"] += 1
        return rec

    def add_member(self, parent, kind, attrs, sig, line, docline):
        self.stats["decls_found"] += 1
        if parent.rec is None:
            return
        mm = MOD_RE.match(sig)
        mods = mm.group(1).split() if mm else []
        if not visible(mods, parent.rec["kind"]):
            self.stats["members_hidden"] += 1
            return
        entry = {"kind": kind, "sig": sig if not attrs else
                 "".join("[%s] " % a for a in attrs) + sig, "line": line}
        doc = self.doc_above(docline)
        if doc:
            entry["doc"] = doc
        parent.rec["members"].append(entry)

    def enum_values(self, sc, brace_off):
        body = self.text(sc.body_start, brace_off)
        base = sc.body_start
        for part in split_top(body):
            attrs, _asm, rest = split_attrs(norm(part))
            m = IDENT.match(rest)
            if not m:
                continue
            name = m.group(1)
            off = base + body.find(name, 0)
            # locate this member's own offset accurately
            idx = body.find(part.strip()[:40])
            if idx >= 0:
                seg = body[idx:]
                k = seg.find(name)
                if k >= 0:
                    off = base + idx + k
            line = self.line_of(off)
            self.stats["decls_found"] += 1
            self.record_attrs(attrs, "enumvalue", line, sc.rec["name"])
            entry = {"kind": "enumvalue",
                     "sig": ("".join("[%s] " % a for a in attrs) + norm(rest)),
                     "line": line}
            doc = self.doc_above(line)
            if doc:
                entry["doc"] = doc
            sc.rec["members"].append(entry)


# ------------------------------------------------------------------ roles

COMPONENT_IFACES = {"IComponentData", "IBufferElementData", "ISharedComponentData",
                    "ICleanupComponentData", "ICleanupBufferElementData",
                    "ICleanupSharedComponentData", "IEnableableComponent"}
RPC_IFACES = {"IRpcCommand", "IApproveConnection", "IRpcCommandSerializer"}
SERIALIZER_IFACES = {"IGhostComponentSerializer", "IGhostSerializer", "IGhostSerializerCollection",
                     "IGhostPrefabSerializerCollection", "IComponentSerializer"}
JOB_RE = re.compile(r"^IJob(Entity|Chunk|ParallelFor\w*|EntityChunkBeginEnd|For\w*|Filter\w*|"
                    r"EntityBatch\w*|Extension\w*)?$|^IJob$|^IJob[A-Z]\w*$")


def simple(base: str) -> str:
    b = base.split("<")[0].strip()
    return b.rsplit(".", 1)[-1]


def closure(all_types: dict, seeds: set[str]) -> set[str]:
    """Names whose base chain reaches any of `seeds` (matched on simple names)."""
    by_name: dict[str, list[str]] = {}
    for rec in all_types.values():
        by_name.setdefault(rec["name"], []).extend(simple(b) for b in rec["bases"])
    found = set(seeds)
    changed = True
    while changed:
        changed = False
        for name, bases in by_name.items():
            if name in found:
                continue
            if any(b in found for b in bases):
                found.add(name)
                changed = True
    return found


def assign_roles(all_types: dict, blob_targets: set[str]) -> None:
    attr_names = closure(all_types, {"Attribute", "PropertyAttribute"})
    authoring_names = closure(all_types, {"MonoBehaviour", "ScriptableObject"})
    group_names = closure(all_types, {"ComponentSystemGroup"})
    system_names = closure(all_types, {"SystemBase", "ComponentSystemBase"})

    for rec in all_types.values():
        bases = {simple(b) for b in rec["bases"]}
        attrs = {attr_name(a) for a in rec["attrs"]}
        roles = []
        if bases & COMPONENT_IFACES:
            roles.append("component")
        if "ISystem" in bases or "ISystemStartStop" in bases or rec["name"] in system_names:
            roles.append("system")
        if rec["name"] in group_names:
            roles.append("group")
            if "system" not in roles:
                roles.append("system")
        if rec["name"] in attr_names:
            roles.append("attribute")
        if any(JOB_RE.match(b) for b in bases):
            roles.append("job")
        if rec["name"] in authoring_names:
            roles.append("authoring")
        if any(simple(b) == "Baker" for b in rec["bases"]):
            roles.append("baker")
            if "authoring" in roles:
                roles.remove("authoring")
        if "IInputComponentData" in bases:
            roles.append("input")
        if "ICommandData" in bases:
            roles.append("command")
        if bases & RPC_IFACES:
            roles.append("rpc")
        if bases & SERIALIZER_IFACES or any(b.startswith("IGhost") and "Serializer" in b for b in bases):
            roles.append("serializer")
        if (rec["name"].endswith(("Config", "Settings", "Configuration"))
                or "ISettings" in bases or "ISettingsCategory" in bases):
            roles.append("settings")
        if (rec["name"] in blob_targets
                or "MayOnlyLiveInBlobStorage" in attrs
                or any("BlobArray<" in m["sig"] or "BlobPtr<" in m["sig"] or "BlobString" in m["sig"]
                       for m in rec["members"])):
            roles.append("blob")
        rec["roles"] = sorted(set(roles))


# ------------------------------------------------------------------ driver

FIXTURE = '''﻿// <copyright file="F.cs" company="X"> </copyright>
namespace N.Sub
{
    using System;

    /// <summary> A <see cref="Thing"/> holder. </summary>
    [Serializable]
    [GenerateTestsForBurstCompatibility(GenericTypeArguments = new[] { typeof(int) },
        RequiredUnityDefine = "ENABLE_CHECKS")]
    public readonly struct Holder<T> : IComponentData, IEnableableComponent
        where T : unmanaged
    {
        public const int Cap = 8;
        [GhostField] public int Value;
        private int hidden;

        public readonly int Doubled => this.Value * 2;

        public int Prop { get; private set; }

#if UNITY_EDITOR
        public Holder(int a, AtomicSafetyHandle h)
#else
        public Holder(int a)
#endif
        {
            this.Value = a;
        }

        public enum Mode : byte
        {
            Off = 0,
            On = 1 << 2,
        }

        internal struct Inner { public float X; }
    }

    public delegate void Cb<TArg>(TArg a) where TArg : struct;
}
'''


def selfcheck() -> int:
    stats = {"files_scanned": 0, "files_failed": 0, "files_unbalanced": 0, "decls_found": 0,
             "decls_unparsed": 0, "types_hidden": 0, "members_hidden": 0,
             "unparsed_samples": [], "failed_files": []}
    sink: list = []
    types = {t["name"]: t for t in Scanner("p", "unity", "F.cs", FIXTURE, stats, sink).run()}
    h = types["Holder"]
    assert h["kind"] == "struct" and h["mods"] == ["public", "readonly"], h["mods"]
    assert h["generics"] == "<T>" and h["constraints"] == "where T : unmanaged", h
    assert h["bases"] == ["IComponentData", "IEnableableComponent"], h["bases"]
    assert h["doc"] == "A Thing holder.", h["doc"]
    assert FIXTURE.splitlines()[h["line"] - 1].lstrip().startswith("public readonly struct")
    assert 'RequiredUnityDefine = "ENABLE_CHECKS"' in h["attrs"][1], h["attrs"]
    sigs = [m["sig"] for m in h["members"]]
    assert "public const int Cap = 8" in sigs, sigs
    assert "[GhostField] public int Value" in sigs, sigs
    assert not any("hidden" in s for s in sigs), "private member leaked"
    assert "public readonly int Doubled => ..." in sigs, "expression body not stripped"
    assert "public int Prop { get; private set; }" in sigs, sigs
    assert sum(s.startswith("public Holder(") for s in sigs) == 1, "#if branches concatenated"
    assert types["Mode"]["kind"] == "enum"
    assert [m["sig"] for m in types["Mode"]["members"]] == ["Off = 0", "On = 1 << 2"]
    assert types["Inner"]["mods"] == ["internal"]
    assert types["Cb"]["kind"] == "delegate" and types["Cb"]["generics"] == "<TArg>"

    ids = {t["id"] for t in Scanner("p", "unity", "F.cs", FIXTURE, stats, []).run()}
    assert "p:N.Sub.Holder+Inner" in ids and "p:N.Sub.Holder+Mode" in ids, sorted(ids)

    bl = Scanner("p", "commercial", "F.cs", FIXTURE, stats, []).run()
    assert all(t["doc"] is None and all("doc" not in m for m in t["members"]) for t in bl), \
        "commercial licence gate let doc text through"
    assert stats["decls_unparsed"] == 0, stats["unparsed_samples"]
    assert {a[0] for a in sink} >= {"Serializable", "GhostField"}, sink
    print("selfcheck: ok")
    return 0


def resolve_cache(arg: str | None) -> Path:
    for cand in (arg, os.environ.get("PACKAGE_CACHE"),
                 ROOT.parent / "vex-ee-3" / "Library" / "PackageCache"):
        if cand and Path(cand).is_dir():
            return Path(cand)
    sys.exit("PackageCache not found; pass --cache /path/to/Library/PackageCache")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=None)
    ap.add_argument("--selfcheck", action="store_true",
                    help="run the parser over a fixture and assert; no output written")
    args = ap.parse_args()
    if args.selfcheck:
        return selfcheck()
    cache = resolve_cache(args.cache)
    OUT.mkdir(exist_ok=True)

    stats = {"files_scanned": 0, "files_failed": 0, "files_unbalanced": 0,
             "decls_found": 0, "decls_unparsed": 0, "types_hidden": 0,
             "members_hidden": 0, "unparsed_samples": [], "failed_files": []}
    attr_sites: list[tuple] = []
    blob_targets: set[str] = set()
    blob_re = re.compile(r"BlobAssetReference\s*<\s*([\w.]+)")

    pkg_rows = []
    shards: dict[str, list[dict]] = {}

    for pid, pname, display, lic in PACKAGES:
        matches = sorted(cache.glob(pname + "@*"))
        if not matches:
            sys.exit(f"package not found in cache: {pname}@*")
        root = matches[0]
        meta = json.loads((root / "package.json").read_text(encoding="utf-8"))

        types: dict[str, dict] = {}
        files = loc = 0
        for path in sorted(root.rglob("*.cs")):
            rel = path.relative_to(root)
            if skip_path(rel):
                continue
            files += 1
            stats["files_scanned"] += 1
            try:
                # utf-8-sig: many BovineLabs files carry a BOM, which is not \s and
                # would otherwise glue itself to the first declaration on the file.
                src = path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                stats["files_failed"] += 1
                stats["failed_files"].append(f"{pid}/{rel}")
                continue
            loc += src.count("\n") + 1
            try:
                sc = Scanner(pid, lic, rel.as_posix(), src, stats, attr_sites)
                found = sc.run()
            except Exception as exc:  # noqa: BLE001 - report, never drop silently
                stats["files_failed"] += 1
                stats["failed_files"].append(f"{pid}/{rel}: {type(exc).__name__}: {exc}")
                continue
            blob_targets.update(m.rsplit(".", 1)[-1] for m in blob_re.findall(sc.clean))
            for rec in found:
                prev = types.get(rec["id"])
                if prev is None:
                    types[rec["id"]] = rec
                    continue
                if "files" not in prev:  # partial type: parallel files[]/lines[]
                    prev["files"] = [prev["file"]]
                    prev["lines"] = [prev["line"]]
                    for m in prev["members"]:  # a merged type spans files: say which
                        m["file"] = prev["file"]
                prev["files"].append(rec["file"])
                prev["lines"].append(rec["line"])
                for m in rec["members"]:
                    m["file"] = rec["file"]
                for key in ("mods", "bases", "attrs"):
                    for v in rec[key]:
                        if v not in prev[key]:
                            prev[key].append(v)
                prev["members"].extend(rec["members"])
                if prev["doc"] is None:
                    prev["doc"] = rec["doc"]
                if rec.get("constraints") and not prev.get("constraints"):
                    prev["constraints"] = rec["constraints"]

        shards[pid] = list(types.values())
        pkg_rows.append({"id": pid, "pkg": pname, "version": meta.get("version", "?"),
                         "display": display, "license": lic, "files": files, "loc": loc,
                         "types": len(types), "members": 0})

    all_types = {r["id"]: r for recs in shards.values() for r in recs}
    assign_roles(all_types, blob_targets)

    # ---- write shards + search + packages
    search_rows = []
    for row in pkg_rows:
        recs = sorted(shards[row["id"]], key=lambda r: (r["ns"], r["name"]))
        row["members"] = sum(len(r["members"]) for r in recs)
        for r in recs:
            search_rows.append([r["id"], r["name"], r["ns"], r["kind"], row["id"], r["roles"]])
        blob = json.dumps({"package": row["id"], "types": recs}, separators=(",", ":"))
        if len(blob.encode()) > SHARD_LIMIT:
            n = len(blob.encode()) // SHARD_LIMIT + 1
            step = (len(recs) + n - 1) // n
            names = []
            for k in range(n):
                part = recs[k * step:(k + 1) * step]
                fn = f"{row['id']}-{k + 1}.json"
                (OUT / fn).write_text(json.dumps({"package": row["id"], "types": part},
                                                 separators=(",", ":")), encoding="utf-8")
                names.append(fn)
            row["shards"] = names
            (OUT / f"{row['id']}.json").unlink(missing_ok=True)
        else:
            (OUT / f"{row['id']}.json").write_text(blob, encoding="utf-8")

    (OUT / "packages.json").write_text(json.dumps(pkg_rows, indent=1), encoding="utf-8")
    (OUT / "search.json").write_text(
        json.dumps({"v": 1, "rows": search_rows}, separators=(",", ":")), encoding="utf-8")

    # ---- attributes
    decl_by_name: dict[str, dict] = {}
    for rec in all_types.values():
        if "attribute" in rec["roles"]:
            decl_by_name.setdefault(rec["name"], rec)
            if rec["name"].endswith("Attribute"):
                decl_by_name.setdefault(rec["name"][:-9], rec)

    agg: dict[str, dict] = {}
    for name, as_written, target, pkg, rel, line, on in attr_sites:
        a = agg.get(name)
        if a is None:
            a = agg[name] = {"name": name, "fq": None, "declaredIn": None, "declType": None,
                             "uses": 0, "byPackage": {}, "targets": set(), "_ex": {}}
        a["uses"] += 1
        a["byPackage"][pkg] = a["byPackage"].get(pkg, 0) + 1
        a["targets"].add(target)
        bucket = a["_ex"].setdefault(pkg, [])
        if len(bucket) < 3:
            bucket.append({"pkg": pkg, "file": rel, "line": line, "on": on,
                           "as": "[%s]" % as_written})

    attributes = []
    for name, a in agg.items():
        decl = decl_by_name.get(name) or decl_by_name.get(name + "Attribute")
        if decl:
            a["declaredIn"] = decl["id"].split(":")[0]
            a["declType"] = decl["id"]
            a["fq"] = f"{decl['ns']}.{name}" if decl["ns"] else name
        ex = []
        pools = list(a.pop("_ex").values())
        k = 0
        while len(ex) < 8 and any(k < len(p) for p in pools):
            for p in pools:
                if k < len(p) and len(ex) < 8:
                    ex.append(p[k])
            k += 1
        a["targets"] = sorted(a["targets"])
        a["examples"] = ex
        attributes.append(a)
    attributes.sort(key=lambda x: (-x["uses"], x["name"]))
    (OUT / "attributes.json").write_text(json.dumps(attributes, separators=(",", ":")),
                                         encoding="utf-8")

    stats["unparsed_samples"] = stats["unparsed_samples"][:20]
    stats["failed_files"] = stats["failed_files"][:20]
    (OUT / "stats.json").write_text(json.dumps(
        {"generated_by": "tools/extract_api.py", "packages": pkg_rows, "parse": stats},
        indent=1), encoding="utf-8")

    for r in pkg_rows:
        print(f"{r['id']:>18}  {r['version']:<8} files={r['files']:<5} "
              f"types={r['types']:<5} members={r['members']}")
    print(f"parse: scanned={stats['files_scanned']} failed={stats['files_failed']} "
          f"unbalanced={stats['files_unbalanced']} decls={stats['decls_found']} "
          f"unparsed={stats['decls_unparsed']} "
          f"({100 * stats['decls_unparsed'] / max(1, stats['decls_found']):.2f}%)")
    print(f"attributes: {len(attributes)} distinct, "
          f"{sum(a['uses'] for a in attributes)} application sites")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
