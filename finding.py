#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]   # transitive: mendeley_mirror imports it for DEFAULT_OUT
# ///
"""
finding.py -- record what was asked of a paper and where the answer is.

The mirror preserves what papers SAY. This preserves what was asked of them and
where the answer sat, so a question answered once is not re-derived from nothing
six months later. Records live in `<out>/findings/<citekey>.md`.

    uv run --script finding.py --key Jo2007Automated \
        --question "does it optimize the protein's embedding?" \
        --page 2 --journal-page 2 --asked-by pestifer-manuscript \
        --quote "one should align it in a local machine and then upload it" \
        --used-for "Introduction, placement claim"

    uv run --script finding.py --key Jo2007Automated --check 1 --by cfa \
        --note "quote and locator confirmed against the PDF"

    uv run --script finding.py --key Jo2007Automated --retract 1 \
        --note "superseded: the sentence is conditional, see record 4"

    uv run --script finding.py --replay            # re-check every record, write nothing

**A record locates a passage; it never substitutes for one.** The moment one is
consulted instead of the extract it becomes a paraphrase indistinguishable from the
source, which is the whole thing deterministic extraction exists to prevent. Every
record carries a locator good enough to return to the passage in one step, and the
file header says this to whoever opens it.

**Files are append-only.** A finding later found wrong gets a RETRACTED record
pointing at it; the original stays. That is what makes retraction latency visible
rather than tidied away, and a checked record is likewise appended rather than
edited in, so it carries who checked and when.

**The quote is verified before the record is written.** It must actually appear in
`text/<citekey>.md` under the marker page claimed for it, after Unicode and
whitespace normalization. A quote that cannot be found is refused, not filed with a
warning -- a misattributed quote is the failure that reads as correct at the time,
so it is the one worth making structurally hard. If --journal-page is given, that
number must also appear on the marker page, which is the both-ends offset check.

**What gets stored is the EXTRACT's text, not the text submitted.** The check
normalizes both sides, so a quote can pass it and still differ -- an en dash
written as a hyphen, a ligature typed out. Until 0.50.0 the submitted bytes went
into the append-only record, which is a check that keeps records faithful
normalizing the difference away and then writing the unfaithful version down.

**A printed page is not always an integer**, and the part that is not a digit is
the part that makes it citable: PRX prints `021039-2`, a topical review prints
`R863`. `read_folio` is the one place that reads those forms, and the derivation,
the confirmer and the --journal-page check all go through it.

This script never touches Mendeley and never writes outside `<out>/findings/`.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import sys
import unicodedata
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).parent))

try:
    from mendeley_mirror import DEFAULT_OUT, __version__
except ImportError as exc:  # pragma: no cover
    sys.exit(f"finding.py must sit beside the other mirror scripts ({exc})")

HEADER = """# Findings: {key}

*Records **locate** a passage; they never substitute for one. Re-read
`text/{key}.md` before quoting anything below. Append-only: corrections are
appended as RETRACTED records, never edited in.*
"""


class Record(NamedTuple):
    """One block of a findings file, as `render_finding` writes it."""
    ordinal: int
    kind: str                 # FINDING | CHECKED | RETRACTED
    date: str
    by: str = ""              # CHECKED / RETRACTED: who
    ref: int = 0              # CHECKED / RETRACTED: the ordinal it is about
    note: str = ""            # CHECKED / RETRACTED: the note
    question: str = ""
    asked_by: str = ""
    locator: str = ""
    quote: str = ""
    used_for: str = ""


HEAD = re.compile(r"^## (\d+) \u00b7 (FINDING|CHECKED|RETRACTED) \u00b7 "
                  r"(\d{4}-\d\d-\d\d)(?: \u00b7 (.*))?$")
FIELD = re.compile(r"^(question|asked-by|locator|used-for): {2,}(.*)$")
REF = re.compile(r"^re: (\d+)(?: \u2014 (.*))?$")
MARKER = re.compile(r"^marker p\. (\d+)\b")
PRINTED = re.compile(r"\u00b7 printed (\S+) \(offset ")


def render_finding(ordinal: int, date: str, question: str, asked_by: str,
                   locator: str, quote: str, used_for: str) -> str:
    """The one place a FINDING record's text is built.

    `parse_findings` is its inverse and `test_mirror.py` round-trips the pair,
    which is the point of having it. `library` replayed the corpus with a
    hand-written regex parser of this format and said the quiet part first: when
    the format changes, a parser that lives elsewhere matches FEWER records and
    reports them as fine. That is the stale-record shape with this tool's own
    output as the stale file -- a faithful index over something only this repo
    owns. So the reader ships beside the writer, and anything the reader cannot
    read is reported by name rather than skipped.
    """
    lines = [f"## {ordinal} \u00b7 FINDING \u00b7 {date}", f"question:  {question}"]
    if asked_by:
        lines.append(f"asked-by:  {asked_by}")
    lines.append(f"locator:   {locator}")
    lines.append("")
    lines += ["> " + ln for ln in quote.strip().splitlines()]
    if used_for:
        lines += ["", f"used-for:  {used_for}"]
    return "\n".join(lines) + "\n"


def parse_findings(body: str) -> tuple[list[Record], list[str]]:
    """`(records, complaints)` for one findings file.

    **A block this cannot read is a complaint, never a silent skip.** A parser
    that drops what it does not recognize reports a corrupted file as a clean
    one, and the count of what it parsed is the only thing that would show it --
    which is the guard `library` described as weak, correctly.
    """
    recs: list[Record] = []
    bad: list[str] = []
    blocks = re.split(r"(?m)^(?=## )", body)
    for block in blocks:
        if not block.startswith("## "):
            continue
        head = HEAD.match(block.splitlines()[0])
        if not head:
            bad.append(f"unreadable header: {block.splitlines()[0][:70]!r}")
            continue
        ordinal, kind, date, by = int(head.group(1)), head.group(2), head.group(3), head.group(4) or ""
        rest = block.splitlines()[1:]
        while rest and not rest[-1].strip():
            rest.pop()
        if kind != "FINDING":
            ref = REF.match(rest[0]) if rest else None
            if not ref or len(rest) != 1:
                bad.append(f"#{ordinal} {kind}: body is not a single 're: N' line")
                continue
            recs.append(Record(ordinal, kind, date, by=by,
                               ref=int(ref.group(1)), note=ref.group(2) or ""))
            continue
        fields: dict[str, str] = {}
        quote: list[str] = []
        for ln in rest:
            if not ln.strip():
                continue
            f = FIELD.match(ln)
            if f:
                fields[f.group(1)] = f.group(2)
            elif ln.startswith(">"):
                quote.append(ln[2:] if ln.startswith("> ") else ln[1:])
            else:
                bad.append(f"#{ordinal} FINDING: line is neither a field nor a quote: {ln[:60]!r}")
                break
        else:
            missing = [k for k in ("question", "locator") if k not in fields]
            if missing or not quote:
                bad.append(f"#{ordinal} FINDING: no " + ", ".join(missing + ([] if quote else ["quote"])))
                continue
            recs.append(Record(ordinal, kind, date, question=fields["question"],
                               asked_by=fields.get("asked-by", ""),
                               locator=fields["locator"], quote="\n".join(quote),
                               used_for=fields.get("used-for", "")))
    return recs, bad


def die(msg: str):
    sys.exit(f"error: {msg}")


def normalize(s: str) -> str:
    """Compare quotes the way a reader would, not the way bytes do.

    Extracted text carries ligatures (con<fi>guration), typographic quotes and
    dashes, and line breaks wherever the PDF happened to wrap. None of that should
    decide whether a quote is really on the page.
    """
    s = unicodedata.normalize("NFKC", s)
    for a, b in (("‘", "'"), ("’", "'"), ("“", '"'), ("”", '"'),
                 ("–", "-"), ("—", "-"), ("−", "-"), ("­", "")):
        s = s.replace(a, b)
    # A bad glyph map drops control characters into the extract where a symbol
    # was: Kucerka 2005 carries "30\x01C" for "30 degrees C". Those must not
    # decide whether a quote is on the page, so they go the way of the
    # zero-width joiners -- same classes mendeley_mirror strips, plus Cc.
    # ...but newline and tab are Cc too, and they are word separators, not junk:
    # dropping them joins the words either side and defeats a real quote.
    s = "".join(ch for ch in s
                if ch.isspace() or unicodedata.category(ch) not in ("Cc", "Cf", "Co"))
    return " ".join(s.lower().split())


def _words(s: str) -> list[tuple[int, int, str]]:
    """`(raw start, raw end, one normalized word)` for every word in `s`.

    The word sequence this produces is exactly `normalize(s).split()`, which is
    what makes a match found here the same match the quote check found. A raw
    token that normalizes to two words -- a non-breaking space inside it -- gives
    two entries over the same raw span, and one that normalizes to nothing gives
    none, because `normalize` does both of those too.
    """
    out: list[tuple[int, int, str]] = []
    for m in re.finditer(r"\S+", s):
        for w in normalize(m.group(0)).split():
            out.append((m.start(), m.end(), w))
    return out


def extract_span(page: str, quote: str) -> str | None:
    """The PAGE's own characters for `quote`, wrapping collapsed, or None.

    `normalize` folds en dashes to hyphens, typographic quotes to straight ones
    and ligatures to their letters. That is right for deciding whether a quote is
    on the page and wrong for deciding what to write down: on 2026-11-04 `library`
    submitted `[24-27]` against an extract reading `[24\u201327]`, the page check
    passed, and the record stored the submitted bytes. The record had to be
    repaired by hand.

    **That is the dangerous direction.** A wrong page number is caught by the
    offset check. A quote that drifts from the extract by one character is caught
    by nothing, and a findings record exists to locate a passage faithfully enough
    to return to it.

    Words are matched rather than characters, because a quote usually stops just
    before a comma -- its first and last words are a suffix and a prefix of the
    extract's own -- and because the word sequence is the one thing the normalizer
    provably preserves. The span inside those two end words is then found by
    trying them, which asserts nothing: the test is
    `normalize(span) == normalize(quote)`, the same equality the check used.

    Returning None is a real answer. A quote that begins or ends inside a
    character the extract writes as a ligature has no raw span, and the caller
    refuses rather than falling back to the submitted bytes -- falling back is the
    defect this function exists to remove.
    """
    nq = normalize(quote)
    if not nq:
        return None
    q = nq.split()
    words = _words(page)
    m = len(q)
    for i in range(len(words) - m + 1):
        if m >= 3 and [w[2] for w in words[i + 1:i + m - 1]] != q[1:m - 1]:
            continue
        if m == 1:
            if q[0] not in words[i][2]:
                continue
        elif not (words[i][2].endswith(q[0]) and words[i + m - 1][2].startswith(q[-1])):
            continue
        lo_s, lo_e = words[i][0], words[i][1]
        hi_s, hi_e = words[i + m - 1][0], words[i + m - 1][1]
        for a in range(lo_s, lo_e):
            for b in range(max(a + 1, hi_s), hi_e + 1):
                if normalize(page[a:b]) == nq:
                    return " ".join(page[a:b].split())
    return None


# How far into a page a running head or footer can sit. Wider than this and the
# window reaches figure captions and reference markers, whose small integers vote
# for an offset near zero; see derive_offset.
EDGE_CHARS = 160

# How far before its first printed page an extract may begin: a scanned cover
# sheet or a journal's section divider, not an article starting mid-way.
LEAD_PAGES = 2

# How much a running head may wander between pages and still count as "the same
# place". Wide enough for a ragged last line, narrow enough that a figure number
# does not cluster with itself by chance.
POSITION_BAND = 120
# The same tolerance counted in lines. A character count is a lossy proxy for
# "the same place on the page": Knight2015Memgen prints its running head two
# lines from the end of every page, but a citation block on the title page puts
# that head 278 characters from the end against 140 on the last page -- one
# layout, two numbers, 138 apart. Lines say what the characters were standing in
# for. Two is the knee: widening to three buys six more papers across the
# library and makes the rule looser for all of them.
LINE_BAND = 2


def page_text(extract: str, page: int) -> str | None:
    """The text under one `<!-- p. N -->` marker, or None if there is no such page."""
    marks = [(m.start(), m.end(), int(m.group(1)))
             for m in re.finditer(r"<!-- p\. (\d+) -->", extract)]
    for i, (_s, e, n) in enumerate(marks):
        if n == page:
            end = marks[i + 1][0] if i + 1 < len(marks) else len(extract)
            return extract[e:end]
    return None


# The forms a printed folio takes, in ONE place, because this bug was three
# readers of the same quantity disagreeing. `derive_offset` has read `2040011-9`
# off the page since the easyAmber case; `confirm_offset` and the
# `--journal-page` check could only compare integers. So PRX's `021039-2` --
# the only locator Phys. Rev. X gives a reader, and one the derivation had
# already found -- was refused as a mismatch against the `2` it derived.
#
# Unrecognized forms are refused by name rather than guessed at, the same way
# `stem_conflict` excludes publisher templates one at a time. A rule that kept
# whatever digits it found would silently read the page RANGE `12-15` as page 15.
ARTICLE_FOLIO = re.compile(r"\b(\d{5,8})-(\d{1,3})\b")
LETTER_LEAD = re.compile(r"(?<![A-Za-z])([A-Za-z]{1,2})$")
FOLIO_FORMS = ("a page (863), an article-number folio (021039-2), "
               "or a lettered page (R863)")


def read_folio(s: str) -> tuple[str, int] | None:
    """`(what the journal prints around the number, the number)`, or None.

    The number is what offset arithmetic applies to; the rest is what makes the
    result citable. `021039-2` reads as `("021039-", 2)` and `R863` as
    `("R", 863)`. A form not listed in `FOLIO_FORMS` is None, never a guess.
    """
    s = s.strip()
    if re.fullmatch(r"\d{1,5}", s):
        return "", int(s)
    m = re.fullmatch(r"(\d{5,8})-(\d{1,3})", s)
    if m:
        return m.group(1) + "-", int(m.group(2))
    m = re.fullmatch(r"([A-Za-z]{1,2})(\d{1,5})", s)
    if m:
        return m.group(1), int(m.group(2))
    return None


def bib_page_range(out: Path, key: str) -> tuple[int, int] | None:
    """`(first, last)` printed pages `library.bib` records for `key`, or None.

    **This is ground truth the derivation was measured against and then not
    allowed to use.** `derive_offset`'s own docstring cites "the 2129 extracts
    whose bib entry records a numeric first page (marker 1 == that page)" as
    the corpus its two corrections were scored on -- so the repo already knows
    this field is reliable at that scale, and still re-derived the answer from
    stray integers at runtime.

    It is used as a tie-break and a check, never as an override: an extract can
    begin on a cover sheet or a title page, in which case marker 1 is not the
    first printed page and the offset really is something else. So it has to
    win votes like any other candidate -- it just wins ties, and it is what an
    operator's `--journal-page` is checked against.
    """
    path = out / "library.bib"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"@\w+\{" + re.escape(key) + r",(.*?)(?=\n@|\Z)", text, re.S)
    if not m:
        return None
    span = re.search(r"pages\s*=\s*\{+\s*(\d+)\s*-+\s*(\d+)", m.group(1))
    if not span:
        # **A lone number is not a one-page article.** It is usually an article
        # number -- `126606`, `012005`, MDPI's `1219` -- or a range the record
        # truncated. 0.31.1 read it as the range N-N, which made every marker
        # "outside" and cost 13 of `library`'s 148 findings: nine good offsets
        # lost and four records refused outright. There is no safe reading, so
        # there is no reading: a bib with no range constrains nothing.
        return None
    first, last = int(span.group(1)), int(span.group(2))
    return (first, last) if last >= first else None


def outside_pages(printed: int, pages: tuple[int, int] | None) -> bool:
    """Is this printed page outside what `library.bib` says the article spans?

    `library`'s suggestion, 2026-10-06, after 0.31.0 emitted "printed 312" for
    a paper whose bib entry reads `pages = {309--311}`. It is a backstop rather
    than a derivation: whatever reasoning produced the number, a page the
    article does not contain is wrong, and the bib is an independent source
    that costs nothing to consult.

    It catches what `derive_offset` cannot, because it sees the MARKER the
    operator actually cited. An offset can place most markers inside the range
    and still put the one being quoted outside it.
    """
    if pages is None:
        return False
    first, last = pages
    return not (first <= printed <= last)


def derive_offset(extract: str) -> tuple[int, int, str] | None:
    """(offset, how many pages agree, what is printed around the number), or None.

    The third element is the DECORATION, and it is the difference between a
    locator a reader can look up and one they cannot. Phys. Rev. X paginates by
    article number: marker 2 of Ninarello2017Models is printed `021039-2`, and
    `2` is not a page in that journal. This function already found `021039-2`
    on the page -- the article-number pattern below is the oldest thing in it --
    and then returned the bare suffix, so the record said `printed 2`. The
    decoration is recorded, never counted: it adds no vote and moves no offset.

    Do not trust a claimed page number: derive it. A running head or footer prints
    the journal page, so for each marker page collect the integer tokens near the
    EDGES of its text and take the offset that recurs. Anything that appears on
    every page but is not a page number -- a volume, a DOI fragment, an article
    number like 2040011 -- yields a different offset on each page and falls out of
    the tally by itself.

    Requiring recurrence is the point. The first version of this function asked
    whether the claimed number appeared anywhere on the page, which on a dense page
    is true of almost any small integer: a check that could not fail, reported as
    "verified".

    Two later corrections, both measured against the 2129 extracts whose bib entry
    records a numeric first page (marker 1 == that page), which is ground truth the
    derivation never sees:

    * EDGE_CHARS was 300 leading and 400 trailing -- four or five lines, enough to
      reach figure captions, section numbers and reference markers. Those small
      integers vote for an offset near zero on a paper whose markers are also small,
      and on a short paper they tie with the real running head.
    * The tie-break preferred the SMALLER absolute offset, so every one of those ties
      resolved to the coincidence rather than the head. It is exactly backwards: zero
      is the offset a coincidence produces. Kendrick1990Calculated (+0 x4 against a
      true +3994 x4) and Hamerton1996Molecular (+0 x3 against +310 x3) were both
      recorded with a confidently wrong page number this way.

    A tie is now refused outright. Across those 2129 papers the two changes together
    take wrong offsets from 294 to 227 and precision from 85.4% to 88.2%, at a cost
    of 4.2 points of coverage. Refusing a further margin (winner must lead by 2)
    would reach 89.0%, but it refuses Kendrick and Hamerton too -- it converts the
    reported bug into silence instead of fixing it -- so it is deliberately not done.

    Returning None is a real answer, not a failure: Georjon1997Molecular prints its
    running head where extraction puts it mid-page, and no edge window finds it.

    What those two corrections COST was not measured at the time, and should have
    been: 137 papers that derived correctly before them now refuse, against 99
    wrong derivations removed. confirm_offset reaches 120 of the 137 once an
    operator supplies the number. ROADMAP item 4 carries the re-measurement, on a
    larger corpus than the 2129 quoted above, and the seventeen still uncovered.

    **`pages` is `library.bib`'s own page range for this record, and it is
    the single largest improvement this function has had.** Measured 2026-10-06
    over the 2,482 extracts whose bib entry records a numeric first page:

        without it   precision 84.4%   339 wrong   coverage 87.4%
        with it      precision 89.6%   228 wrong   coverage 88.1%

    111 fewer wrong page numbers, and coverage slightly UP rather than traded
    away -- unlike both earlier corrections, which bought precision with
    silence. It reaches 89.6% where the rejected "winner must lead by 2" rule
    reached 89.0%, and it does so without refusing Kendrick1990Calculated or
    Hamerton1996Molecular, both of which still derive correctly.

    It works because the bib is an **independent** assertion about the same
    quantity: nothing in the extract knows what `library.bib` says, so a lone
    edge token that happens to land inside that range is not a
    coincidence, and one such vote outranks three unrelated small integers that
    happen to share an offset. That is what Garau2003Dual was.

    It cannot invent an offset no page voted for, which is what protects the
    case this docstring warns about: an extract beginning on a cover sheet has
    marker 1 somewhere other than the first printed page, so the hint is simply
    wrong, no page corroborates it, and it never fires.
    """
    marks = [(m.start(), m.end(), int(m.group(1)))
             for m in re.finditer(r"<!-- p\. (\d+) -->", extract)]
    tally: dict[int, int] = {}
    forms: dict[int, dict[str, set[int]]] = {}
    for i, (_s, e, n) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(extract)
        body = extract[e:end]
        edges = body[:EDGE_CHARS] + " " + body[-EDGE_CHARS:]
        cands: list[tuple[str, str]] = []
        for m in re.finditer(r"(?<![\d.-])(\d{1,5})(?![\d.])", edges):
            # J. Phys. Condens. Matter prints R863 for a topical review. The
            # lookbehind in LETTER_LEAD is what keeps "Fig2" and "eq3" out: a
            # run of letters longer than two is not a folio prefix.
            lead = LETTER_LEAD.search(edges[:m.start()])
            cands.append((lead.group(1) if lead else "", m.group(1)))
        # Article-number journals print "2040011-9" and have no journal page at all;
        # the suffix is the page. Without this easyAmber derived a WRONG offset that
        # four pages happened to agree on, which is worse than deriving none.
        for m in ARTICLE_FOLIO.finditer(edges):
            cands.append((m.group(1) + "-", m.group(2)))
        seen = set()
        for pre, tok in cands:
            off = int(tok) - n
            forms.setdefault(off, {}).setdefault(pre, set()).add(n)
            if off not in seen:            # one vote per page per offset
                seen.add(off)
                tally[off] = tally.get(off, 0) + 1
    if not tally:
        return None
    # The bib's first page breaks ties and nothing more. It cannot manufacture
    # support for an offset no page voted for, so an extract that starts on a
    # cover sheet still derives its real offset -- or refuses.
    ranked = sorted(tally.items(), key=lambda kv: (-kv[1], abs(kv[0])))
    best = ranked[0]
    # A real running footer appears on nearly every page. A coincidence appears on a
    # few. Requiring a clear majority is what separates them -- on the eight papers
    # whose offsets were derived by hand, every true offer agreed on 100% of pages
    # and the one false candidate on 21%.
    need = max(3, int(0.6 * len(marks)))
    if best[1] < need:
        return None
    if len(ranked) > 1 and ranked[1][1] == best[1]:
        return None      # two candidates equally supported: no winner, so say so
    # The decoration is a recurrence claim too, and is judged the same way the
    # offset is: it has to appear on a majority of pages. Requiring instead that
    # EVERY vote for the winning offset agree on one form does not work, because
    # the bare pattern fires on any stray integer that happens to equal the
    # printed page -- which on a journal whose offset is 0 is most of the small
    # integers on the page. The longer form wins a tie: `\d{5,8}-\d{1,3}`
    # recurring at one offset across a majority of pages is the folio, where a
    # bare integer doing so may be the coincidence this function already warns
    # about twice.
    pages = forms.get(best[0], {})
    pre = max(pages, key=lambda k: (len(pages[k]), len(k)), default="")
    return best[0], best[1], (pre if len(pages.get(pre, ())) >= need else "")


def _page_bodies(extract: str) -> list[tuple[int, str]]:
    """(marker number, that page's text) for every marker, in order."""
    marks = [(m.start(), m.end(), int(m.group(1)))
             for m in re.finditer(r"<!-- p\. (\d+) -->", extract)]
    return [(n, extract[e:(marks[i + 1][0] if i + 1 < len(marks) else len(extract))])
            for i, (_s, e, n) in enumerate(marks)]


def confirm_offset(extract: str, page: int, journal_page: int,
                   prefix: str = "") -> int | None:
    """Support for an offset the OPERATOR claims, or None. Never guesses one.

    `prefix` is what the journal prints around the number, from `read_folio`, and
    the search is for the whole printed folio rather than its digits: marker 3 of
    a PRX paper prints `021039-3`, and looking for a bare `3` would not find it --
    the guard before the match refuses a digit preceded by a hyphen, which is
    every page of such a paper. Without this the confirmer could only ever refuse
    an article-number folio, in a message naming a cause it had not tested.

    derive_offset looks near the page edges, which is where a running head sits
    on most papers and not on all of them. Lin1999Effect prints its footer and
    then a Wiley licence block, so the page number lands ~410 characters from the
    end of every page -- outside any edge window, yet at the SAME place on all
    twelve pages. That regularity is the real signal; proximity to the edge was
    only ever a proxy for it.

    So this scans the whole page and asks a different question: does the claimed
    offset show up at a consistent position across pages? A running head does. A
    figure number does not -- "Figure 8" on marker 8 sits wherever the figure is.

    It is deliberately NOT used to derive an offset on its own. Measured over the
    2129 extracts with a known first page, letting it guess unaided recovers 44
    correct offsets and invents 19 wrong ones -- 70% precision against the edge
    scan's 88% -- and a wrong locator is the failure that gets copied into a
    manuscript. As a test of a number a person already asserted it cannot invent
    anything: the hypothesis comes from the human, the evidence from the page.

    As a confirmer, measured over the 2402 extracts of three pages or more with a
    first page in library.bib: it accepts the true first page for 1861 of them,
    and accepts a deliberately wrong claim (first page + 7) for one. Counting
    lines as well as characters is what took the first number from 1807; the
    second did not move, and is Strukil2020Highly either way.
    """
    pp = _page_bodies(extract)
    if len(pp) < 3:
        return None
    off = journal_page - page
    # Four readings of "the same place", because no one of them holds for every
    # layout: characters from either end of the page, and lines from either end.
    from_end: list[tuple[int, int]] = []        # (marker page, characters from end)
    from_start: list[tuple[int, int]] = []      # (marker page, characters from start)
    lines_from_end: list[tuple[int, int]] = []  # (marker page, lines from end)
    lines_from_start: list[tuple[int, int]] = []
    for n, body in pp:
        want = str(n + off)
        if not want.lstrip("-").isdigit():
            continue
        want = prefix + want
        lines = body.split("\n")
        at = 0                                  # character offset of the line start
        for i, line in enumerate(lines):
            for m in re.finditer(r"(?<![\d.-])" + re.escape(want) + r"(?![\d.])", line):
                # A running head sits at a line boundary -- alone on its line, or at
                # the start or end of the header line. "Figure 8 shows the trend" does
                # not, and a figure number that happens to track the page number is
                # otherwise indistinguishable from a footer: it recurs, and on a
                # regularly laid out paper it recurs at a consistent position too.
                if line[:m.start()].strip() and line[m.end():].strip():
                    continue
                from_end.append((n, len(body) - (at + m.end())))
                from_start.append((n, at + m.start()))
                lines_from_end.append((n, len(lines) - 1 - i))
                lines_from_start.append((n, i))
            at += len(line) + 1                 # the newline split() removed

    def clustered(occ: list[tuple[int, int]], band: int) -> int:
        """Most distinct pages whose occurrence falls inside one band."""
        if not occ:
            return 0
        occ = sorted(occ, key=lambda x: x[1])
        best = lo = 0
        for hi in range(len(occ)):
            while occ[hi][1] - occ[lo][1] > band:
                lo += 1
            best = max(best, len({pg for pg, _ in occ[lo:hi + 1]}))
        return best

    support = max(clustered(from_end, POSITION_BAND),
                  clustered(from_start, POSITION_BAND),
                  clustered(lines_from_end, LINE_BAND),
                  clustered(lines_from_start, LINE_BAND))
    return support if support >= max(3, int(0.6 * len(pp))) else None


def next_ordinal(path: Path) -> int:
    if not path.exists():
        return 1
    nums = [int(m.group(1)) for m in re.finditer(r"^## (\d+) ", path.read_text(encoding="utf-8"), re.M)]
    return max(nums, default=0) + 1


def existing_ordinals(path: Path) -> set[int]:
    if not path.exists():
        return set()
    return {int(m.group(1)) for m in re.finditer(r"^## (\d+) ", path.read_text(encoding="utf-8"), re.M)}


def append(path: Path, key: str, block: str, dry_run: bool) -> None:
    if dry_run:
        print(f"--- would append to {path} ---\n{block}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(HEADER.format(key=key), encoding="utf-8")
    with path.open("a", encoding="utf-8") as f:
        f.write("\n" + block)
    print(f"appended to {path}")


class Locator(NamedTuple):
    """What a locator resolved to: the line, why it was refused, what to say.

    Exactly one of `text` and `problem` is non-empty. It is a return value rather
    than a `die()` so that `--replay` can ask the same question `main` asks and
    get the answer as data. A replay that re-derived the rule would drift from
    the rule -- the same defect, one level up, that made `library`'s hand-written
    parser of this tool's own output a weak guard.
    """
    text: str = ""
    problem: str = ""
    note: str = ""


def resolve_locator(extract: str, key: str, page: int, journal_page: str | None,
                    page_range: tuple[int, int] | None) -> Locator:
    """The locator for one record, and nothing else. Writes nothing, reads nothing."""
    locator = f"marker p. {page}"
    derived = derive_offset(extract)
    # **The bib vetoes a derivation; it never makes one.** 0.31.0 and 0.31.1
    # both tried to let `library.bib` steer the derivation -- first by its
    # first page, then by its range -- and `library`'s replay of 148 real
    # findings scored the second at 10 fixed against 15 newly broken. Each
    # version traded a wrong locator for a different wrong locator, because an
    # offset built partly from the bib is wrong in a way that looks plausible.
    #
    # Used only to REFUSE, it has none of that. An offset that puts the cited
    # marker on a page the article does not contain is wrong whatever supports
    # it, and discarding it loses nothing: the operator's own --journal-page
    # still goes through `confirm_offset`, which is how these records were
    # written before any of this.
    #
    # It is also the only thing that answers Berthier2023Modern, where the
    # attachment is a 17-page arXiv preprint and the bib holds the published
    # 15-page range. No derivation can be right there -- the extract is not
    # the document the pages describe -- and a refusal is the only correct
    # output. 0.31.1 emitted "printed 110" instead.
    vetoed = ""
    if derived and page_range and outside_pages(page + derived[0], page_range):
        vetoed = (f"derived offset {derived[0]:+d} would put marker "
                  f"p. {page} on page {page + derived[0]}, outside "
                  f"the {page_range[0]}-{page_range[1]} library.bib records")
        derived = None

    if not journal_page:
        if derived:
            off, agree, dpre = derived
            return Locator(f"{locator} \u00b7 printed {dpre}{page + off} "
                           f"(offset {off:+d}, derived from {agree} pages)")
        if vetoed:
            # No printed page rather than a wrong one. A findings record is
            # quoted back months later as the place a passage sits.
            return Locator(locator, note=(f"no printed page recorded -- {vetoed}. "
                                          f"Pass --journal-page if you can read the "
                                          f"number on the page."))
        return Locator(locator)

    folio = read_folio(journal_page)
    if folio is None:
        return Locator(problem=f"--journal-page {journal_page.strip()!r} is not a folio "
                               f"this tool reads. It reads {FOLIO_FORMS}. Nothing written.")
    prefix, jp = folio

    if derived is None:
        # The edge scan found nothing. Before refusing -- which blocks a correct
        # record and costs the locator entirely -- test the operator's own number
        # against the page positions. This cannot invent an offset; it can only
        # agree or stay silent.
        support = confirm_offset(extract, page, jp, prefix)
        if support is None:
            if vetoed:
                return Locator(problem=f"{vetoed}, and marker p. {page} does not carry "
                                       f"{journal_page.strip()} at a consistent position "
                                       f"either. Nothing written.")
            return Locator(problem=f"cannot derive a page offset for {key}, and marker "
                                   f"p. {page} does not carry {journal_page.strip()} at a "
                                   f"consistent position either. Check the printed number "
                                   f"on the rendered page (uv run --script get_pdf.py "
                                   f"{key}); if it is right, record this without "
                                   f"--journal-page and say in --used-for where the number "
                                   f"came from.")
        off = jp - page
        return Locator(f"{locator} \u00b7 printed {journal_page.strip()} (offset {off:+d}, "
                       f"confirmed at a consistent position on {support} pages)")

    off, agree, dpre = derived
    # The comparison is between NUMBERS and the report is in the operator's own
    # form. Comparing the strings refused `021039-2` against a derived `2` -- a
    # disagreement purely of form, on a journal where the form is the locator.
    if jp != page + off:
        return Locator(problem=f"offset says marker p. {page} is printed page "
                               f"{dpre}{page + off} (offset {off:+d}, agreeing on {agree} "
                               f"pages), not {journal_page.strip()}. Nothing written.")
    # Two forms for one number is a real disagreement, not a formatting
    # difference, and picking one quietly is how a locator nobody can look up
    # gets written with a derivation behind it.
    if dpre and prefix and dpre != prefix:
        return Locator(problem=f"{agree} pages print marker p. {page} as "
                               f"{dpre}{page + off}, not {journal_page.strip()}. "
                               f"Nothing written.")
    # The decoration recurs on a majority of pages, which is the same evidence
    # the offset rests on -- so a bare number the operator supplied for an
    # article-number journal is completed rather than recorded as the uncitable
    # `2` it is.
    shown = f"{dpre or prefix}{page + off}"
    note = ("" if shown == journal_page.strip() else
            f"the locator records {shown}, which is the folio the page carries, "
            f"rather than the submitted {journal_page.strip()}.")
    return Locator(f"{locator} \u00b7 printed {shown} (offset {off:+d}, "
                   f"derived from {agree} pages)", note=note)


def replay(out: Path, only: str | None, show_all: bool) -> int:
    """Re-run every record in `findings/` against its extract. Writes NOTHING.

    `library` built this twice by hand in one day and it found five durable
    records carrying a wrong journal page -- two of them visible without any tool
    at all, since a sibling record in the same file on the same marker page
    already held the right answer. It lives here rather than in a replay script
    for the reason they gave: a parser of this format that lives anywhere else
    matches FEWER records as the format drifts and reports them as fine.

    **The stored folio is passed back**, which is what makes a refusal mean
    something. Without it `confirm_offset` is unreachable -- it never derives an
    offset unaided, deliberately -- so every record that used it comes back bare
    and reads as a locator lost. That is how the first replay reported four
    correct locators dropped that were not dropped at all.

    Exit status is 1 when anything disagrees or cannot be read, so a sweep can be
    scripted. A disagreement is a claim about the mirror, never a repair: these
    files are append-only by their own header, and the fix is a RETRACTED record
    written by whoever owns the library.
    """
    fdir = out / "findings"
    if not fdir.is_dir():
        die(f"no findings directory at {fdir}")
    files = sorted(fdir.glob("*.md"))
    if only:
        files = [f for f in files if f.stem == only]
        if not files:
            die(f"no findings file for {only} under {fdir}")

    rows: list[tuple[str, str, str]] = []
    n = dict(records=0, disagree=0, quote=0, gains=0, support=0, blind=0,
             same=0, unreadable=0)

    def row(kind: str, key: str, ordinal: str, detail: str, mark: str) -> None:
        rows.append((kind, f"{key} {ordinal}".strip() + (f"  [{mark}]" if mark else ""),
                     detail))

    for path in files:
        key = path.stem
        recs, bad = parse_findings(path.read_text(encoding="utf-8"))
        for complaint in bad:
            n["unreadable"] += 1
            row("UNREADABLE", key, "", complaint, "")
        retracted = {r.ref for r in recs if r.kind == "RETRACTED"}
        hand = {r.ref for r in recs if r.kind == "CHECKED"}
        epath = out / "text" / f"{key}.md"
        text = epath.read_text(encoding="utf-8", errors="replace") if epath.is_file() else None
        rng = bib_page_range(out, key)
        for r in recs:
            if r.kind != "FINDING":
                continue
            n["records"] += 1
            ordinal = f"#{r.ordinal}"
            # A record a person checked against the PDF, or one already
            # retracted, is still reported -- the mark says which, because the
            # tool disagreeing with a human is MORE interesting, not less.
            mark = ("retracted" if r.ordinal in retracted
                    else "checked by hand" if r.ordinal in hand else "")
            if text is None:
                n["blind"] += 1
                row("NO EXTRACT", key, ordinal, f"nothing at text/{key}.md", mark)
                continue
            m = MARKER.match(r.locator)
            if not m:
                n["unreadable"] += 1
                row("UNREADABLE", key, ordinal,
                    f"locator does not begin with a marker page: {r.locator[:60]!r}", mark)
                continue
            page = int(m.group(1))
            body = page_text(text, page)
            if body is None:
                n["blind"] += 1
                row("NO SUCH PAGE", key, ordinal,
                    f"the extract has no marker page {page}", mark)
                continue
            if normalize(r.quote) not in normalize(body):
                n["blind"] += 1
                row("QUOTE GONE", key, ordinal,
                    f"the stored quote is no longer on marker p. {page}", mark)
                continue
            span = extract_span(body, r.quote)
            if span is not None and span != " ".join(r.quote.split()):
                n["quote"] += 1
                row("QUOTE DRIFT", key, ordinal,
                    f"stored:  {' '.join(r.quote.split())[:110]}\n"
                    f"extract: {span[:110]}", mark)
            pm = PRINTED.search(r.locator)
            stored = pm.group(1) if pm else None
            loc = resolve_locator(text, key, page, stored, rng)
            if loc.problem:
                n["disagree"] += 1
                row("DISAGREES", key, ordinal,
                    f"stored: {r.locator}\ntool:   {loc.problem}", mark)
                continue
            nm = PRINTED.search(loc.text)
            now = nm.group(1) if nm else None
            if stored is None and now is not None:
                n["gains"] += 1
                row("GAINS A PAGE", key, ordinal,
                    f"stored: {r.locator}\ntool:   {loc.text}", mark)
            elif stored != now:
                n["disagree"] += 1
                row("DISAGREES", key, ordinal,
                    f"stored: {r.locator}\ntool:   {loc.text}", mark)
            elif loc.text != r.locator:
                n["support"] += 1
                if show_all:
                    row("SUPPORT ONLY", key, ordinal,
                        f"stored: {r.locator}\ntool:   {loc.text}", mark)
            else:
                n["same"] += 1

    for kind, who, detail in rows:
        print(f"\n{kind}  {who}")
        for ln in detail.splitlines():
            print(f"    {ln}")

    # Every number says what it is a number OF, and the locator verdicts
    # partition the records while the quote count deliberately does not.
    print(f"\n{n['records']} FINDING records in {len(files)} file(s), "
          f"replayed against their extracts by offprint {__version__}:")
    print(f"  locator, one verdict per record:")
    print(f"    {n['disagree']:4d} disagreeing with this tool about the printed page")
    print(f"    {n['gains']:4d} carrying no printed page, which would now be given one")
    print(f"    {n['support']:4d} agreeing on the page, differing only in how much support "
          f"is reported" + ("" if show_all else " (--all lists them)"))
    print(f"    {n['blind']:4d} unverifiable -- no extract, no such marker page, or the "
          f"quote has moved")
    print(f"    {n['same']:4d} unchanged")
    print(f"  quote, counted separately -- a record here may also appear above:")
    print(f"    {n['quote']:4d} storing a quote differing from the extract's own characters")
    print(f"  reader:")
    print(f"    {n['unreadable']:4d} block(s) this tool could not read in its own format")
    print("\nNothing was written. These files are append-only: a disagreement is "
          "repaired\nwith a RETRACTED record by whoever owns the library, not by this tool.")
    return 1 if (n["disagree"] or n["unreadable"]) else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Record what was asked of a paper and where the answer is.")
    ap.add_argument("--key", help="citation key (every mode but --replay needs one)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="mirror directory")
    ap.add_argument("--dry-run", action="store_true", help="show the record, write nothing")
    ap.add_argument("--replay", action="store_true",
                    help="re-check every record in findings/ against its extract; writes nothing")
    ap.add_argument("--all", action="store_true",
                    help="--replay: list the records that differ only in reported support")
    # a finding
    ap.add_argument("--question", help="what was asked")
    ap.add_argument("--quote", help="the verbatim sentence that answered it")
    ap.add_argument("--page", type=int, help="extract marker page the quote is on")
    ap.add_argument("--journal-page",
                    help="printed folio: 863, or 021039-2, or R863")
    ap.add_argument("--asked-by", default="", help="who asked")
    ap.add_argument("--used-for", default="", help="what the answer was used for")
    # the other two record types
    ap.add_argument("--check", type=int, metavar="N", help="append a CHECKED record for finding N")
    ap.add_argument("--retract", type=int, metavar="N", help="append a RETRACTED record for finding N")
    ap.add_argument("--by", default="", help="who checked")
    ap.add_argument("--note", default="", help="note for a CHECKED or RETRACTED record")
    args = ap.parse_args()

    out = args.out.expanduser()
    if args.replay:
        return replay(out, args.key, args.all)
    if not args.key:
        die("--key is required for everything but --replay")
    path = out / "findings" / f"{args.key}.md"
    today = _dt.date.today().isoformat()

    if args.check is not None or args.retract is not None:
        n = args.check if args.check is not None else args.retract
        kind = "CHECKED" if args.check is not None else "RETRACTED"
        have = existing_ordinals(path)
        if n not in have:
            die(f"{args.key} has no record {n}" + (f" (has {sorted(have)})" if have else " (no findings yet)"))
        who = f" · {args.by}" if args.by else ""
        body = f"re: {n} — {args.note}" if args.note else f"re: {n}"
        append(path, args.key, f"## {next_ordinal(path)} · {kind} · {today}{who}\n{body}\n", args.dry_run)
        return 0

    if not (args.question and args.quote and args.page):
        die("a finding needs --question, --quote and --page (or use --check / --retract)")

    extract = out / "text" / f"{args.key}.md"
    if not extract.exists():
        die(f"no extract at {extract} -- is {args.key} a citation key this mirror knows?")
    text = extract.read_text(encoding="utf-8", errors="replace")

    page = page_text(text, args.page)
    if page is None:
        pages = [int(m.group(1)) for m in re.finditer(r"<!-- p\. (\d+) -->", text)]
        die(f"{args.key} has no marker page {args.page} (it has 1-{max(pages) if pages else 0})")

    if not normalize(args.quote):
        die("--quote is empty once normalized. Nothing written.")

    if normalize(args.quote) not in normalize(page):
        where = [n for n in (int(m.group(1)) for m in re.finditer(r"<!-- p\. (\d+) -->", text))
                 if (pt := page_text(text, n)) and normalize(args.quote) in normalize(pt)]
        hint = f" -- but it IS on marker page {where[0]}" if where else ""
        die(f"that quote is not on marker page {args.page} of {args.key}{hint}. "
            f"Nothing written. Re-read the extract rather than adjusting the quote.")

    # **The record stores the extract's characters, not the submitted ones.**
    # The check above normalizes both sides, so it passes on a difference it
    # then has no way to report -- and what used to be written down was the
    # submitted form. `library` filed `[24-27]` against an extract reading
    # `[24–27]` on 2026-11-04 and repaired the record by hand afterwards.
    # Line wrapping is the one difference NOT preserved: where the PDF broke the
    # line is a fact about the PDF, not about the sentence.
    quote = extract_span(page, args.quote)
    if quote is None:
        die(f"the quote matches marker p. {args.page} of {args.key} only after "
            f"normalization, and the extract's own characters for it could not be "
            f"located -- it begins or ends inside a ligature or a similar fold. "
            f"Nothing written. Copy the sentence out of text/{args.key}.md.")
    if quote != " ".join(args.quote.split()):
        print("note: the record stores the extract's wording, not the submitted "
              "wording.\n"
              f"      submitted: {' '.join(args.quote.split())}\n"
              f"      extract:   {quote}")

    loc = resolve_locator(text, args.key, args.page, args.journal_page,
                          bib_page_range(out, args.key))
    if loc.problem:
        die(loc.problem)
    if loc.note:
        print(f"note: {loc.note}")
    locator = loc.text

    append(path, args.key,
           render_finding(next_ordinal(path), today, args.question, args.asked_by,
                          locator, quote, args.used_for),
           args.dry_run)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
