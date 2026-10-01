#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]
# ///
"""Check every record's own DOI against Crossref, and say where they disagree.

Three records were found by hand on 2026-10-02 carrying **identifiers from one
paper and an author list headed by a different paper's first author**:
`Won2001Influence` on a Shan paper, `Daoulas2005Molecular` on a Harmandaris
paper, `Hirota2000Effect` on a Gong paper. Identifiers from A, authors from B,
three times, which is a pattern and not three coincidences. This looks for the
rest of the class.

Read-only and offline-safe in the way `pdbxref.py` is: it queries Crossref,
which is public and unauthenticated, and it **never writes to the library**. Its
cache lives in `~/.cache/offprint/`.

    uv run --script doixref.py                      # the whole library
    uv run --script doixref.py --key Won2001Influence
    uv run --script doixref.py --report audit.md

**The comparison is three-way, and that is the point.** The obvious probe —
Crossref's first author against the citation key's surname — flags every record
whose authors were ever legitimately corrected, because `assign_citekeys` freezes
a key at assignment and never renumbers it. `Abrams2013Enhanced` carries
`year = 2014` for the same reason. So this compares:

    citation key surname  ·  library.bib's current first author  ·  Crossref's

and reports on the **middle against the right**. A key that disagrees with a bib
author that agrees with Crossref is a frozen key doing its job, and is counted
separately rather than reported as a defect.

Two classes come out, and both are **candidates, not verdicts**:

- **author** — the DOI's title matches the record but its first author does not.
  That is the class the three found by hand belong to.
- **title** — the DOI resolves to a different paper altogether. Worse, and
  rarer. A DOI that resolves to an unrelated paper is how `Wu1982Potts` nearly
  acquired the wrong PDF.

Agreement is exact after folding diacritics; nothing here scores similarity.
`literature` tried similarity three times on this library and it failed three
times, and `zotero_migrate.py` settled 3 of 2,739 pairs that way. A surname that
agrees only on its last whitespace-separated token — `van der Waals` against
`Waals` — is listed under its own heading rather than quietly counted as
agreement, because a false agreement hides a defect and a false mismatch only
costs a reader a second.

Every record that could not be checked is counted and said out loud, in the same
breath as the score: a denominator that shrinks to fit its numerator reads as a
perfect result, and this repo has produced that twice.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mendeley_mirror import DEFAULT_OUT, __version__, ascii_fold  # noqa: E402
from zotero_migrate import parse_bib  # noqa: E402

CROSSREF = "https://api.crossref.org/works"
UA = f"offprint/{__version__} (https://github.com/cameronabrams/offprint; mailto:cfa22@drexel.edu)"
MAX_TRIES = 4


def cache_path() -> Path:
    d = Path.home() / ".cache" / "offprint"
    d.mkdir(parents=True, exist_ok=True)
    return d / "crossref.json"


# --------------------------------------------------------------------------
# comparison -- pure, and where a wrong answer would be confident
# --------------------------------------------------------------------------

def norm_surname(name: str) -> str:
    """A surname reduced to letters, for an EXACT comparison.

    `ascii_fold` normalises to NFKD and drops combining marks, so `Müller`
    becomes `Muller` rather than `Mller` -- deleting the letter instead of the
    accent is a real bug this repo has already shipped once, in the checker that
    verified the Zotero migration.
    """
    return re.sub(r"[^a-z]", "", ascii_fold(name or "").lower())


def bib_first_author(author_field: str) -> str:
    """The first author's surname as `library.bib` currently records it.

    `format_authors` writes `Surname, I. and Surname, I.`, so the first author
    is everything before the first comma of the first ` and `-separated chunk.
    A record whose author field is a single unstructured name still yields
    something usable, and `and others` is never first.
    """
    first = (author_field or "").split(" and ")[0].strip()
    if not first:
        return ""
    return first.split(",")[0].strip() if "," in first else first.strip()


def key_surname(citekey: str) -> str:
    """The surname `make_citekey` put at the front of the key.

    Keys are `<Surname><Year><Word>`, so the surname is the leading run before
    the first digit. Corporate acronyms stay upper-case in a key, which is why
    this does not assume a single capital.
    """
    m = re.match(r"^([A-Za-z]+?)(?=\d)", citekey or "")
    return m.group(1) if m else ""


def norm_title(s: str) -> str:
    s = ascii_fold(s or "").lower()
    s = re.sub(r"\\[a-z]+", " ", s)          # LaTeX commands
    return re.sub(r"[^a-z0-9]", "", s)


def compare(bib_author: str, cr_author: str) -> str:
    """`agree`, `particle`, `mismatch`, or `skip` when either side is unusable.

    `particle` is deliberately NOT agreement. Treating `van der Waals` and
    `Waals` as a match is almost always right and occasionally hides exactly
    what this is looking for, and the two errors do not cost the same: a false
    mismatch costs a reader one second, a false agreement costs a defect.
    """
    a, b = norm_surname(bib_author), norm_surname(cr_author)
    if not a or not b:
        return "skip"
    if a == b:
        return "agree"
    tail_a = norm_surname((bib_author or "").split()[-1]) if bib_author.split() else ""
    tail_b = norm_surname((cr_author or "").split()[-1]) if cr_author.split() else ""
    if tail_a and tail_a == tail_b:
        return "particle"
    return "mismatch"


# --------------------------------------------------------------------------

def crossref_work(session: requests.Session, doi: str, cache: dict) -> dict | None:
    """Trimmed Crossref metadata for one DOI, cached. None means not found."""
    if doi in cache:
        return cache[doi] or None
    for attempt in range(1, MAX_TRIES + 1):
        resp = session.get(f"{CROSSREF}/{doi}", timeout=45)
        if resp.status_code == 404:
            cache[doi] = None
            return None
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt == MAX_TRIES:
                raise RuntimeError(f"{doi}: HTTP {resp.status_code} after {attempt} tries")
            time.sleep(min(float(resp.headers.get("Retry-After") or 2 ** attempt), 60))
            continue
        resp.raise_for_status()
        m = resp.json()["message"]
        authors = m.get("author") or m.get("editor") or []
        cache[doi] = {
            "title": (m.get("title") or [""])[0],
            "first_author": (authors[0].get("family") or authors[0].get("name") or "")
                            if authors else "",
            "container": (m.get("container-title") or [""])[0],
            "volume": m.get("volume"), "issue": m.get("issue"), "page": m.get("page"),
        }
        return cache[doi]
    return None


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Compare each record's first author against Crossref, via its own DOI.")
    ap.add_argument("--version", action="version", version=f"offprint {__version__}")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT,
                    help=f"mirror directory (default: {DEFAULT_OUT})")
    ap.add_argument("--key", action="append", default=[],
                    help="limit to this citation key (repeatable)")
    ap.add_argument("--limit", type=int, default=0, help="stop after this many lookups")
    ap.add_argument("--report", type=Path, help="write the report here as well as to stdout")
    args = ap.parse_args()

    bib = args.out / "library.bib"
    if not bib.is_file():
        sys.exit(f"No library.bib at {bib}.")
    entries = parse_bib(bib.read_text(encoding="utf-8"))
    if args.key:
        wanted = set(args.key)
        entries = [e for e in entries if e[0] in wanted]
        missing = wanted - {e[0] for e in entries}
        if missing:
            sys.exit(f"not in library.bib: {', '.join(sorted(missing))}")

    cache = {}
    if cache_path().exists():
        try:
            cache = json.loads(cache_path().read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cache = {}
    session = requests.Session()
    session.headers.update({"User-Agent": UA})

    out: list = [f"# DOI cross-reference — offprint {__version__}", "",
                 f"{len(entries)} record(s) read from {bib}", ""]
    author_hits, title_hits, particles = [], [], []
    checked = agreed = 0
    no_doi = no_author = not_found = failed = 0
    frozen_key = 0

    for n, (citekey, _etype, fields) in enumerate(sorted(entries), 1):
        doi = (fields.get("doi") or "").strip()
        if not doi:
            no_doi += 1
            continue
        bib_author = bib_first_author(fields.get("author") or fields.get("editor") or "")
        if not bib_author:
            no_author += 1
            continue
        try:
            work = crossref_work(session, doi, cache)
        except Exception as exc:
            failed += 1
            out.append(f"- `{citekey}` lookup failed: {type(exc).__name__}: {exc}")
            continue
        if work is None:
            not_found += 1
            continue

        checked += 1
        verdict = compare(bib_author, work["first_author"])
        titles_agree = norm_title(fields.get("title", "")) == norm_title(work["title"])
        bib_title = (fields.get("title") or "").replace("{", "").replace("}", "")
        evidence = (f"`{citekey}` · doi {doi}\n"
                    f"    bib      {bib_author!r} — {bib_title[:70]}\n"
                    f"    crossref {work['first_author']!r} — {work['title'][:70]}\n"
                    f"    {work['container'][:40]} {work.get('volume')}"
                    f"({work.get('issue')}) {work.get('page')}")

        if not titles_agree:
            title_hits.append(evidence)
        elif verdict == "mismatch":
            author_hits.append(evidence)
        elif verdict == "particle":
            particles.append(evidence)
        elif verdict == "agree":
            agreed += 1
            # Expected and not a defect: the key was frozen before the author
            # field was corrected. Counted so it cannot be mistaken for one.
            if norm_surname(key_surname(citekey)) != norm_surname(bib_author):
                frozen_key += 1
        else:
            no_author += 1

        if n % 25 == 0:
            cache_path().write_text(json.dumps(cache), encoding="utf-8")
        if not (doi in cache and cache[doi] is not None and checked == 0):
            time.sleep(0.05)
        print(f"\r  {n}/{len(entries)}  {checked} checked", end="", file=sys.stderr, flush=True)
        if args.limit and checked >= args.limit:
            break

    cache_path().write_text(json.dumps(cache), encoding="utf-8")
    print(file=sys.stderr)

    out += ["## Counted, including what could not be checked", "",
            f"- checked against Crossref: {checked}",
            f"- first author agrees: {agreed}",
            f"- first author DIFFERS, title agrees: {len(author_hits)}",
            f"- DOI resolves to a different title: {len(title_hits)}",
            f"- agrees only on the last name part: {len(particles)}",
            "",
            f"- not checked, no DOI in the record: {no_doi}",
            f"- not checked, no author field: {no_author}",
            f"- not checked, DOI unknown to Crossref: {not_found}",
            f"- not checked, lookup failed: {failed}",
            "",
            f"- of the agreeing records, {frozen_key} have a citation key naming a "
            "different surname. That is a frozen key, not a defect: a key is "
            "assigned once and never renumbered.",
            ""]
    for label, rows, note in (
            ("DOI resolves to a different paper", title_hits,
             "The DOI is probably wrong. A DOI resolving to an unrelated paper is "
             "worse than no DOI."),
            ("First author differs", author_hits,
             "Candidates, not verdicts. This is the class the three records found "
             "by hand belong to: identifiers from one paper, authors from another."),
            ("Agrees only on the last name part", particles,
             "Usually a particle — `van der Waals` against `Waals`. Listed rather "
             "than counted as agreement, because a false agreement hides a defect.")):
        if not rows:
            continue
        out += [f"## {label} ({len(rows)})", "", note, ""]
        out += [f"- {r}" for r in rows] + [""]

    text = "\n".join(out)
    print(text)
    if args.report:
        args.report.write_text(text, encoding="utf-8")
        print(f"\nwritten to {args.report}", file=sys.stderr)


if __name__ == "__main__":
    main()
