#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]
# ///
"""
citemap.py -- write, for each paper, which of its claims are citation-supported
and what supports them.

    uv run --script citemap.py                      # dry run over the library
    uv run --script citemap.py --only Jo2007Automated
    uv run --script citemap.py --write              # actually write citations/

An extract preserves what a paper SAYS. It does not say which sentences are the
authors' own result and which restate someone else's, and a quotation pulled out
of the second kind reads exactly like the first. `findings/` has that hazard
built in: a record locates a verified quote, and if that quote were a restatement
the record would look identical.

This writes `citations/<citekey>.md`: the paper's reference list, resolved to our
own citation keys where we hold the work, plus **which markers appear on which
page** so a claim on p. 7 can be traced to what supports it.

## Where the reference list comes from, and why not from the extract

Not from `text/<key>.md`. Locating the reference section inside an extract is the
hardest part of this problem, not the easiest: measured 2026-10-15 over the live
library, a detected run of numbered entries was really a reference list only
about a third of the time -- the rest were section numbering, table rows and
figure captions. Old text layers make it worse by swapping digits and letters
("F10ry" for Flory, reference 7 rendered "1").

`refs.py` already fetches the publisher's **deposited** reference list from
Crossref and already matches each entry against `library.bib` with rules strict
enough to prefer a false negative to a wrong key. That work is reused here
wholesale rather than reimplemented. 98% of a 40-DOI sample had a deposited list,
90% of those entries carried a DOI of their own, and 14% resolved to a record the
library already holds.

## The one thing that had to be measured: does marker N mean reference N?

It is an assumption, it is load-bearing, and it is false often enough to matter.
Checked over 400 DOIs against each extract's **own** numbered entries -- evidence
Crossref never saw -- with three controls so the test could fail:

| mapping | mean agreement |
|---|---|
| marker N -> reference N | **0.858** |
| control: N -> reference N+1 | 0.433 |
| control: N -> reference N-1 | 0.115 |
| control: N -> a random reference | 0.071 |

Of 78 papers that produced any signal, it held at >=0.90 for 62 (79.5%) and
failed for 10. `Letcher1972Thermodynamicsa` scored 0.20 against 1.00 for the
shifted mapping -- a clean off-by-one, where the deposited list carries an entry
the printed one does not.

So **every paper is verified individually before anything is written**, by that
same test, and a paper that does not pass gets no file. The 117 papers that
produced no signal in any arm are not written either: no evidence is not
evidence. Right when it speaks, silent otherwise.

## What it refuses

- **No DOI, or no deposited reference list** -- nothing to be right about.
- **Verification not confirmed** -- including the off-by-one, which is named in
  the report rather than quietly treated as a failure, because it is a different
  thing and a human may want to look.
- **A marker numbered above the deposited list** -- dropped and counted, never
  mapped to the nearest entry.
- It **never writes into `text/`**. The extract is the durable artifact and stays
  byte-identical; everything here is derived and lives beside it, the way
  `annotations/` and `findings/` do. A derivation written into the verbatim body
  would be indistinguishable later from what the paper actually said -- and it
  would break the 156 stored quotes in `findings/`, which verify by substring.

Read-only with respect to Zotero, and dry-run by default. Crossref is public and
unauthenticated; responses are cached in `~/.cache/offprint/`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mendeley_mirror import DEFAULT_OUT, __version__, save_json  # noqa: E402
from refs import Ref, crossref_refs, match_refs, read_library  # noqa: E402

PAGE_MARK = re.compile(r"<!-- p\. (\d+) -->")

# In-text markers, one pattern per style. The superscript pattern requires the
# digits to follow a word AND its punctuation, because a bare "word12" also
# describes "nm3" and "MgCl2"; the looser form is used only once a paper is
# already known to be superscript-style, where its false positives are bounded
# by the deposited list's length.
BRACKET = re.compile(r"\[(\d{1,3}(?:\s*[,;–-]\s*\d{1,3})*)\]")
PAREN = re.compile(r"\((\d{1,3}(?:\s*[,;–-]\s*\d{1,3})*)\)")
SUPER = re.compile(r"[a-zÀ-ɏ]{3,}[.,;:)]\s?"
                   r"(\d{1,3}(?:[,;–-]\d{1,3})*)(?=[\s–]|$)")
SUPER_LOOSE = re.compile(r"[a-zÀ-ɏ]{4,}"
                         r"(\d{1,3}(?:[,;–-]\d{1,3})*)(?=[\s.,;:)]|$)")
# Words that take a trailing number in ordinary scientific prose. Without this
# the loose superscript pattern reads "Figure 3" and "50 nm3" as citations.
NOT_A_MARKER = re.compile(
    r"(?:nm|cm|mm|km|mol|kj|kcal|ev|hz|ps|ns|fs|ms|kda|da|mpa|gpa|fig|figure|"
    r"table|eq|equation|ref|chapter|section|no|vol|type|group|level|phase|mode|"
    r"state|form|step|case|class|model|run|set|site|sample|series|region|zone|"
    r"layer|stage|part|line|row|column|point|page|chain|atom|residue|loop)$")

STYLES = {"bracket": BRACKET, "paren": PAREN, "super": SUPER}

# "(Rizzuto et al., 1998)". A paper citing this way has no marker N, so
# "marker N means reference N" is not a claim that can hold or fail -- it does
# not apply, and saying `failed` about it asserts something untrue.
AUTHOR_YEAR = re.compile(
    r"\([^()]{0,80}?[A-Za-z]{3,}[^()]{0,40}?(?:19|20)\d{2}[a-z]?\)")


def cites_by_name(prose: str) -> bool:
    """Does this paper cite by author and year rather than by number?

    Decided from the extract BEFORE any scoring, because the scorer cannot
    tell "the mapping is wrong" from "there is no mapping". `library` found
    three Cell Press papers of one citation style drawing three different
    verdicts on 2026-10-15 -- two `failed`, one `no-signal` -- none of which
    was true of any of them.
    """
    named = len(AUTHOR_YEAR.findall(prose))
    numeric = max(len(rx.findall(prose)) for rx in STYLES.values())
    return named >= 8 and named > numeric

CACHE_VERSION = 1
MIN_TESTED = 5            # fewer comparisons than this cannot distinguish anything
CONFIRM_AT = 0.80         # agreement required of the true mapping
OVER_RANDOM = 0.30        # ... and by this much over matching a random entry
OFFSET_AT = 0.30          # shifted mapping beating true by this much is an offset

# `CONFIRM_AT` is deliberately conservative and its cost is known. Over the
# first 500 records, 7 of the 44 rejected for "failed" scored >= 0.50 with both
# controls below a quarter of that -- `Debnath1999Structure` at 0.74 against
# 0.11 and 0.06 -- which is a mapping that is almost certainly right and merely
# verified in part, the unverified entries being accented or garbled names the
# window never matched. That is 1.4% of the corpus withheld for strictness.
#
# It is left strict on purpose. A withheld file costs a convenience; a wrong
# one tells the reader that a claim rests on a paper it does not, inside a tool
# built to stop exactly that. The alternative rule -- confirm on the CONTRAST,
# `true >= 0.50 and true > 4 * max(up, rand)` -- is written down here rather
# than adopted, because 500 records is not enough to choose a threshold on and
# the full corpus run will say more.


def cache_path() -> Path:
    return Path.home() / ".cache" / "offprint" / "references.json"


def load_cache() -> dict:
    try:
        blob = json.loads(cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return blob.get("refs", {}) if blob.get("version") == CACHE_VERSION else {}


def save_cache(cache: dict) -> None:
    """Written through `save_json`, which replaces the file atomically.

    A plain `write_text` truncates in place, so a second run writing the cache
    while the first is mid-write leaves JSON that parses as nothing -- and
    `load_cache` swallows the ValueError and returns an empty dict, so the
    whole cache silently evaporates and every DOI is fetched again. That is
    not hypothetical: `library` started two passes eight seconds apart on
    2026-10-15 and both were saving here every 50 records.

    Two runs can still lose each other's ENTRIES, last writer winning. That is
    left alone, because the cost is re-fetching a DOI, not losing one -- a
    recoverable waste rather than a wrong answer.
    """
    save_json(cache_path(), {"version": CACHE_VERSION, "refs": cache})


def cached_refs(doi: str, cache: dict, pause: float = 0.25) -> tuple[list, str]:
    """`refs.py`'s Crossref fetch, remembered between runs.

    A corpus pass asks about 2,300 DOIs and a re-run should ask about none of
    them: this is a read-only audit and it has no business hammering a public
    endpoint to re-derive what it already knows.
    """
    if doi in cache:
        hit = cache[doi]
        return [Ref(**d) for d in hit["refs"]], hit["note"]
    refs, note = crossref_refs(doi)
    cache[doi] = {"refs": [asdict(r) for r in refs], "note": note}
    time.sleep(pause)
    return refs, note


def split_pages(body: str) -> list[tuple[int, str, int]]:
    """The extract's text page by page, with each page's offset in the body.

    The offset is what lets a bibliography be cut out of the page map: a page
    is not wholly text or wholly references, and a reference list can start
    halfway down one.
    """
    marks = [(m.start(), m.end(), int(m.group(1)))
             for m in PAGE_MARK.finditer(body)]
    out = []
    for i, (_s, e, n) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(body)
        out.append((n, body[e:end], e))
    return out


def expand(group: str) -> list[int]:
    """"1-4" cites 1,2,3,4; "5,7" cites 5 and 7.

    A range is expanded only when it is small and ascending. "1-150" is a page
    range or a residue span, not a citation of 150 works.
    """
    out: list[int] = []
    for part in re.split(r"[,;]", group):
        part = part.strip()
        m = re.fullmatch(r"(\d{1,3})\s*[–-]\s*(\d{1,3})", part)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            if 0 < hi - lo <= 30:
                out.extend(range(lo, hi + 1))
            else:
                out.extend([lo, hi])
        elif part.isdigit():
            out.append(int(part))
    return out


def detect_style(prose: str) -> str:
    """Which marker style this paper uses, by the STRICT patterns only.

    Selection must not use the loose superscript pattern: it matches a word
    followed by digits anywhere, so it wins the vote on papers that plainly use
    brackets. Measured 2026-10-15 -- letting it select dropped bracket-style
    papers from 514 to 309 and took median precision from 1.00 to 0.79.
    """
    counts = {name: len(rx.findall(prose)) for name, rx in STYLES.items()}
    return max(counts, key=lambda k: counts[k])


def markers_in(text: str, style: str) -> list[int]:
    return [n for _pos, n in markers_with_pos(text, style)]


def markers_with_pos(text: str, style: str) -> list[tuple[int, int]]:
    """`(offset, number)` for each marker, because WHERE one sits decides
    whether it is a citation or the bibliography numbering itself."""
    found: list[tuple[int, int]] = []
    for m in STYLES[style].finditer(text):
        found.extend((m.start(), n) for n in expand(m.group(1)))
    if style == "super":
        for m in SUPER_LOOSE.finditer(text):
            word = text[max(0, m.start()):m.start(1)].rstrip(".,;:) ").lower()
            if NOT_A_MARKER.search(word):
                continue
            found.extend((m.start(), n) for n in expand(m.group(1)))
    return found


NAME_WORD = re.compile(r"[A-Z][A-Za-z'\u2019-]*[a-z]{3}[A-Za-z'\u2019-]*")


def surname(author: str) -> str:
    """A name word out of Crossref's `author`, which has no fixed shape.

    Three shapes turned up in one day, each costing a measurement before it was
    found: "Brader M. L." (trailing initials) where the extract reads
    "Brader, M. L."; "Blundell" bare; and Nature's "CD Rizzuto", with the
    initials in FRONT. The first version stripped trailing initials only, so
    every Nature deposit scored zero against entries that were sitting right
    there -- `Shaik2019Structural`, 78 well-formed entries, reported no-signal.

    Stripping initials by position is what kept being wrong, because position
    is the thing that varies. So this does not strip at all: it picks the
    longest token that LOOKS like a name word -- a capital followed by real
    lowercase letters -- which is shape-independent and gets all three right.
    "van der Waals" yields "Waals" rather than the whole name, and that is
    fine: this feeds a whole-word search, not a bibliography.
    """
    words = NAME_WORD.findall(author or "")
    return max(words, key=len) if words else ""


def names_match(sur: str, window: str) -> bool:
    """Whole-word, so "Li" does not match "Ligand" or "Lithium"."""
    return bool(re.search(rf"\b{re.escape(sur)}\b", window, re.I))


NEXT_ENTRY = re.compile(r"(?:^|[\s\[(])\(?\[?\d{1,3}[.)\]]")


def entry_windows(body: str, n: int, width: int = 150) -> list[tuple[int, str]]:
    """Text just after each place the EXTRACT numbers an entry `n`, and no
    further than where it numbers the next one.

    Deliberately searched over the whole extract rather than inside a detected
    reference section: finding that section is the unreliable step, so the
    verification must not depend on having found it.

    **The window stops at the next entry number**, which is what makes the
    shifted control mean anything. A fixed-width window over a terse reference
    list reaches into entry N+1, so the shifted mapping scores as well as the
    true one and a perfectly correct paper is rejected for failing to beat its
    own contamination. Bounding it here is better than loosening the threshold:
    the threshold would hide the contamination, this removes it.
    """
    out = []
    for m in re.finditer(rf"(?:^|[\s\[(])\(?\[?{n}[.)\]]", body):
        win = body[m.end():m.end() + width]
        nxt = NEXT_ENTRY.search(win, 10)
        out.append((m.start(), win[:nxt.start()] if nxt else win))
    return out


def verify_ordinal(body: str, refs: list) -> dict:
    """Does in-text marker N mean deposited reference N?

    The extract's own numbered entries are the evidence, and Crossref never saw
    them, so this is not the tool agreeing with its own input -- which is the
    trap two withdrawn page-offset rules fell into earlier this month.

    Three controls, because a test that cannot fail proves nothing: the mapping
    shifted down one, shifted up one, and a reference drawn at random. If the
    random arm scores as well as the true one, surnames are simply common in
    this extract and the comparison says nothing.
    """
    arms = {k: [0, 0] for k in ("true", "down", "up", "rand")}
    # Offsets where the TRUE mapping matched a surname. Each one is a reference
    # entry confirmed by evidence, which is the only trustworthy way this tool
    # has of knowing where the bibliography sits -- pattern-matching a
    # "References" heading finds a real list about a third of the time.
    entry_at: list[int] = []
    pool = [s for r in refs if len(s := surname(r.author)) >= 4]
    if len(pool) < 10:
        return {"verdict": "unverifiable",
                "why": f"only {len(pool)} deposited entries name a usable "
                       f"surname, "
                       "which is too few to test the mapping"}
    # Seeded from the text so a re-run reaches the same verdict. A verification
    # that wobbles between runs is worse than none: it would write a file one
    # day and withhold it the next with nothing having changed.
    rng = random.Random(len(body))
    for n in range(2, min(len(refs), 60) + 1):
        wins = entry_windows(body, n)
        if not wins:
            continue
        for name, idx in (("true", n - 1), ("down", n - 2), ("up", n)):
            if not 0 <= idx < len(refs):
                continue
            who = surname(refs[idx].author)
            if len(who) < 4:
                continue
            arms[name][1] += 1
            for pos, w in wins:
                if names_match(who, w):
                    arms[name][0] += 1
                    if name == "true":
                        entry_at.append(pos)
                    break
        arms["rand"][1] += 1
        decoy = rng.choice(pool)
        if any(names_match(decoy, w) for _pos, w in wins):
            arms["rand"][0] += 1

    # Entry 1 is located but never SCORED. The loop starts at 2 because "1."
    # is the commonest token in a paper -- page numbers, list items, equation
    # labels -- and scoring it would import that noise into the rates. But not
    # locating it leaves the bibliography's first entry on the prose side of
    # the cut, which is why `Demir2021Automated` still mapped "p. 8 -- [1]"
    # after the reference list was otherwise excluded. Same evidence standard,
    # no effect on the measurement.
    if entry_at and refs:
        first = surname(refs[0].author)
        if len(first) >= 4:
            for pos, w in entry_windows(body, 1):
                if pos < min(entry_at) and names_match(first, w):
                    entry_at.append(pos)

    rate = {k: (v[0] / v[1] if v[1] else 0.0) for k, v in arms.items()}
    tested = arms["true"][1]
    out = {"rates": rate, "tested": tested, "entry_at": sorted(entry_at)}
    if tested < MIN_TESTED:
        out.update(verdict="unverifiable",
                   why=f"only {tested} entries could be compared")
    elif max(rate.values()) <= 0.10:
        # Every arm silent, including the random one: the extract's numbered
        # entries were never found, so this is no evidence either way -- not
        # evidence against. It still gets no file.
        out.update(verdict="no-signal",
                   why="the extract's own reference entries could not be "
                       "located, so the mapping cannot be checked")
    elif rate["up"] > rate["true"] + OFFSET_AT:
        out.update(verdict="offset",
                   why=f"marker N matches reference N+1 ({rate['up']:.2f}) far "
                       f"better than reference N ({rate['true']:.2f}); the "
                       "deposited list looks to carry an entry the printed one "
                       "does not")
    elif (rate["true"] >= CONFIRM_AT and rate["true"] > rate["up"]
            and rate["true"] > rate["rand"] + OVER_RANDOM):
        out.update(verdict="confirmed",
                   why=f"{rate['true']:.2f} on {tested} entries, against "
                       f"{rate['up']:.2f} shifted and {rate['rand']:.2f} random")
    else:
        out.update(verdict="failed",
                   why=f"{rate['true']:.2f} on {tested} entries, against "
                       f"{rate['up']:.2f} shifted and {rate['rand']:.2f} random")
    return out


def render(key: str, doi: str, refs: list, by_page: list, check: dict,
           dropped: list, chars: int, sha: str = "") -> str:
    """`citations/<key>.md`.

    The header says what this file is NOT, for the same reason `findings/` does:
    it is the first thing a reader sees, and the failure worth preventing is
    someone quoting a derived file as though it were the paper.
    """
    held = [r for r in refs if r.match]
    rates = check.get("rates", {})
    lines = [
        "---",
        f"citekey: {key}",
        f"doi: {doi}",
        "source: crossref (publisher-deposited reference list)",
        f"references: {len(refs)}",
        f"in-library: {len(held)}",
        f"verified: {check['verdict']} -- {check['why']}",
        # Only where a score was actually taken. Printing "controls: 0.00"
        # for a paper nothing was scored on states a result that does not
        # exist, which is the failure this whole file is built to avoid.
        (f"controls: true {rates.get('true', 0):.2f} / shifted-up "
         f"{rates.get('up', 0):.2f} / shifted-down {rates.get('down', 0):.2f} "
         f"/ random {rates.get('rand', 0):.2f}" if rates
         else "controls: not scored -- no mapping was claimed"),
        f"extract-sha: {sha}",
        # Kept beside the hash because 1,048 files were written with only this
        # before the hash existed, and a reader comparing two files should not
        # have to know which version produced which.
        f"extract-characters: {chars}",
        f"written-by: offprint {__version__}",
        "---",
        "",
        f"# Citations: {key}",
        "",
        "*Derived, not extracted. The paper's own words are in "
        f"`text/{key}.md` and nothing here is a quotation. This file says which "
        "claims rest on a citation and what the citation is; it does not say "
        "what either paper found. Re-read the extract before quoting.*",
        "",
    ]
    if check["verdict"] != "not-applicable":
        # Untrue of an author-year file, which claims no mapping and so had
        # none to verify. A standing sentence that is false on some of the
        # files it is printed into is worse than no sentence.
        lines += [
            "*The marker-to-reference mapping was verified against this "
            "extract's own numbered entries before the file was written. A "
            "paper whose mapping could not be confirmed has no file at all, "
            "so the presence of this one is itself the check having passed.*",
            "",
        ]

    if check["verdict"] == "not-applicable":
        lines += [
            "*This paper cites by author and year, so there is no numbered "
            "marker to map and none is claimed. What follows is the "
            "publisher's deposited reference list resolved to our citation "
            "keys: a fact about the DOI and a match against `library.bib`, "
            "with nothing inferred and so nothing to verify. There is no "
            "page map here, which is a property of the paper and not a "
            "failure.*",
            "",
        ]

    if by_page:
        lines += ["## Markers by page", "",
                  "Where a citation-supported claim sits, and what supports it.",
                  ""]
        for page, nums in by_page:
            cites = ", ".join(
                f"[{n}](#ref-{n})" + (f" — **{refs[n-1].match}**"
                                      if refs[n-1].match else "")
                for n in nums)
            lines.append(f"- **p. {page}** — {cites}")
        lines.append("")

    if dropped:
        lines += [
            "## Markers that resolve to nothing", "",
            f"These numbers appear where a marker would, but the deposited list "
            f"holds only {len(refs)} entries, so they are reported rather than "
            "mapped to the nearest one: "
            + ", ".join(str(d) for d in sorted(set(dropped))[:40]) + ".",
            "",
            "Most are quantities the detector could not tell from a marker.",
            "",
        ]

    lines += ["## References", ""]
    for r in refs:
        n = r.label
        who = ", ".join(b for b in (r.author, r.year) if b)
        what = r.title or r.raw or "(entry carries no title)"
        bits = [f'<a id="ref-{n}"></a>**{n}.** ']
        bits.append(f"{who}. " if who else "")
        bits.append(what)
        if r.journal:
            bits.append(f" *{r.journal}*")
        if r.doi:
            bits.append(f" [{r.doi}](https://doi.org/{r.doi})")
        if r.match:
            bits.append(f" — **in the library as `{r.match}`** ({r.how})")
        lines.append("".join(bits))
        lines.append("")
    return "\n".join(lines) + "\n"


def extract_chars(body: str) -> int:
    m = re.search(r"^characters:\s*(\d+)", body, re.M)
    return int(m.group(1)) if m else 0


def extract_sha(raw: str) -> str:
    """A hash of the extract's BODY, which is what a page map depends on.

    `library` asked for this in place of the character count, and was right: a
    count collides. An OCR correction that swaps characters without changing
    the length moves the page markers and leaves the count identical, so the
    stale check would pass on a file whose page map had shifted -- a faithful
    index over changed source, invisible to cross-checking, which is the exact
    failure this is supposed to catch.

    The BODY and not the whole file: a frontmatter-only rewrite (a title
    corrected in Zotero, say) changes the file without moving a single page
    marker, and marking 1,048 files stale for that would train the reader to
    ignore the report.
    """
    body = raw.split("---", 2)[-1] if raw.startswith("---") else raw
    return hashlib.sha256(body.encode("utf-8", "replace")).hexdigest()[:16]


def stale_report(out: Path) -> tuple[list, list, list, int]:
    """Which `citations/` files no longer describe the extract beneath them.

    A refresh never deletes this directory -- it unlinks only its own targets
    and sweeps no directory it does not own -- so a citations file outlives the
    extract it was built from. What it does NOT outlive is correctness: a
    re-extraction (a changed attachment, `--ocr`, `--reassess`) rewrites
    `text/<key>.md` and can move the page markers, which is exactly what the
    "Markers by page" section points at.

    So each file records the character count of the extract it read, and this
    is what reads it back. Report only: regenerating is `--only`, and deleting
    someone's file is a different claim on different evidence.

    A `not-applicable` file carries no page map, so a changed extract leaves
    nothing in it wrong except the stamp. Reported apart for that reason --
    they do not need regenerating, and saying they do would send a reader
    chasing 215 files that are fine.
    """
    stale, orphan, cosmetic, ok = [], [], [], 0
    for path in sorted((out / "citations").glob("*.md")):
        head = path.read_text(encoding="utf-8", errors="replace")[:1200]
        key = re.search(r"^citekey:\s*(\S+)", head, re.M)
        sha = re.search(r"^extract-sha:\s*([0-9a-f]+)", head, re.M)
        was = re.search(r"^extract-characters:\s*(\d+)", head, re.M)
        verdict = re.search(r"^verified:\s*(\S+)", head, re.M)
        if not key:
            continue
        extract = out / "text" / f"{key.group(1)}.md"
        if not extract.exists():
            orphan.append(key.group(1))
            continue
        raw = extract.read_text(encoding="utf-8", errors="replace")
        # The hash when the file carries one; the character count when it was
        # written before the hash existed. 1,048 files were written that way on
        # 2026-10-15 and they stay checkable -- weakly, since a count collides,
        # but a weak check that names the file beats no check at all. Each
        # becomes hash-backed the first time it is regenerated.
        if sha:
            matched = sha.group(1) == extract_sha(raw)
        elif was:
            matched = int(was.group(1)) == extract_chars(raw)
        else:
            matched = False
        if matched:
            ok += 1
        elif verdict and verdict.group(1) == "not-applicable":
            cosmetic.append(key.group(1))
        else:
            stale.append(key.group(1))
    return stale, orphan, cosmetic, ok


def plan_one(out: Path, key: str, doi: str, lib: list, cache: dict) -> dict:
    """Everything decided for one paper, without writing anything."""
    path = out / "text" / f"{key}.md"
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"key": key, "status": "no-extract"}
    body = raw.split("---", 2)[-1] if raw.startswith("---") else raw
    if len(body) < 2000:
        return {"key": key, "status": "extract-too-short"}

    refs, note = cached_refs(doi, cache)
    if not refs:
        return {"key": key, "status": "no-deposited-list",
                "why": note or "crossref holds no reference list for this DOI"}

    prose = PAGE_MARK.sub(" ", body)
    if cites_by_name(prose):
        # Nothing is inferred for these: the deposited list is a fact about the
        # DOI and the citekeys come from `refs.py`'s matching. There is no
        # marker mapping, so there is nothing for a verification to check, and
        # the file says so rather than implying a map it does not carry.
        match_refs(refs, lib)
        check = {"verdict": "not-applicable", "rates": {},
                 "why": "cites by author and year, so there is no marker N to "
                        "map and none is claimed"}
        return {"key": key, "status": "not-applicable", "refs": refs,
                "check": check, "by_page": [], "dropped": [], "style": "name",
                "markers": 0, "held": sum(1 for r in refs if r.match),
                "chars": extract_chars(raw), "sha": extract_sha(raw),
                "doi": doi, "why": check["why"]}

    check = verify_ordinal(body, refs)
    if check["verdict"] != "confirmed":
        return {"key": key, "status": check["verdict"], "why": check["why"],
                "refs": len(refs), "check": check}

    match_refs(refs, lib)
    style = detect_style(prose)
    # Where the bibliography begins, page by page, taken from the entry
    # positions the verification CONFIRMED rather than from a heading pattern.
    # `library` found on 2026-10-15 that the map was reading a reference
    # list's own numbering back as citations -- `Demir2021Automated` mapped
    # "p. 8 -- [1]...[54]" where page 8 is its reference list, and 18% of all
    # mapped markers sat on such pages. A bibliography page carries no claims,
    # so listing it says a paper's last pages are its most densely cited,
    # which is backwards.
    #
    # Per page and not one global cut: Nature puts Methods AFTER the
    # references, so `Shaik2019Structural` has entries on extract pages 5, 6
    # and 8 with prose on 7. A single cut point would throw page 7 away.
    entry_at = check.get("entry_at", [])
    by_page, dropped, seen, hidden = [], [], 0, 0
    for page, text, start in split_pages(body):
        here = [o - start for o in entry_at if start <= o < start + len(text)]
        cut = min(here) if here else len(text)
        nums, late = set(), 0
        for pos, n in markers_with_pos(text, style):
            if pos >= cut:
                late += 1
                continue
            nums.add(n)
        hidden += late
        nums = sorted(nums)
        good = [n for n in nums if 1 <= n <= len(refs)]
        dropped.extend(n for n in nums if n > len(refs))
        if good:
            by_page.append((page, good))
            seen += len(good)
    return {"key": key, "status": "confirmed", "refs": refs, "check": check,
            "by_page": by_page, "dropped": dropped, "style": style,
            "markers": seen, "held": sum(1 for r in refs if r.match),
            "bib_skipped": hidden,
            "chars": extract_chars(raw), "sha": extract_sha(raw), "doi": doi}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="write which claims are citation-supported, and by what")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT,
                    help="mirror directory")
    ap.add_argument("--only", nargs="+", metavar="CITEKEY",
                    help="just these records")
    ap.add_argument("--write", action="store_true",
                    help="write citations/; without it this is a dry run")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after this many records (for a first look)")
    ap.add_argument("--refresh-stale", action="store_true",
                    help="rewrite only the files whose extract moved under "
                         "them. Dry like everything else: add --write to act")
    ap.add_argument("--stale", action="store_true",
                    help="report which citations/ files no longer match the "
                         "extract beneath them, and stop")
    ap.add_argument("--report", type=Path, metavar="PATH",
                    help="write a TSV naming EVERY record and its verdict. "
                         "Without it the console names only the refusals that "
                         "might be mistakes, so most of a pass cannot be "
                         "audited without running it again")
    ap.add_argument("--version", action="version",
                    version=f"offprint {__version__}")
    args = ap.parse_args()

    if args.stale:
        stale, orphan, cosmetic, ok = stale_report(args.out)
        print(f"offprint {__version__} citemap --stale\n")
        print(f"  {ok} file(s) still describe their extract")
        if stale:
            print(f"\n  {len(stale)} with a page map built on a DIFFERENT "
                  f"extract -- the markers may point at the wrong pages:")
            for k in stale:
                print(f"    {k}")
            print("\n  Regenerate with:\n    uv run --script citemap.py "
                  "--write --only " + " ".join(stale[:8])
                  + (" ..." if len(stale) > 8 else ""))
        if cosmetic:
            print(f"\n  {len(cosmetic)} author-year file(s) whose extract "
                  "changed. These carry no page map, so nothing in them is "
                  "wrong; only the recorded character count is out of date.")
        if orphan:
            print(f"\n  {len(orphan)} file(s) whose extract is gone: "
                  + ", ".join(orphan))
            print("  Nothing here removes them -- that is a different claim.")
        if not (stale or orphan or cosmetic):
            print("\n  Nothing is stale.")
        return 0

    lib = read_library(args.out)
    if not lib:
        sys.exit(f"error: no library.bib under {args.out}")
    wanted = {e.key: e.doi for e in lib if e.doi}
    if args.refresh_stale:
        if args.only:
            ap.error("--refresh-stale chooses the records itself; "
                     "--only names them. Give one.")
        stale, orphan, cosmetic, ok = stale_report(args.out)
        print(f"offprint {__version__} citemap --refresh-stale: "
              f"{ok} file(s) still match their extract")
        if cosmetic:
            # Their extract moved, but they carry no page map, so nothing in
            # them is wrong -- only the recorded hash. Rewriting 215 files to
            # correct a stamp would churn a directory that now syncs to
            # Cameron's laptops, for no change a reader could use.
            print(f"  {len(cosmetic)} author-year file(s) also moved and are "
                  f"SKIPPED: no page map, so nothing in them is wrong. "
                  f"Refresh the stamp with --only if you want it.")
        if orphan:
            print(f"  {len(orphan)} file(s) whose extract is gone, left alone: "
                  + ", ".join(orphan[:6]) + ("..." if len(orphan) > 6 else ""))
        if not stale:
            print("\n  Nothing to rewrite.")
            return 0
        print(f"  {len(stale)} file(s) to rewrite\n")
        wanted = {k: wanted[k] for k in stale if k in wanted}
    if args.only:
        unknown = [k for k in args.only if k not in wanted]
        if unknown:
            sys.exit("error: no record with a DOI for: " + ", ".join(unknown))
        wanted = {k: wanted[k] for k in args.only}

    print(f"offprint {__version__} citemap: "
          f"{'writing' if args.write else 'DRY RUN -- nothing is written'}")
    print(f"  {len(wanted)} record(s) with a DOI, of {len(lib)} in library.bib\n")

    cache = load_cache()
    tally: dict[str, int] = {}
    rows: list[tuple] = []
    written = refs_seen = held_seen = bib_seen = 0
    try:
        for i, (key, doi) in enumerate(sorted(wanted.items())):
            if args.limit and i >= args.limit:
                break
            plan = plan_one(args.out, key, doi, lib, cache)
            tally[plan["status"]] = tally.get(plan["status"], 0) + 1
            got = plan.get("refs")
            rows.append((key, plan["status"],
                         len(got) if isinstance(got, list) else (got or 0),
                         plan.get("held", 0), plan.get("markers", 0),
                         plan.get("why", "")))
            # Saved as we go. A corpus pass is thousands of requests and the
            # one that gets interrupted should not throw away what it already
            # asked for -- which is exactly what happened while building this.
            if i and i % 50 == 0:
                save_cache(cache)
            if plan["status"] not in ("confirmed", "not-applicable"):
                if args.only or plan["status"] in ("offset", "failed"):
                    print(f"  - {key}: {plan['status']}"
                          + (f" -- {plan['why']}" if plan.get("why") else ""),
                          flush=True)
                continue
            refs_seen += len(plan["refs"])
            held_seen += plan["held"]
            bib_seen += plan.get("bib_skipped", 0)
            text = render(key, doi, plan["refs"], plan["by_page"],
                          plan["check"], plan["dropped"], plan["chars"],
                          plan.get("sha", ""))
            print(f"  {'+' if args.write else 'would write'} citations/{key}.md"
                  f"  {len(plan['refs'])} refs, {plan['held']} in the library, "
                  f"{plan['markers']} markers on {len(plan['by_page'])} pages "
                  f"[{plan['check']['why']}]", flush=True)
            if args.write:
                dest = args.out / "citations" / f"{key}.md"
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(text, encoding="utf-8")
                written += 1
    finally:
        save_cache(cache)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(
                "citekey\tverdict\treferences\tin_library\tmarkers\twhy\n"
                + "".join("\t".join(str(c) for c in r) + "\n" for r in rows),
                encoding="utf-8")
            print(f"\nEvery record and verdict: {args.report}")

    WRITES = ("confirmed", "not-applicable")
    planned = sum(tally.get(s, 0) for s in WRITES)
    print(f"\n{'written' if args.write else 'would write'}: "
          f"{written if args.write else planned}"
          + (f"  ({tally.get('not-applicable', 0)} of them author-year, "
             "reference list only)" if tally.get("not-applicable") else ""))
    for status, n in sorted(tally.items(), key=lambda kv: -kv[1]):
        if status not in WRITES:
            print(f"  {status}: {n}")
    if planned:
        print(f"\n{refs_seen:,} references across them, {held_seen:,} already "
              f"records in this library.")
        if bib_seen:
            print(f"{bib_seen:,} number(s) left out of the page maps as "
                  f"bibliography rather than citation.")
    if not args.write:
        print("\nDry run. Nothing was written. A paper listed above as anything "
              "other than a write is one whose mapping could not be confirmed, "
              "and it gets no file rather than an unchecked one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
