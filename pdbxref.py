#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]
# ///
"""
pdbxref.py -- structures the library cites whose primary papers it does not hold.

`pdbrefs.py` reads accessions out of the mirrored text. This asks RCSB what paper
each one came from, and checks that paper against `library.bib`.

    uv run --script pdbxref.py                  # ranked summary
    uv run --script pdbxref.py --min 3          # only what 3+ papers reach for
    uv run --script pdbxref.py --tsv gap.tsv    # full table

**Rank by how many LIBRARY PAPERS reach for a structure, not by how many
structures share a paper.** Those are different questions and only the first one
is useful. A single deposition often lands a dozen entries at once, so ranking by
entry count promotes whichever group deposited in bulk. Ranked by demand instead,
the shape of this library's own field appears at the top -- and in practice ~80%
of the gap is wanted by exactly one paper, which usually means somebody borrowed
coordinates rather than needing the argument.

**This produces a prompt, not a want-list.** That a structure keeps being reached
for is evidence the paper might be worth holding; whether it actually is depends
on what the structure is being used FOR, which is a judgment about the science and
belongs to the person whose library it is.

**Two things it cannot see.** An entry with no primary-citation DOI in RCSB is
invisible here rather than absent -- older depositions especially. And accessions
RCSB does not recognize at all are reported separately: some are obsoleted
entries, some are false positives escaping `pdbrefs.py`, and either way that count
is the only external measurement of the local index's precision. Read it as such.

**Unlike a paper, a PDB entry is not frozen.** Entries get re-refined, superseded
and obsoleted, so an answer here is true on the day it was asked. Output carries
the date for that reason; a claim about a structure has a shelf life a claim about
a published page does not.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    import requests
    from mirror import DEFAULT_OUT
    from pdbrefs import scan
except ImportError as exc:  # pragma: no cover
    sys.exit(f"pdbxref.py must sit beside the other mirror scripts ({exc})")

API = "https://data.rcsb.org/graphql"
UA = "offprint pdbxref (mailto:cfa22@drexel.edu)"
QUERY = ("{entries(entry_ids:[%s]){rcsb_id struct{title} "
         "rcsb_primary_citation{pdbx_database_id_DOI pdbx_database_id_PubMed "
         "title year journal_abbrev} "
         "rcsb_accession_info{status_code}}}")


def flat(s: str | None) -> str:
    """Collapse whitespace. Citation titles carry newlines; a TSV row cannot."""
    return " ".join((s or "").split())


def norm_title(s: str) -> str:
    """A title reduced to something two sources can agree on."""
    s = re.sub(r"\\[a-zA-Z]+|[{}$\\]", " ", s or "")
    return " ".join(re.findall(r"[a-z0-9]+", s.lower()))


def library_keys(out: Path) -> tuple[set[str], set[str], set[str]]:
    """(dois, pmids, normalized titles) -- THREE keys, because one is not enough.

    17% of this library's records carry no `doi` field: an older import
    often has a PMID and a PubMed URL instead. Matching on DOI alone reported
    Kwong 2000 as a paper the library lacked, it was downloaded a second time, and
    the extract had been on disk since August. RCSB publishes a PubMed ID beside
    the DOI, and titles are the last resort -- so use all three.
    """
    bib = (out / "library.bib").read_text(encoding="utf-8")
    dois = {m.group(1).strip().lower()
            for m in re.finditer(r"^\s*doi\s*=\s*\{(.*?)\}", bib, re.M)}
    pmids = {m.group(1).strip()
             for m in re.finditer(r"^\s*pmid\s*=\s*\{(\d+)\}", bib, re.M)}
    titles = {norm_title(m.group(1))
              for m in re.finditer(r"^\s*title\s*=\s*\{+(.*?)\}+,\s*$", bib, re.M | re.S)}
    return dois, pmids, titles - {""}


def crossref(entries: list[dict], have, acc2papers: dict[str, set[str]]):
    """(ranked gap, held, no_doi) -- pure, so the ranking is testable offline.

    `have` is (dois, pmids, titles). A citation counts as held if ANY of the three
    matches: a DOI-only test called Kwong 2000 missing because that record carries
    a PMID instead, and it was downloaded twice as a result.

    RCSB returns DOIs in mixed case ("10.1126/SCIENCE.AAD2450"), so both sides are
    lowered before comparison; without that every Science structure looks missing.
    """
    have_dois, have_pmids, have_titles = have
    gap: dict[str, dict] = {}
    held = no_doi = 0
    for e in entries:
        cit = (e or {}).get("rcsb_primary_citation") or {}
        doi = (cit.get("pdbx_database_id_DOI") or "").strip().lower()
        pmid = str(cit.get("pdbx_database_id_PubMed") or "").strip()
        ntitle = norm_title(cit.get("title"))
        if not doi:
            no_doi += 1
            continue
        if doi in have_dois or (pmid and pmid in have_pmids) or (ntitle and ntitle in have_titles):
            held += 1
            continue
        g = gap.setdefault(doi, {"year": cit.get("year"),
                                 "journal": flat(cit.get("journal_abbrev")),
                                 "title": flat(cit.get("title")),
                                 "ids": set(), "papers": set()})
        acc = e["rcsb_id"].upper()
        g["ids"].add(acc)
        g["papers"] |= acc2papers.get(acc, set())
    ranked = sorted(gap.items(), key=lambda kv: (-len(kv[1]["papers"]), -len(kv[1]["ids"]), kv[0]))
    return ranked, held, no_doi


def replay(path: Path) -> list[dict]:
    """Rebuild citation records from a previous table, so no query is needed.

    One row per PAPER in the table, one entry per STRUCTURE here -- crossref()
    counts structures, so a three-structure paper must come back as three.
    """
    out: list[dict] = []
    rows = [l for l in path.read_text(encoding="utf-8").splitlines() if not l.startswith("#")]
    import csv as _csv
    for r in _csv.DictReader(rows, delimiter="\t"):
        cit = {"pdbx_database_id_DOI": r["doi"], "title": r["title"],
               "year": r["year"], "journal_abbrev": r["journal"]}
        for acc in r["structures"].split(","):
            out.append({"rcsb_id": acc, "rcsb_primary_citation": dict(cit)})
    return out


def fetch(accs: list[str], batch: int = 60) -> tuple[list[dict], list[str]]:
    sess = requests.Session()
    sess.headers["User-Agent"] = UA
    got, unresolved = [], []
    for i in range(0, len(accs), batch):
        chunk = accs[i:i + batch]
        ids = ",".join(f'"{a}"' for a in chunk)
        for attempt in range(3):
            try:
                r = sess.post(API, json={"query": QUERY % ids}, timeout=60)
                if r.status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(2 * (attempt + 1))
        else:
            unresolved += chunk
            continue
        seen = set()
        for e in (r.json().get("data", {}).get("entries") or []):
            if e:
                seen.add(e["rcsb_id"].upper())
                got.append(e)
        unresolved += [a for a in chunk if a not in seen]
        print(f"  {min(i + batch, len(accs))}/{len(accs)}", file=sys.stderr, flush=True)
    return got, unresolved


def main() -> int:
    ap = argparse.ArgumentParser(description="Structures cited here whose papers are not held.")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="mirror directory")
    ap.add_argument("--min", type=int, default=1, help="only papers wanted by at least N library papers")
    ap.add_argument("--tsv", type=Path, help="write the full table here (default <out>/pdb-gap.tsv)")
    ap.add_argument("--from-tsv", type=Path, nargs="?", const=True,
                    help="re-filter a previous table against the CURRENT library, no network")
    ap.add_argument("--top", type=int, default=15, help="how many to print")
    args = ap.parse_args()

    out = args.out.expanduser()
    by_key = scan(out)
    acc2papers: dict[str, set[str]] = {}
    for key, hits in by_key.items():
        for acc in hits:
            acc2papers.setdefault(acc, set()).add(key)
    accs = sorted(acc2papers)
    if not accs:
        sys.exit("error: pdbrefs found no accessions -- is text/ populated?")

    have = library_keys(out)
    print(f"{len(accs)} accessions from {len(by_key)} papers; library keys: "
          f"{len(have[0])} DOIs, {len(have[1])} PMIDs, {len(have[2])} titles", file=sys.stderr)

    if args.from_tsv:
        # Re-filtering only needs the library, not RCSB. Filing a paper removes it
        # from the gap, and that is a local fact -- so the page can be brought up to
        # date after every filing without 830 network calls or any risk of drift.
        cache = out / "pdb-gap.tsv" if args.from_tsv is True else args.from_tsv
        entries = replay(cache)
        unresolved = []
        print(f"replayed {len(entries)} citations from {cache} (no network)", file=sys.stderr)
    else:
        entries, unresolved = fetch(accs)
    ranked, held, no_doi = crossref(entries, have, acc2papers)

    today = _dt.date.today().isoformat()
    print(f"\n# PDB cross-reference, {today}  (an entry is not frozen -- this is true today)")
    if args.from_tsv:
        # A replay sees only the previously-missing set, so these three counts are
        # NOT MEASURED here. Printing 0 for them would be a zero that means
        # "did not look", which reads exactly like a zero that means "none".
        print(f"replayed citations      : {len(entries)}  (from cache; RCSB not queried)")
        print("unrecognized accessions : not measured in replay")
        print("no primary-citation DOI : not measured in replay")
        print(f"newly held since cache  : {held}  <- papers filed since the last full run")
    else:
        print(f"resolved by RCSB        : {len(entries)}")
        print(f"unrecognized accessions : {len(unresolved)}  <- obsolete, or pdbrefs false positives")
        print(f"no primary-citation DOI : {no_doi}  <- invisible here, not absent")
        print(f"primary citation held   : {held}")
    print(f"primary citation MISSING: {sum(len(g['ids']) for _, g in ranked)}"
          f" structures, {len(ranked)} distinct papers")

    dist = [len(g["papers"]) for _, g in ranked]
    if dist:
        one = sum(1 for x in dist if x <= 1)
        print(f"  wanted by 1 paper : {one} ({100*one/len(dist):.0f}%) -- usually borrowed coordinates")
        print(f"  wanted by 3+      : {sum(1 for x in dist if x >= 3)}")
        print(f"  wanted by 5+      : {sum(1 for x in dist if x >= 5)}")

    shown = [(d, g) for d, g in ranked if len(g["papers"]) >= args.min]
    print(f"\nwanted by >= {args.min} library paper(s), most-wanted first:\n")
    for doi, g in shown[:args.top]:
        print(f"{len(g['papers']):>3} papers · {len(g['ids'])} structure(s) · {g['year']} · {g['journal']}")
        print(f"    {g['title'][:96]}")
        print(f"    {doi}   {','.join(sorted(g['ids']))[:60]}")

    tsv = args.tsv or (None if args.from_tsv else out / "pdb-gap.tsv")
    if tsv:
        with tsv.open("w", encoding="utf-8") as f:
            f.write(f"# generated {today}; PDB entries are versioned, this is true as of that date\n")
            f.write("library_papers\tn_structures\tdoi\tyear\tjournal\ttitle\tstructures\tciting_papers\n")
            for doi, g in ranked:
                f.write(f"{len(g['papers'])}\t{len(g['ids'])}\t{doi}\t{g['year']}\t{g['journal']}\t"
                        f"{g['title']}\t{','.join(sorted(g['ids']))}\t{','.join(sorted(g['papers']))}\n")
        print(f"\nfull table: {tsv}")
    if unresolved:
        print(f"\nunrecognized by RCSB ({len(unresolved)}): {', '.join(sorted(unresolved))}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
