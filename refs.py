#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]
# ///
"""
refs.py -- list the works a paper cites, and say which of them are already in
the library.

    uv run --script refs.py FANG1995Polycyanate            # every reference
    uv run --script refs.py FANG1995Polycyanate --have     # only ones we hold
    uv run --script refs.py FANG1995Polycyanate --missing  # only ones we don't
    uv run --script refs.py --search "cyanate ester"       # find the key first

Two sources, tried in that order:

  crossref  the publisher's own deposited reference list, keyed by the paper's
            DOI. Structured, carries DOIs of its own, and is not guessing --
            but only about three quarters of the library's DOIs have one, and
            pre-1995 papers usually have none.
  text      parsed out of text/<key>.md. Works on numbered reference lists and
            on author-year lists that survived extraction with one entry per
            line. Reflowed two-column reference lists come out badly, and the
            output says so rather than pretending otherwise.

Each reference is matched against library.bib, and every match prints how it
was made, because the weaker ones are worth an eye:

  doi           the reference and the library entry carry the same DOI
  title         normalized titles agree
  title-in-raw  the library entry's title words are present in the raw text
  author-year+  first author, year, volume AND first page all agree

Matching is deliberately strict. Same first author and same year is NOT enough
on its own -- prolific authors publish several times a year -- so a reference
that cannot be corroborated is left unmatched rather than given a wrong key.
Expect false negatives, not false positives: "not found" means "not found by
these rules", not "definitely not in the library".

The mirror is one-way and this script only reads it; nothing here is written
back to Mendeley.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field, asdict
from difflib import SequenceMatcher
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from mendeley_mirror import DEFAULT_OUT
except ImportError:
    sys.exit("refs.py must sit in the same folder as mendeley_mirror.py")

CROSSREF = "https://api.crossref.org/works/"
UA = "offprint refs.py (mailto:cfa22@drexel.edu)"

# Words too common to count as evidence that two titles are the same work.
STOP = {
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or",
    "the", "to", "with", "its", "their", "using", "via", "into", "between",
}


# --------------------------------------------------------------------------
# the library we are matching against
# --------------------------------------------------------------------------

@dataclass
class Entry:
    key: str
    title: str = ""
    surname: str = ""
    year: str = ""
    journal: str = ""
    volume: str = ""
    firstpage: str = ""
    doi: str = ""
    words: frozenset = frozenset()


def norm_doi(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", s)
    return s.rstrip(" .")


def deaccent(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm_title(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", deaccent(s or "").lower()).strip()


def sig_words(title: str) -> frozenset:
    return frozenset(w for w in norm_title(title).split() if len(w) > 3 and w not in STOP)


def read_library(out: Path) -> list[Entry]:
    """Parse library.bib into the fields worth matching on."""
    bib = out / "library.bib"
    if not bib.exists():
        sys.exit(f"No library at {bib} -- run the mirror first.")
    entries = []
    for chunk in re.split(r"\n@", bib.read_text(encoding="utf-8"))[1:]:
        m = re.match(r"\w+\{([^,]+),", chunk)
        if not m:
            continue
        e = Entry(key=m.group(1).strip())
        for fname, raw in re.findall(r"^\s*(\w+)\s*=\s*\{(.*?)\},?\s*$", chunk, re.M | re.S):
            val = raw.strip().strip("{}").strip()
            if fname == "title":
                e.title = val
            elif fname == "year":
                e.year = val
            elif fname in ("journal", "booktitle"):
                e.journal = val
            elif fname == "volume":
                e.volume = val
            elif fname == "pages":
                e.firstpage = re.split(r"[-–—]", val)[0].strip()
            elif fname == "doi":
                e.doi = norm_doi(val)
            elif fname == "author":
                first = val.split(" and ")[0]
                e.surname = deaccent(first.split(",")[0] if "," in first else first.split()[-1]).strip()
        e.words = sig_words(e.title)
        entries.append(e)
    return entries


# --------------------------------------------------------------------------
# references, from either source
# --------------------------------------------------------------------------

@dataclass
class Ref:
    label: str = ""
    doi: str = ""
    title: str = ""
    author: str = ""
    year: str = ""
    journal: str = ""
    raw: str = ""
    match: str = ""          # library key, or "" if not found
    how: str = ""            # which rule matched

    def describe(self) -> str:
        if self.title:
            bits = [b for b in (self.author, self.year) if b]
            head = f"{', '.join(bits)}. " if bits else ""
            return f"{head}{self.title}" + (f" ({self.journal})" if self.journal else "")
        return self.raw or f"{self.author} {self.year}".strip() or self.doi


def crossref_refs(doi: str) -> tuple[list[Ref], str]:
    """The publisher's deposited reference list, if there is one."""
    r = requests.get(CROSSREF + doi, headers={"User-Agent": UA}, timeout=60)
    if r.status_code == 404:
        return [], "crossref has no record of this DOI"
    r.raise_for_status()
    raw = r.json()["message"].get("reference") or []
    refs = []
    for i, c in enumerate(raw, 1):
        refs.append(Ref(
            label=str(i),
            doi=norm_doi(c.get("DOI", "")),
            title=(c.get("article-title") or c.get("volume-title") or "").strip(),
            author=(c.get("author") or "").strip(),
            year=(c.get("year") or "").strip(),
            journal=(c.get("journal-title") or "").strip(),
            raw=(c.get("unstructured") or "").strip(),
        ))
    return refs, ""


NUMBER_STYLES = [
    ("N.",  re.compile(r"^\s*(\d{1,3})\.\s+(\S.*)$")),
    ("[N]", re.compile(r"^\s*\[(\d{1,3})\]\s*(\S.*)$")),
    ("(N)", re.compile(r"^\s*\((\d{1,3})\)\s*(\S.*)$")),
    ("N",   re.compile(r"^\s*(\d{1,3})\s+([A-Z]\S.*)$")),
]
HEADING = re.compile(
    r"^\s*#*\s*(?:\d+\.?\s*)?(references?(?:\s+and\s+notes)?|bibliography"
    r"|literature\s+cited|works\s+cited)\s*:?\s*$", re.I)
# The same heading when extraction GLUED the first entry to it. `clean_page_text`
# turns a single newline inside a paragraph into a space, so "REFERENCES" on its
# own line becomes "REFERENCES Alper, H. E., and T. R. Stouch. 1995. ..." and the
# anchored pattern above never fires. Measured 2026-10-16 on the live library:
# `Belohorcova1997Structure` found ZERO headings and fell back to a block scan
# that recovered 13 of its 49 entries, starting at Brooks rather than Alper.
HEADING_GLUED = re.compile(
    r"^\s*#*\s*(?:\d+\.?\s*)?(references?(?:\s+and\s+notes)?|bibliography"
    r"|literature\s+cited|works\s+cited)\b[\s:.]+(?=\S)", re.I)


def reference_head(lines: list[str], floor: int) -> int | None:
    """The last line that opens the reference section, or None.

    Anchored heading first, then the glued form. Taken from the LAST match
    rather than the first, because a paper's own text says "references" in
    prose and its running head may repeat it.
    """
    for pat in (HEADING, HEADING_GLUED):
        hits = [i for i, l in enumerate(lines) if i >= floor and pat.match(l)]
        if hits:
            return hits[-1]
    return None
AUTHOR_YEAR = re.compile(r"^[A-Z][\w'’\-]+(?:,| et al\.| and )")


def span(nums: list[int]) -> str:
    """Render [1,2,3,7] as "1-3, 7"."""
    out, start, prev = [], nums[0], nums[0]
    for n in nums[1:] + [None]:
        if n == prev + 1:
            prev = n
            continue
        out.append(str(start) if start == prev else f"{start}-{prev}")
        if n is None:
            break
        start = prev = n
    return ", ".join(out[:8]) + (", ..." if len(out) > 8 else "")


def body_lines(md: Path) -> list[str]:
    text = md.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S)
    text = re.sub(r"<!-- p\. \d+ -->", "", text)
    return text.splitlines()


def longest_run(hits: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Longest ascending chain of (line, number), skipping intervening noise.

    Not a contiguous run: a stray number from a wrapped line sits in the middle
    of a real reference list, and a run-based scan would split the list in two
    there. A longest-increasing-subsequence walks straight past it. Increments
    are capped so that section numbers and years cannot chain together.
    """
    if not hits:
        return []
    best_len = [1] * len(hits)
    prev = [-1] * len(hits)
    for i in range(len(hits)):
        for j in range(i):
            if 0 < hits[i][1] - hits[j][1] <= 5 and best_len[j] + 1 > best_len[i]:
                best_len[i], prev[i] = best_len[j] + 1, j
    i = max(range(len(hits)), key=lambda k: best_len[k])
    chain = []
    while i != -1:
        chain.append(hits[i])
        i = prev[i]
    return chain[::-1]


def text_refs(out: Path, key: str) -> tuple[list[Ref], str]:
    """Parse the reference list out of the extracted text."""
    md = out / "text" / f"{key}.md"
    if not md.exists():
        return [], f"no extracted text at text/{key}.md (see extraction-report.md)"
    lines = body_lines(md)
    floor = int(len(lines) * 0.35)          # references never start in the first third

    best_style, best_run = "", []
    for name, pat in NUMBER_STYLES:
        hits = [(i, int(pat.match(l).group(1)))
                for i, l in enumerate(lines) if i >= floor and pat.match(l)]
        run = longest_run(hits)
        if len(run) > len(best_run):
            best_style, best_run = name, run

    if len(best_run) >= 5:
        pat = dict(NUMBER_STYLES)[best_style]
        starts = [i for i, _ in best_run]
        refs, empty = [], []
        for n, start in enumerate(starts):
            stop = starts[n + 1] if n + 1 < len(starts) else len(lines)
            m = pat.match(lines[start])
            body = " ".join((m.group(2) + " " + " ".join(lines[start + 1:stop])).split())
            if len(body) < 12:
                # A bare number with no text: the two-column reference list came
                # apart in extraction, numbers in one column and text in another.
                empty.append(m.group(1))
                continue
            refs.append(Ref(label=m.group(1), raw=body))
        note = f'numbered "{best_style}" style'
        got = {int(r.label) for r in refs}
        if got:
            missing = sorted(set(range(1, max(got) + 1)) - got)
            if missing:
                note += f"; {len(missing)} entries did not survive extraction ({span(missing)})"
        return refs, note

    # No numbering. Look for a dense block of lines that each open like an
    # author-year entry -- "Surname, A.B., ... 1998." A heading is a bonus, not
    # a requirement: plenty of extractions lose it.
    head = reference_head(lines, floor)
    if head is not None:
        # The glued heading is carrying the FIRST entry on its own line, so
        # leaving it in place drops that entry -- and a dropped first entry is
        # invisible once the file is written, which is exactly what makes a
        # parsed list weaker evidence than a deposit. Strip the heading and the
        # entry behind it becomes an ordinary opener.
        bare = HEADING_GLUED.sub("", lines[head])
        if bare != lines[head] and bare.strip():
            lines = lines[:head] + [bare] + lines[head + 1:]
    opens = [i for i, l in enumerate(lines)
             if i >= (head if head is not None else floor)
             and AUTHOR_YEAR.match(l.strip()) and re.search(r"\b(1[89]\d{2}|20\d{2})\b", l)]
    if head is not None:
        # A heading is hard evidence of where the list starts, so everything
        # after it that opens like an entry belongs to it. The block scan below
        # exists for extractions that lost the heading, and its 3-line gap
        # tolerance silently truncates a list whose entries wrap -- which is
        # how 49 entries became 13.
        best = opens
    else:
        block: list[int] = []
        best = []
        for i in opens:
            if block and i - block[-1] <= 3:
                block.append(i)
            else:
                block = [i]
            if len(block) > len(best):
                best = list(block)
    if len(best) >= 8:
        refs = []
        for n, start in enumerate(best):
            stop = best[n + 1] if n + 1 < len(best) else len(lines)
            body = " ".join(" ".join(lines[start:stop]).split())
            refs.append(Ref(label=str(n + 1), raw=body))
        how = "after its REFERENCES heading" if head is not None else "by a block scan, no heading found"
        return refs, (f"author-year list, one entry per line, {how} (weaker parse; "
                      "entries are not numbered in the paper)")

    return [], ("could not find a reference list in the extracted text -- a reflowed "
                "two-column list often comes out as one unsplittable blob")


# --------------------------------------------------------------------------
# matching references to the library
# --------------------------------------------------------------------------

def match_refs(refs: list[Ref], lib: list[Entry]) -> None:
    by_doi = {e.doi: e for e in lib if e.doi}
    by_surname: dict[str, list[Entry]] = {}
    for e in lib:
        if e.surname:
            by_surname.setdefault(e.surname.lower(), []).append(e)

    for ref in refs:
        if ref.doi and ref.doi in by_doi:
            ref.match, ref.how = by_doi[ref.doi].key, "doi"
            continue

        if ref.title:
            nt = norm_title(ref.title)
            pool = [e for e in lib if not ref.year or not e.year or abs_year(e.year, ref.year) <= 1]
            best, score = None, 0.0
            for e in pool:
                if not e.title:
                    continue
                s = SequenceMatcher(None, nt, norm_title(e.title)).ratio()
                if s > score:
                    best, score = e, s
            if best and score >= 0.90:
                ref.match, ref.how = best.key, "title"
            # A reference that came with a title has already had its best shot.
            # Falling through to surname+year here would turn "same author, same
            # year, different paper" into a false match, which is worse than a
            # blank.
            continue

        raw = " ".join(ref.raw.split()) or ref.describe()
        if not raw:
            continue
        low = deaccent(raw).lower()
        years = set(re.findall(r"\b(1[89]\d{2}|20\d{2})\b", raw))
        rank = {"title-in-raw": 2, "author-year+": 1}
        scored: list[tuple[int, Entry, str]] = []
        rawwords = sig_words(raw)
        for surname, cands in by_surname.items():
            if len(surname) < 3 or not re.search(rf"\b{re.escape(surname)}\b", low):
                continue
            for e in cands:
                if e.year and (not years or e.year not in years):
                    continue
                overlap = len(e.words & rawwords) / len(e.words) if e.words else 0
                if overlap >= 0.6:
                    how = "title-in-raw"
                elif (e.volume and e.firstpage
                      and re.search(rf"\b{re.escape(e.volume)}\b", raw)
                      and re.search(rf"\b{re.escape(e.firstpage)}\b", raw)):
                    how = "author-year+"
                else:
                    # Same surname and year is not evidence of the same paper --
                    # prolific authors publish several times a year. Without the
                    # title or both volume and page to corroborate it, leave the
                    # reference unmatched rather than assert a wrong key.
                    continue
                scored.append((rank[how], e, how))
        if not scored:
            continue
        top = max(r for r, _, _ in scored)
        winners = [(e, how) for r, e, how in scored if r == top]
        if len(winners) > 1:
            continue
        ref.match, ref.how = winners[0][0].key, winners[0][1]


def abs_year(a: str, b: str) -> int:
    try:
        return abs(int(a) - int(b))
    except ValueError:
        return 99


# --------------------------------------------------------------------------

def search_index(out: Path, needle: str) -> list[tuple[str, str]]:
    index = out / "index.md"
    if not index.exists():
        sys.exit(f"No index at {index} -- run the mirror first.")
    hits = []
    for line in index.read_text(encoding="utf-8").splitlines():
        if line.startswith("| `") and needle.lower() in line.lower():
            cells = [c.strip() for c in line.strip("|").split("|")]
            hits.append((cells[0].strip("`"), " · ".join(cells[1:4])))
    return hits


def report(key: str, entry: Entry | None, refs: list[Ref], source: str,
           note: str, args, by_key: dict[str, Entry]) -> None:
    title = entry.title if entry else "?"
    print(f"\n{key} -- {title}" + (f" ({entry.year})" if entry and entry.year else ""))
    if not refs:
        print(f"  no references recovered: {note}")
        return
    have = [r for r in refs if r.match]
    print(f"  {len(refs)} references from {source}" + (f" [{note}]" if note else ""))
    print(f"  in the library: {len(have)}    not found: {len(refs) - len(have)}\n")

    shown = refs
    if args.have:
        shown = have
    elif args.missing:
        shown = [r for r in refs if not r.match]
    width = max((len(r.label) for r in shown), default=2)
    for r in shown:
        mark = f"{r.match} ({r.how})" if r.match else "-"
        # A crossref entry can be nothing but a DOI. Once it is matched, the
        # library's own title is the most useful thing to print for it.
        desc = r.describe()
        if r.match and (not desc or desc == r.doi):
            desc = by_key[r.match].title
        desc = re.sub(r"\s+", " ", desc)[:100]
        print(f"  {r.label:>{width}}  {mark:<34}  {desc}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="List the works a mirrored paper cites, and which are in the library.")
    ap.add_argument("keys", nargs="*", help="citation keys, e.g. FANG1995Polycyanate")
    ap.add_argument("--search", metavar="TEXT", help="find citation keys by title/author/DOI")
    ap.add_argument("--source", choices=("both", "crossref", "text"), default="both",
                    help="where to get the reference list (default: crossref, then text)")
    ap.add_argument("--have", action="store_true", help="show only references already in the library")
    ap.add_argument("--missing", action="store_true", help="show only references not in the library")
    ap.add_argument("--json", action="store_true", dest="as_json", help="machine-readable output")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="mirror directory")
    args = ap.parse_args()

    out = args.out.expanduser()
    if args.search:
        hits = search_index(out, args.search)
        if not hits:
            print(f"nothing matching {args.search!r}")
            return 1
        for key, rest in hits[:40]:
            print(f"{key}\t{rest}")
        if len(hits) > 40:
            print(f"... and {len(hits) - 40} more")
        return 0

    if not args.keys:
        ap.error("give at least one citation key, or --search TEXT")

    lib = read_library(out)
    by_key = {e.key: e for e in lib}
    payload, status = [], 0

    for key in args.keys:
        entry = by_key.get(key)
        if not entry:
            near = [k for k in by_key if k.lower().startswith(key.lower()[:8])][:5]
            print(f"! unknown citation key {key!r}" + (f" -- did you mean {near}?" if near else ""))
            status = 1
            continue

        refs, source, note = [], "", ""
        if args.source in ("both", "crossref") and entry.doi:
            try:
                refs, note = crossref_refs(entry.doi)
            except requests.RequestException as exc:
                note = f"crossref lookup failed: {exc}"
            if refs:
                source = "crossref"
            elif args.source == "crossref":
                note = note or "crossref has no deposited reference list for this DOI"
        elif args.source == "crossref":
            note = "no DOI in the library entry, so crossref cannot be asked"

        if not refs and args.source in ("both", "text"):
            refs, note = text_refs(out, key)
            if refs:
                source = "extracted text"

        match_refs(refs, lib)
        if args.as_json:
            payload.append({"key": key, "source": source, "note": note,
                            "references": [asdict(r) for r in refs]})
        else:
            report(key, entry, refs, source, note, args, by_key)

    if args.as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return status


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
