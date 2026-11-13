#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]   # transitive: mirror imports it for DEFAULT_OUT
# ///
"""
pdbrefs.py -- which papers in the library cite which PDB structures.

A fifth of the mirrored papers mention the Protein Data Bank, and the accession
codes are sitting in `text/` already. This reads them out, so "which structures
came from papers we hold?" is a question the library can answer on its own,
without touching RCSB.

    uv run --script pdbrefs.py                 # summary
    uv run --script pdbrefs.py --list          # every accession and who cites it
    uv run --script pdbrefs.py --key Jo2007Automated
    uv run --script pdbrefs.py --id 2HAC       # who cites this structure

**Context is required, and that is the whole design.** A PDB accession is four
characters -- a digit then three alphanumerics -- which also describes "1997",
"3D", "2ND", "1H50" and half the equation labels in a physics paper. Matching the
shape alone produces a long list that looks authoritative and is mostly noise. So
an accession counts only when the surrounding text says it is one: "PDB ID 1ABC",
"PDB code: 1ABC", "Protein Data Bank under accession 1ABC", "rcsb.org/.../1ABC",
and the list forms that follow them ("PDB IDs 1ABC, 2DEF and 3GHI").

**What this necessarily misses, and it should be said rather than discovered.**
A paper that writes a bare "(1ABC)" with no nearby cue is invisible here, and so
is every one of the ~94 attachments with no text layer -- a scanned paper cannot
be grepped at all. The subtilisin paper filed this week deposits 1SCD and says so
in a footnote on a scanned page: this script will never see it. **Treat the output
as a floor, never as a census.**

Requiring a cue also quietly excludes accessions that appear only inside tables,
and that is a feature. A table's text layer interleaves columns and glues
footnote markers onto the cells, so Jo 2007's table yields "1SU44" and "2A654" --
right structures, wrong strings. Cued prose is the reliable source; a table needs
the rendered page, as it does for every other kind of number.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    from mirror import DEFAULT_OUT
except ImportError as exc:  # pragma: no cover
    sys.exit(f"pdbrefs.py must sit beside the other mirror scripts ({exc})")

ACC = r"[1-9][A-Za-z0-9]{3}"

# A cue that the next token really is an accession. Ordered longest-first so the
# specific forms win; each may be followed by a list, handled by LIST below.
CUE = re.compile(
    r"(?:Protein\s+Data\s+Bank\s*(?:\(PDB\))?\s*"
    r"(?:under\s+)?(?:accession|reference|entry|entries|ID|IDs|code|codes|with)?"
    r"[\s:.,]*(?:no\.|code)?"
    r"|PDB[\s-]*(?:ID|IDs|code|codes|entry|entries|accession|structure|structures)?"
    r"|rcsb\.org/(?:structure/|pdb/explore(?:/explore)?\.do\?structureId=)"
    r")[\s:=]*",
    re.I,
)
# "1ABC, 2DEF and 3GHI" -- keep consuming while the separators stay list-like.
# Papers commonly gloss each entry as they go: "PDB:1H2S (sensory rhodopsin II),
# 2A65 (a bacterial homolog of ...), 1SU4 (calcium ATPase)". Without stepping over
# those parentheticals the list ends at the first gloss and every later accession
# is lost -- which is how 2A65 went missing from Jo 2007 on the first pass.
GLOSS = r"(?:\s*\([^)]{0,90}\))?"
# The accession must not begin inside a word. Without this, the cue "PDB" eats the
# tail of its own software: PDB2PQR yields "2PQR", pdb2gmx yields "2GMX", and both
# arrive looking exactly like structures -- 2PQR was reported as the 5th most-cited
# structure in the library, by the PDB2PQR paper among others.
LIST = re.compile(rf"(?<![A-Za-z0-9])({ACC}){GLOSS}((?:\s*(?:,|;|/|and|&)\s*{ACC}{GLOSS})*)", re.I)
MORE = re.compile(rf"({ACC})", re.I)

# Four-character tokens that pass the shape test but are never accessions. Years
# dominate; the rest showed up in a first pass over the real corpus.
NOT_ACC = {"1H50", "3RD", "2ND"} | {str(y) for y in range(1900, 2100)}

# Software whose NAME ends in something accession-shaped, where extraction has
# already split the word: "PDB2PQR" comes out of some PDFs as "PDB 2PQR", which no
# pattern can tell from a real citation. Suppressed by name and REPORTED, not
# filtered silently -- if one of these ever is a real entry someone cites, the
# summary line is what makes that visible.
SOFTWARE = {"2PQR": "PDB2PQR", "2GMX": "pdb2gmx"}


def accessions_in(text: str) -> dict[str, int]:
    """{ACCESSION: times cued} for one extract."""
    out: dict[str, int] = defaultdict(int)
    for cue in CUE.finditer(text):
        m = LIST.match(text, cue.end())
        if not m:
            continue
        for acc in MORE.findall(m.group(0)):
            acc = acc.upper()
            if acc not in NOT_ACC and acc not in SOFTWARE:
                out[acc] += 1
    return dict(out)


def scan(out: Path) -> dict[str, dict[str, int]]:
    by_key: dict[str, dict[str, int]] = {}
    for f in sorted((out / "text").glob("*.md")):
        hits = accessions_in(f.read_text(encoding="utf-8", errors="replace"))
        if hits:
            by_key[f.stem] = hits
    return by_key


def main() -> int:
    ap = argparse.ArgumentParser(description="Which papers cite which PDB structures.")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="mirror directory")
    ap.add_argument("--list", action="store_true", help="every accession and its papers")
    ap.add_argument("--key", help="accessions cited by one paper")
    ap.add_argument("--id", help="papers citing one accession")
    args = ap.parse_args()

    out = args.out.expanduser()
    if not (out / "text").is_dir():
        sys.exit(f"error: no text/ under {out}")
    by_key = scan(out)

    by_acc: dict[str, set[str]] = defaultdict(set)
    for key, hits in by_key.items():
        for acc in hits:
            by_acc[acc].add(key)

    if args.key:
        hits = by_key.get(args.key)
        if hits is None:
            extract = out / "text" / f"{args.key}.md"
            if not extract.exists():
                sys.exit(f"error: no extract for {args.key}")
            print(f"{args.key}: no cued PDB accessions found "
                  f"(a bare '(1ABC)' or a scanned page would not be seen)")
            return 0
        print(f"{args.key} cites {len(hits)}:")
        for acc, n in sorted(hits.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {acc}  ({n}x)")
        return 0

    if args.id:
        acc = args.id.upper()
        papers = sorted(by_acc.get(acc, ()))
        if not papers:
            print(f"{acc}: not cited by any extract -- which is a floor, not a census")
            return 0
        print(f"{acc} is cited by {len(papers)}:")
        for k in papers:
            print(f"  {k}")
        return 0

    total_extracts = len(list((out / "text").glob("*.md")))
    print(f"extracts scanned          : {total_extracts}")
    print(f"papers citing a structure : {len(by_key)}  ({100*len(by_key)/max(total_extracts,1):.0f}%)")
    print(f"distinct accessions       : {len(by_acc)}")
    print(f"paper-structure links     : {sum(len(v) for v in by_key.values())}")
    print(f"suppressed as software    : " +
          ", ".join(f"{k} ({v})" for k, v in sorted(SOFTWARE.items())))

    if args.list:
        print("\naccession  papers")
        for acc in sorted(by_acc, key=lambda a: (-len(by_acc[a]), a)):
            print(f"  {acc}     {', '.join(sorted(by_acc[acc]))}")
    else:
        print("\nmost-cited structures in this library:")
        for acc in sorted(by_acc, key=lambda a: (-len(by_acc[a]), a))[:12]:
            papers = sorted(by_acc[acc])
            shown = ", ".join(papers[:3]) + (f" +{len(papers)-3}" if len(papers) > 3 else "")
            print(f"  {acc}  {len(papers):>2} paper(s)   {shown}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
