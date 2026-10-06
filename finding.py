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

This script never touches Mendeley and never writes outside `<out>/findings/`.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    from mendeley_mirror import DEFAULT_OUT
except ImportError as exc:  # pragma: no cover
    sys.exit(f"finding.py must sit beside the other mirror scripts ({exc})")

HEADER = """# Findings: {key}

*Records **locate** a passage; they never substitute for one. Re-read
`text/{key}.md` before quoting anything below. Append-only: corrections are
appended as RETRACTED records, never edited in.*
"""


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


# How far into a page a running head or footer can sit. Wider than this and the
# window reaches figure captions and reference markers, whose small integers vote
# for an offset near zero; see derive_offset.
EDGE_CHARS = 160

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


def bib_first_page(out: Path, key: str) -> int | None:
    """The first printed page `library.bib` records for `key`, or None.

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
    pm = re.search(r"pages\s*=\s*\{+\s*(\d+)", m.group(1))
    return int(pm.group(1)) if pm else None


def derive_offset(extract: str, first_page: int | None = None) -> tuple[int, int] | None:
    """(offset, how many pages agree), or None if the printed pages cannot be read.

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

    **`first_page` is `library.bib`'s own first page for this record, and it is
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
    edge token that happens to equal `first_page - marker` is not a
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
    for i, (_s, e, n) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(extract)
        body = extract[e:end]
        edges = body[:EDGE_CHARS] + " " + body[-EDGE_CHARS:]
        cands = re.findall(r"(?<![\d.-])(\d{1,5})(?![\d.])", edges)
        # Article-number journals print "2040011-9" and have no journal page at all;
        # the suffix is the page. Without this easyAmber derived a WRONG offset that
        # four pages happened to agree on, which is worse than deriving none.
        cands += re.findall(r"\b\d{5,8}-(\d{1,3})\b", edges)
        seen = set()
        for tok in cands:
            off = int(tok) - n
            if off not in seen:            # one vote per page per offset
                seen.add(off)
                tally[off] = tally.get(off, 0) + 1
    if not tally:
        return None
    # The bib's first page breaks ties and nothing more. It cannot manufacture
    # support for an offset no page voted for, so an extract that starts on a
    # cover sheet still derives its real offset -- or refuses.
    hint = (first_page - marks[0][2]) if first_page is not None and marks else None
    ranked = sorted(tally.items(),
                    key=lambda kv: (-kv[1], kv[0] != hint, abs(kv[0])))
    best = ranked[0]
    # A real running footer appears on nearly every page. A coincidence appears on a
    # few. Requiring a clear majority is what separates them -- on the eight papers
    # whose offsets were derived by hand, every true offer agreed on 100% of pages
    # and the one false candidate on 21%.
    need = max(3, int(0.6 * len(marks)))
    if best[1] < need:
        return None
    # **The bib's first page plus ONE corroborating page is two independent
    # sources, and beats three coincidences.** The recurrence rule exists to
    # reject a lone accidental match -- but a lone match that also equals the
    # first page `library.bib` records is not accidental, because nothing in
    # the extract knows what the bib says. Garau2003Dual: after the download
    # stamp is stripped, +2226 keeps only page 3's running head, one vote,
    # while +4 still has three from unrelated digits. One vote from the right
    # source wins.
    #
    # The >= 1 is what protects the cover-sheet case the docstring warns about:
    # where marker 1 is NOT the first printed page the hint is simply wrong,
    # no page corroborates it, and this never fires.
    if hint is not None and tally.get(hint, 0) >= 1:
        return (hint, tally[hint])
    if len(ranked) > 1 and ranked[1][1] == best[1]:
        return None      # two candidates equally supported: no winner, so say so
    # A winner that CONTRADICTS the bib is not a winner. Garau2003Dual, reported
    # by `library` 2026-10-06: +4 took three votes from three unrelated small
    # integers -- "05" on page 1, "06" on page 2, "7" on page 3 -- while the
    # true +2226 took only two, because page 2's running head did not reach the
    # edge window. The tool then wrote "printed 6" into a findings file and
    # REFUSED the operator's correct 2228. One vote per page per offset cannot
    # tell three coincidences from a running head; the bib can.
    if hint is not None and best[0] != hint and tally.get(hint, 0) >= 2:
        return None
    return best


def _page_bodies(extract: str) -> list[tuple[int, str]]:
    """(marker number, that page's text) for every marker, in order."""
    marks = [(m.start(), m.end(), int(m.group(1)))
             for m in re.finditer(r"<!-- p\. (\d+) -->", extract)]
    return [(n, extract[e:(marks[i + 1][0] if i + 1 < len(marks) else len(extract))])
            for i, (_s, e, n) in enumerate(marks)]


def confirm_offset(extract: str, page: int, journal_page: int) -> int | None:
    """Support for an offset the OPERATOR claims, or None. Never guesses one.

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


def main() -> int:
    ap = argparse.ArgumentParser(description="Record what was asked of a paper and where the answer is.")
    ap.add_argument("--key", required=True, help="citation key")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="mirror directory")
    ap.add_argument("--dry-run", action="store_true", help="show the record, write nothing")
    # a finding
    ap.add_argument("--question", help="what was asked")
    ap.add_argument("--quote", help="the verbatim sentence that answered it")
    ap.add_argument("--page", type=int, help="extract marker page the quote is on")
    ap.add_argument("--journal-page", help="printed page, if the journal has one")
    ap.add_argument("--asked-by", default="", help="who asked")
    ap.add_argument("--used-for", default="", help="what the answer was used for")
    # the other two record types
    ap.add_argument("--check", type=int, metavar="N", help="append a CHECKED record for finding N")
    ap.add_argument("--retract", type=int, metavar="N", help="append a RETRACTED record for finding N")
    ap.add_argument("--by", default="", help="who checked")
    ap.add_argument("--note", default="", help="note for a CHECKED or RETRACTED record")
    args = ap.parse_args()

    out = args.out.expanduser()
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

    if normalize(args.quote) not in normalize(page):
        where = [n for n in (int(m.group(1)) for m in re.finditer(r"<!-- p\. (\d+) -->", text))
                 if (pt := page_text(text, n)) and normalize(args.quote) in normalize(pt)]
        hint = f" -- but it IS on marker page {where[0]}" if where else ""
        die(f"that quote is not on marker page {args.page} of {args.key}{hint}. "
            f"Nothing written. Re-read the extract rather than adjusting the quote.")

    locator = f"marker p. {args.page}"
    derived = derive_offset(text, bib_first_page(out, args.key))
    if args.journal_page:
        if derived is None:
            # The edge scan found nothing. Before refusing -- which blocks a
            # correct record and costs the locator entirely -- test the operator's
            # own number against the page positions. This cannot invent an offset;
            # it can only agree or stay silent.
            support = confirm_offset(text, args.page, int(args.journal_page))
            if support is None:
                die(f"cannot derive a page offset for {args.key}, and marker p. {args.page} "
                    f"does not carry {args.journal_page} at a consistent position either. "
                    f"Check the printed number on the rendered page "
                    f"(uv run --script get_pdf.py {args.key}); if it is right, record this "
                    f"without --journal-page and say in --used-for where the number came from.")
            off = int(args.journal_page) - args.page
            locator += (f" · printed {args.journal_page} (offset {off:+d}, confirmed at a "
                        f"consistent position on {support} pages)")
        else:
            off, agree = derived
            want = args.page + off
            if str(args.journal_page).strip() != str(want):
                die(f"offset says marker p. {args.page} is printed page {want} "
                    f"(offset {off:+d}, agreeing on {agree} pages), not {args.journal_page}. "
                    f"Nothing written.")
            locator += f" · printed {want} (offset {off:+d}, derived from {agree} pages)"
    elif derived:
        off, agree = derived
        locator += f" · printed {args.page + off} (offset {off:+d}, derived from {agree} pages)"

    lines = [f"## {next_ordinal(path)} · FINDING · {today}",
             f"question:  {args.question}"]
    if args.asked_by:
        lines.append(f"asked-by:  {args.asked_by}")
    lines.append(f"locator:   {locator}")
    lines.append("")
    lines += ["> " + ln for ln in args.quote.strip().splitlines()]
    if args.used_for:
        lines += ["", f"used-for:  {args.used_for}"]
    append(path, args.key, "\n".join(lines) + "\n", args.dry_run)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
