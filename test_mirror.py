#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31", "pymupdf>=1.24"]
# ///
"""Offline tests for mendeley_mirror.py -- pure functions + a stubbed API."""
import copy, json, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import mendeley_mirror as mm

DOCS = [
    {   # ordinary journal article, TeX-hostile title, accented author, 100% fields
        "id": "d1", "created": "2020-01-01T00:00:00Z", "type": "journal",
        "title": "Yield & selectivity of 50% H_2SO_4 catalysis in CO$_2$ streams #1",
        "authors": [{"first_name": "Jörg", "last_name": "Müller"},
                    {"first_name": "A. B.", "last_name": "Smith"}],
        "year": 2020, "source": "AIChE Journal", "volume": "66", "issue": "4",
        "pages": "1234-1245", "identifiers": {"doi": "10.1002/aic.16789", "issn": "0001-1541"},
        "abstract": "We show 90% conversion at 5% loading.",
        "keywords": ["catalysis", "CO2"], "websites": ["https://example.org/a"],
    },
    {   # book chapter
        "id": "d2", "created": "2021-02-01T00:00:00Z", "type": "book_section",
        "title": "The transport of momentum", "authors": [{"first_name": "R B", "last_name": "Bird"}],
        "year": 2021, "source": "Transport Phenomena", "publisher": "Wiley",
        "city": "New York", "pages": "10-40", "identifiers": {"isbn": "9780470115398"},
    },
    {   # collides with d2 on surname+year+title word -> must get a suffix
        "id": "d3", "created": "2021-03-01T00:00:00Z", "type": "conference_proceedings",
        "title": "The transport of momentum, revisited",
        "authors": [{"first_name": "R B", "last_name": "Bird"}],
        "year": 2021, "source": "AIChE Annual Meeting",
    },
    {   # thesis with institution
        "id": "d4", "created": "2019-05-01T00:00:00Z", "type": "thesis",
        "title": "A study of packed beds", "authors": [{"last_name": "Nguyen", "first_name": "Linh"}],
        "year": 2019, "institution": "Drexel University",
    },
    {   # web page, corporate author, no year
        "id": "d5", "created": "2022-06-01T00:00:00Z", "type": "web_page",
        "title": "Steam tables", "authors": [{"name": "NIST"}],
        "websites": ["https://webbook.nist.gov"],
    },
    {   # degenerate: no author, no year, no title
        "id": "d6", "created": "2023-06-01T00:00:00Z", "type": "generic",
    },
    {   # publisher unicode: U+2010 in the name, U+2013 page range, curly quotes,
        # a non-breaking space and a zero-width space -- all of it ASCII-lookalike
        "id": "d8", "created": "2024-07-01T00:00:00Z", "type": "journal",
        "title": "Curing at 50\u00b0C \u00b1 2: the \u201cinert\u201d case \u2013 a 5\u2009min study\u200b",
        "authors": [{"first_name": "Jean\u2010Pierre", "last_name": "Pascault"}],
        "year": 2024, "source": "J.\u00a0Appl. Polym. Sci.", "volume": "49",
        "pages": "1441\u20131452", "identifiers": {"doi": "10.1002/app.1993.070490812"},
    },
]

ANNOTATIONS = [
    {"id": "a1", "document_id": "d1", "type": "note", "text": "Compare with Fig. 4 of Bird.",
     "positions": [{"page": 3, "top_left": {"x": 10, "y": 100}}]},
    {"id": "a2", "document_id": "d1", "type": "highlight", "text": "conversion plateaus above 5 bar",
     "positions": [{"page": 2, "top_left": {"x": 10, "y": 50}}]},
    {"id": "a3", "document_id": "d1", "type": "highlight",  # no text -- the awkward case
     "positions": [{"page": 2, "top_left": {"x": 10, "y": 400}}]},
]

FOLDERS = [
    {"id": "f1", "name": "Substack"},
    {"id": "f2", "name": "week 3", "parent_id": "f1"},
]
FOLDER_DOCS = {"f1": ["d1", "d2"], "f2": ["d1"]}
FILES_BY_DOC = {"d1": [{"id": "x1", "mime_type": "application/pdf", "filehash": "h1"}]}

fails = []


import hashlib as _hashlib


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def main():
    tmp = Path(tempfile.mkdtemp())
    cfgdir = tmp / "cfg"
    cfgdir.mkdir()
    mm.config_dir = lambda: cfgdir  # keep the test out of the real config dir
    out = tmp / "out"
    out.mkdir()

    print("citation keys")
    keymap = mm.assign_citekeys(DOCS, cfgdir / "citekeys.json")
    print("   ", keymap)
    q = mm.qualify                       # ids are stored namespaced: 'mendeley:d1'
    check(keymap[q("d1")] == "Muller2020Yield", "accents folded, stopword-free title word")
    check(keymap[q("d2")] == "Bird2021Transport", "stopword 'the' skipped")
    check(keymap[q("d3")] == "Bird2021Transporta", "collision gets a suffix")
    check(keymap[q("d5")] == "NISTndSteam", f"corporate acronym keeps its case -> {keymap[q('d5')]}")
    check(len(set(keymap.values())) == len(DOCS), "all keys unique")
    check(all(k.startswith("mendeley:") for k in keymap),
          "every stored id names the backend it came from")

    # stability: re-running with a new document must not renumber existing keys
    more = DOCS + [{"id": "d7", "created": "2018-01-01T00:00:00Z", "type": "journal",
                    "title": "The transport of heat", "year": 2021,
                    "authors": [{"last_name": "Bird", "first_name": "R"}]}]
    keymap2 = mm.assign_citekeys(more, cfgdir / "citekeys.json")
    check(all(keymap2[k] == v for k, v in keymap.items()), "keys stable across runs")

    # Stability the other way: CORRECTING a field on a document already keyed must
    # not renumber it either. This is the contract, not an accident of ordering --
    # the live library holds Lordi2011Molecular over year = 2000 and
    # Pincus2002Excluded over year = 1976, keys frozen before the years were fixed
    # and 11 and 26 years adrift. A key is a handle; read the year off the field.
    corrected = copy.deepcopy(DOCS)
    corrected[0]["year"] = 1976
    corrected[0]["authors"] = [{"last_name": "Pincus", "first_name": "P"}]
    keymap3 = mm.assign_citekeys(corrected, cfgdir / "citekeys.json")
    check(keymap3[q("d1")] == "Muller2020Yield",
          "a corrected year and author do not renumber a key already assigned")
    check(len(keymap3) == len(keymap2), "and no second key is minted for the same id")

    print("\nidentifier namespaces (ROADMAP 1)")

    check(mm.qualify("abc") == "mendeley:abc", "a bare id gets this backend's name")
    check(mm.qualify(mm.qualify("abc")) == "mendeley:abc", "and qualifying twice is a no-op")
    check(mm.qualify("abc", "zotero") == "zotero:abc", "another backend names itself")
    check(mm.split_id("zotero:abc") == ("zotero", "abc"), "and splits back apart")
    check(mm.split_id("abc") == ("mendeley", "abc"),
          "a bare id reads as Mendeley's, which is what makes old files readable")
    # A colon alone is not a namespace, or a DOI-ish or URL-ish id would be eaten.
    check(mm.is_qualified("http://example.org/x") is False, "a URL is not a namespaced id")
    check(mm.split_id("http://example.org/x") == ("mendeley", "http://example.org/x"),
          "and survives a round trip unchanged")

    check(mm.local_id("mendeley:abc") == "abc", "this backend's id comes back raw")
    check(mm.local_id("zotero:abc") is None,
          "another backend's id resolves to nothing rather than to a wrong paper")
    check(mm.local_id("zotero:abc", "zotero") == "abc", "and to itself under its own backend")

    # The collision the namespace exists to prevent: the same raw id from two
    # services is two different papers.
    check(mm.qualify("X1", "mendeley") != mm.qualify("X1", "zotero"),
          "one raw id from two backends does not collide")

    migrated, n = mm.qualify_map({"a": "Key1", "b": "Key2"})
    check(migrated == {"mendeley:a": "Key1", "mendeley:b": "Key2"} and n == 2,
          "an old map migrates whole")
    again, n2 = mm.qualify_map(migrated)
    check(again == migrated and n2 == 0, "and migrating twice changes nothing")
    mixed, n3 = mm.qualify_map({"zotero:z": "Key3", "a": "Key1"})
    check(mixed == {"zotero:z": "Key3", "mendeley:a": "Key1"} and n3 == 1,
          "a map holding both backends migrates only what needs it")
    check(list(mixed) == ["zotero:z", "mendeley:a"], "and keeps its order, so the file diffs cleanly")

    # THE ACCEPTANCE CRITERION for the Zotero migration: a key already cited in a
    # manuscript, or naming a file under findings/, must still mean the same paper.
    premigration = tmp / "premigration.json"
    bare = {d["id"]: keymap[q(d["id"])] for d in DOCS}
    premigration.write_text(json.dumps(bare), encoding="utf-8")
    after = mm.assign_citekeys(DOCS, premigration)
    check(all(after[q(doc_id)] == key for doc_id, key in bare.items()),
          "every pre-namespacing key still resolves to the same paper")
    check(sorted(after.values()) == sorted(bare.values()),
          "and no key was added, dropped or renumbered by the migration")

    # Adding a second backend afterwards must not disturb what is already assigned.
    with_zot = dict(after)
    with_zot["zotero:ZZZ"] = "Somebody2030New"
    premigration.write_text(json.dumps(with_zot), encoding="utf-8")
    after2 = mm.assign_citekeys(DOCS, premigration)
    check(all(after2[q(doc_id)] == key for doc_id, key in bare.items()),
          "a second backend's entries do not renumber the first's")
    check(after2["zotero:ZZZ"] == "Somebody2030New", "and are themselves left alone")

    # But `taken` is built from VALUES and is deliberately namespace-blind, so a
    # citekey spoken for by EITHER half is spoken for. That is what stops a Zotero
    # record being handed a key a Mendeley record already owns while both halves
    # describe the same corpus -- and it is also why a half-finished deletion is
    # worse than none: dropping the mendeley: entry alone does not release the key.
    solo = tmp / "solo.json"
    twin = {"id": "d9", "created": "2030-01-01T00:00:00Z", "type": "journal",
            "title": "New results", "year": 2030,
            "authors": [{"last_name": "Somebody", "first_name": "A"}]}
    solo.write_text(json.dumps({"zotero:ZZZ": "Somebody2030New"}), encoding="utf-8")
    check(mm.assign_citekeys([twin], solo)[q("d9")] == "Somebody2030Newa",
          "a key held only by the zotero half still blocks a new mendeley key")
    solo.write_text(json.dumps({}), encoding="utf-8")
    check(mm.assign_citekeys([twin], solo)[q("d9")] == "Somebody2030New",
          "and the same document takes the unsuffixed key when nothing holds it")

    # state.json is keyed by FILE id and gets the same treatment. The property that
    # matters is that migration must not make the extractor think a file is new:
    # 2,700 re-extractions is what "just re-run it" would cost.
    old_state = {"files": {"f1": {"filehash": "abc", "status": "ok"}}}
    known, moved = mm.qualify_map(old_state["files"])
    check(moved == 1 and known["mendeley:f1"]["filehash"] == "abc",
          "attachment state migrates with its filehash intact")
    check(known.get(mm.qualify("f1"), {}).get("filehash") == "abc",
          "and is still found by the id the API reports, so nothing re-extracts")

    print("\nzotero_migrate: pairing citation keys with a second backend (ROADMAP 1)")

    import zotero_migrate as zmig

    check(zmig.norm_doi("https://doi.org/10.1/A") == "10.1/a", "a DOI URL reduces to the DOI")
    check(zmig.norm_doi("doi: 10.1/a.") == "10.1/a", "as does a doi: prefix with trailing stop")
    check(zmig.norm_doi(None) == "", "and a missing DOI is empty, not an error")

    # The normalization that did the real work. Zotero's Mendeley import writes
    # &#8208; where the BibTeX has a plain hyphen; strip punctuation before
    # entities and "8208" survives as a word, so two identical titles differ.
    check(zmig.norm_title("A model of an&#8208;paraffinic liquid")
          == zmig.norm_title("A model of an-paraffinic liquid"),
          "an HTML entity and the character it stands for normalize alike")
    check("8208" not in zmig.norm_title("an&#8208;paraffinic"),
          "and the entity's digits do not survive as a word")
    check(zmig.norm_title(r"2.4 &angst; resolution") == zmig.norm_title("2.4 Angstrom resolution")
          or "angst" not in zmig.norm_title("2.4 &angst; resolution"),
          "a named entity is dropped rather than read as letters")
    check(zmig.norm_title("{Title}") == "title", "brace protection does not survive normalization")

    # parse_bib reads what write_bibtex writes; the hard part is braces INSIDE
    # values -- protected titles and abstracts that contain them.
    sample = (
        "@article{Key2020Sample,\n"
        "  author    = {Doe, J.},\n"
        "  title     = {{A {braced} title}},\n"
        "  abstract  = {Contains {nested} braces and a brace-balanced {clause}},\n"
        "  doi       = {10.1/x},\n"
        "}\n"
    )
    ents = zmig.parse_bib(sample)
    check(len(ents) == 1 and ents[0][0] == "Key2020Sample", "one entry, keyed correctly")
    check(ents[0][2]["title"] == "{A {braced} title}", "a brace-protected title comes back whole")
    check(ents[0][2]["doi"] == "10.1/x", "and a later field is still found after it")
    check("{clause}" in ents[0][2]["abstract"], "braces inside an abstract do not end the field")

    def _row(ident, doi, title, year):
        return zmig.Row(ident, zmig.norm_doi(doi), zmig.norm_title(title),
                        zmig.year_of(year), title)

    M = [
        _row("Doe2000Alpha",   "10.1/a", "First paper", "2000"),
        _row("Roe2001Unique",  "",       "A quite unique title", "2001"),
        _row("Poe2002Dup",     "",       "Duplicated paper", "2002"),
        _row("Poe2002Dupa",    "",       "Duplicated paper", "2002"),
        _row("Coe2003Fuzzy",   "",       r"Molecular picture of folding of a small \$\textbackslash{}alpha\$/\$\textbackslash{}beta\$ protein", "2003"),
        _row("Zoe2004Orphan",  "",       "Nothing matches this", "2004"),
        _row("Hyp1980Model",   "",       "A computer model of an-paraffinic liquid", "1980"),
        _row("Lon2006Missing", "",       "Only one copy survived", "2006"),
        _row("Lon2006Missinga", "",      "Only one copy survived", "2006"),
    ]
    Z = [
        _row("Z1", "https://doi.org/10.1/A", "Completely different title", "2000-01-01"),
        _row("Z2", "", "A quite unique title", "2001-05-01"),
        _row("Z3", "", "Duplicated paper", "2002"),
        _row("Z4", "", "Duplicated paper", "2002"),
        _row("Z5", "", r"Molecular picture of folding of a small $\alpha$/$\beta$ protein", "2003"),
        _row("Z7", "", "A computer model of an&#8208;paraffinic liquid", "1980"),
        _row("Z8", "", "Only one copy survived", "2006"),
        _row("Z9", "", "Unclaimed extra", "2005"),
    ]
    pairs, unmatched, unclaimed, stats = zmig.match(M, Z)

    check(pairs.get("Doe2000Alpha") == "Z1",
          "a DOI match wins even when the titles disagree entirely")
    check(pairs.get("Roe2001Unique") == "Z2", "an exact title+year pairs")
    check(pairs.get("Hyp1980Model") == "Z7",
          "and so does a title that only agrees after entity normalization")
    check(pairs.get("Coe2003Fuzzy") == "Z5",
          "a title differing only in surviving LaTeX matches fuzzily")
    check(stats["fuzzy title"] == 1, "and the fuzzy pass is credited for exactly that one")

    # Two-sided, because a threshold tested only from above is a threshold nobody
    # has measured. The pair above scores 0.8952; the same difference in a shorter
    # title scores 0.8254, and must be refused.
    short = [_row("Sho2003Fuzzy", "", r"Folding of a small \$\textbackslash{}alpha\$/\$\textbackslash{}beta\$ protein", "2003")]
    shortz = [_row("S5", "", r"Folding of a small $\alpha$/$\beta$ protein", "2003")]
    p4, u4, _, _ = zmig.match(short, shortz)
    check(not p4 and len(u4) == 1,
          "the same difference in a shorter title falls below the threshold and is refused")
    check({pairs.get("Poe2002Dup"), pairs.get("Poe2002Dupa")} == {"Z3", "Z4"},
          "two indistinguishable copies pair with the two on the other side")

    # What it must NOT do.
    check("Zoe2004Orphan" in [m.ident for m in unmatched],
          "a reference with nothing to match is reported, not attached to the nearest thing")
    check({"Lon2006Missing", "Lon2006Missinga"} <= {m.ident for m in unmatched},
          "and an UNEQUAL duplicate group is reported whole rather than half-guessed")
    check("Z8" in {z.ident for z in unclaimed},
          "the lone survivor on the other side stays unclaimed, so the loss is visible")
    check("Z9" in {z.ident for z in unclaimed}, "an item nothing points at is reported too")

    check(len(set(pairs.values())) == len(pairs),
          "no Zotero item is claimed twice (the map is a bijection)")
    check(set(pairs) & {m.ident for m in unmatched} == set(),
          "and nothing is both matched and unmatched")

    # The margin: a near-tie is not a match. Two candidates equally close to the
    # query must leave it unmatched rather than let a thousandth of a ratio pick.
    q = [_row("Tie2010Paper", "", "the quick brown fox jumps over", "2010")]
    tied = [_row("T1", "", "the quick brown fox jumps over it", "2010"),
            _row("T2", "", "the quick brown fox jumps over us", "2010")]
    p2, u2, _, _ = zmig.match(q, tied)
    check(not p2 and len(u2) == 1,
          "two equally-near titles leave the reference unmatched rather than guessing")

    # A single near candidate below the threshold is still refused.
    far = [_row("F1", "", "an entirely unrelated piece of writing", "2010")]
    p3, u3, _, _ = zmig.match(q, far)
    check(not p3 and len(u3) == 1, "and a candidate below the threshold is not a match")

    print("\nbibtex")
    mm.write_bibtex(DOCS, keymap, out, include_abstract=True)
    bib = (out / "library.bib").read_text(encoding="utf-8")
    check("@article{Muller2020Yield," in bib, "journal -> @article")
    check("@incollection{Bird2021Transport," in bib, "book_section -> @incollection")
    check("@phdthesis" in bib and "school" in bib, "thesis -> @phdthesis with school")
    check(r"\&" in bib and r"\%" in bib and r"\_" in bib and r"\$" in bib and r"\#" in bib,
          "TeX specials escaped")
    check("{1234--1245}" in bib, "page range converted to --")
    check("Müller, Jörg and Smith, A. B." in bib, "author list formatted")
    check(bib.count("\n@") == len(DOCS), "one entry per document")

    print("\nthe venue survives every entry type (ROADMAP 7)")

    def entry(**kw):
        d = {"id": "x", "title": "T", "authors": [{"last_name": "Doe"}], "year": 2000}
        d.update(kw)
        return mm.bib_entry(d, "Doe2000T", include_abstract=False)

    # The types that already had a home for it keep using it, and gain no note.
    e = entry(type="journal", source="AIChE Journal")
    check("journal   = {AIChE Journal}" in e or "journal = {AIChE Journal}" in e,
          "an article still puts source in journal")
    check("note" not in e, "and gains no note, so 2,660 bibliographies do not change")
    e = entry(type="book_section", source="Transport Phenomena")
    check("booktitle" in e and "note" not in e, "a book section still uses booktitle")

    # The four that dropped it on the floor. 66 records here, 28 of them holding
    # volume/pages/DOI and no venue at all -- complete-looking and uncitable.
    # NB: not `bib` as the loop name -- this script is flat, and `bib` holds the
    # generated BibTeX that later checks read.
    for kind, bibtype in (("report", "techreport"), ("patent", "misc"),
                          ("web_page", "misc"), ("book", "book")):
        e = entry(type=kind, source="NRL Memorandum Report 6848 (DTIC ADA239276)")
        check(f"@{bibtype}{{" in e and "NRL Memorandum Report 6848 (DTIC ADA239276)" in e,
              f"a {kind} keeps its venue, in note ({bibtype})")

    # This is the case <out>/CLAUDE.md's grey-literature procedure creates: the
    # accession number is put in `source` because no identifier field exists.
    e = entry(type="report", source="DTIC ADA239276", volume="12", pages="1-9")
    check("note" in e and "DTIC ADA239276" in e,
          "an accession number filed per the documented procedure reaches the .bib")

    # A thesis must not claim a doctorate the source never stated.
    e = entry(type="thesis", institution="Johannes Gutenberg University")
    check("@phdthesis{" in e, "a thesis is still @phdthesis, which is what styles know")
    check("type" in e and "{Thesis}" in e,
          "but carries type = {Thesis}, so no style prints a degree Mendeley never gave")
    check("school" in e, "and institution still lands in school")

    # Both notes coexist rather than one overwriting the other.
    e = mm.bib_entry({"id": "d9", "type": "generic", "source": "Some venue"},
                     "Anonnd", include_abstract=False)
    check("Some venue" in e and "Untitled Mendeley record" in e,
          "an untitled record with a venue reports both, not whichever was added last")

    # No source, no note: the fix must not add an empty field to every entry.
    e = entry(type="report")
    check("note" not in e, "a record with no source gains nothing")
    e = entry(type="report", source="")
    check("note" not in e, "and neither does one whose source is empty")

    print("\nunicode punctuation (non-UTF-8 LaTeX safety)")
    check(mm.tex_escape("Jean\u2010Pierre") == "Jean-Pierre", "U+2010 hyphen -> ASCII hyphen")
    check(mm.tex_escape("Jean\u2011Pierre") == "Jean-Pierre", "U+2011 non-breaking hyphen -> ASCII")
    check(mm.tex_escape("a \u2013 b") == "a -- b", "en dash -> --")
    check(mm.tex_escape("a \u2014 b") == "a --- b", "em dash -> ---")
    check(mm.tex_escape("\u201cq\u201d") == "``q''", "curly double quotes -> TeX quotes")
    check(mm.tex_escape("\u2018q\u2019") == "`q'", "curly single quotes -> TeX quotes")
    check(mm.tex_escape("a\u00a0b") == "a b", "non-breaking space -> ordinary space")
    check(mm.tex_escape("a\u200bb") == "ab", "zero-width space dropped")
    check(mm.tex_escape("50\u00b0C") == r"50\textdegree{}C", "degree sign -> \\textdegree")
    check(mm.tex_escape("\u00b1 2") == "$\\pm$ 2", "plus-minus -> math mode")
    # the replacement must not be escaped a second time
    check("textbackslash" not in mm.tex_escape("50\u00b0C"), "LaTeX replacement not re-escaped")
    # accented letters are legitimate UTF-8 and must survive untouched
    check(mm.tex_escape("M\u00fcller, J\u00f6rg") == "M\u00fcller, J\u00f6rg", "accents left alone")
    # TeX specials still escaped alongside the new rules
    check(mm.tex_escape("50% \u2013 a_b") == r"50\% -- a\_b", "specials still escaped")

    check(mm.format_pages("1441\u20131452") == "1441--1452", "en dash page range -> --")
    check(mm.format_pages("1441-1452") == "1441--1452", "hyphen page range -> --")
    check(mm.format_pages("1441--1452") == "1441--1452", "already-correct page range unchanged")

    d8 = bib[bib.index("@article{Pascault2024"):]
    d8 = d8[:d8.index("\n@")] if "\n@" in d8 else d8
    check("Jean-Pierre" in d8, "U+2010 gone from the author field in library.bib")
    check("{1441--1452}" in d8, "en dash page range fixed in library.bib")
    check(not any(ord(c) > 127 for c in d8), f"entry is pure ASCII -> {[c for c in d8 if ord(c) > 127]}")
    check("title    = {{Yield" in bib, "title double-braced for case protection")
    check("author = {{NIST}}" in bib, "corporate author double-braced")
    check(bib.count("institution") == 0, "thesis uses school, not institution")
    check("Untitled Mendeley record" in bib and "key  = {AnonndUntitled}" in bib,
          "degenerate record still yields a sortable, traceable entry")

    print("\nbibtex round-trip (real bibtex binary)")
    tex = tmp / "t.tex"
    cites = ",".join(keymap[mm.qualify(d["id"])] for d in DOCS)
    tex.write_text(
        "\\documentclass{article}\\usepackage[utf8]{inputenc}\\begin{document}\n"
        f"\\nocite{{{cites}}}\n\\bibliographystyle{{plain}}\\bibliography{{library}}\n"
        "\\end{document}\n", encoding="utf-8")
    shutil.copy(out / "library.bib", tmp / "library.bib")
    subprocess.run(["pdflatex", "-interaction=nonstopmode", "t.tex"], cwd=tmp, capture_output=True)
    r = subprocess.run(["bibtex", "t"], cwd=tmp, capture_output=True, text=True)
    blg = (tmp / "t.blg").read_text(errors="replace")
    check("error message" not in blg.lower() and "I was expecting" not in blg,
          "bibtex parses library.bib without errors")
    warns = [l for l in blg.splitlines() if l.startswith("Warning--")]
    print("    bibtex warnings:", len(warns))
    for w in warns[:6]:
        print("     ", w)
    bbl = (tmp / "t.bbl").read_text(errors="replace")
    check(bbl.count("\\bibitem") == len(DOCS), f"all {len(DOCS)} entries resolved ({bbl.count(chr(92)+'bibitem')} found)")

    print("\nindex, folders, annotations")
    ann_by_doc = {}
    for a in ANNOTATIONS:
        ann_by_doc.setdefault(a["document_id"], []).append(a)
    mm.write_index(DOCS, keymap, FILES_BY_DOC, ann_by_doc, out)
    idx = (out / "index.md").read_text(encoding="utf-8")
    check(idx.count("\n|") == len(DOCS) + 2, "index has a row per reference")
    check("10.1002/aic.16789" in idx, "doi in index")

    mm.write_folders(FOLDERS, FOLDER_DOCS, keymap, out)
    folders = json.loads((out / "folders.json").read_text())
    check(folders["Substack"] == ["Bird2021Transport", "Muller2020Yield"], "folder maps to sorted keys")
    check("Substack/week 3" in folders, "nested folder path built")

    md = mm.annotation_markdown(DOCS[0], keymap[mm.qualify("d1")], ANNOTATIONS)
    check(md.index("p. 2") < md.index("p. 3"), "annotations sorted by page")
    check("> conversion plateaus" in md, "highlight text quoted")
    check("*(highlight, p. 2)*" in md, "textless highlight still recorded")
    check("Jörg Müller" in md, "author names in annotation header")

    print("\ntext extraction")
    import pymupdf
    doc = pymupdf.open()
    p1 = doc.new_page()
    p1.insert_text((72, 100), "Packed bed reactors show high cataly-")
    p1.insert_text((72, 115), "sis efficiency under steady flow.")
    p1.insert_textbox(pymupdf.Rect(72, 140, 400, 700),
                      ("Conversion increased with temperature across the range "
                       "studied here, and selectivity fell. " * 6), fontsize=9)
    p2 = doc.new_page()
    p2.insert_textbox(pymupdf.Rect(72, 90, 400, 700),
                      ("The second page discusses transport limitations in "
                       "detail and at some length. " * 8), fontsize=9)
    pdf_bytes = doc.tobytes()
    doc.close()

    body, pages, chars, content, note = mm.extract_pdf_text(pdf_bytes)
    check(pages == 2, f"page count read ({pages})")
    check("<!-- p. 1 -->" in body and "<!-- p. 2 -->" in body, "page markers emitted")
    check("catalysis efficiency" in body, "hyphenation across a line break repaired")
    check(chars > 500, f"substantive text extracted ({chars} chars)")

    scan = pymupdf.open()
    scan.new_page()  # a page with no text layer, as a scan would be
    empty_body, empty_pages, empty_chars, empty_content, _ = mm.extract_pdf_text(scan.tobytes())
    scan.close()
    check(empty_chars < mm.MIN_CHARS_PER_PAGE * max(empty_pages, 1),
          f"textless page falls under the scan threshold ({empty_chars} chars)")

    # A scan whose only text is a stamp repeated on every page. Real case: a
    # ProQuest copy of Lin & Rye 2006 carried exactly 103 characters on each of
    # 29 pages, above MIN_CHARS_PER_PAGE, so the raw count called it readable.
    stamp = ("Reproduced with permission of the copyright owner.  "
             "Further reproduction prohibited without permission.")
    stamped = pymupdf.open()
    for _ in range(29):
        page = stamped.new_page()
        page.insert_text((72, 72), stamp, fontsize=9)
    st_body, st_pages, st_chars, st_content, _ = mm.extract_pdf_text(stamped.tobytes())
    stamped.close()
    check(st_chars >= mm.MIN_CHARS_PER_PAGE * st_pages,
          f"stamped scan clears the raw threshold, as the real one did ({st_chars})")
    check(st_content == 0, f"stamp is recognised as boilerplate, not content ({st_content})")

    # The same logic must not strip a real paper down: a running head repeats on
    # every page too, and removing it should leave the body untouched.
    real = pymupdf.open()
    for n in range(6):
        page = real.new_page()
        page.insert_text((72, 50), "J. Chem. Phys. 152, 044105 (2020)", fontsize=8)
        page.insert_textbox(pymupdf.Rect(72, 90, 420, 700),
                            (f"Section {n} discusses the integrator in detail. " * 12),
                            fontsize=9)
    r_body, r_pages, r_chars, r_content, _ = mm.extract_pdf_text(real.tobytes())
    real.close()
    check(r_content > mm.MIN_CHARS_PER_PAGE * r_pages,
          f"paper with a running head stays readable ({r_content} content chars)")
    check(r_content < r_chars, "the running head itself was discounted")

    check(note == "", "a clean paper carries no garble note")

    # A broken font encoding reads as punctuation soup of normal length. Real
    # case: Allington et al. 2001 came out 2-14% alphanumeric on every page with
    # PyMuPDF while pdftotext read 87-97%, and the length checks passed it.
    soup = ",     , , ,   ,       ?@ ,    , ,      ,    :D;  , 4   . " * 30
    check(mm.page_is_garbled(soup), "punctuation soup is judged garbled")
    check(not mm.page_is_garbled("Conversion rose with temperature. " * 20),
          "prose is not judged garbled")
    check(not mm.page_is_garbled(("0.92 244 +/- 2 | 0.80 168 +/- 1 | " * 20)),
          "a numeric table is not judged garbled")
    check(not mm.page_is_garbled("シアネート硬化エポキシ樹脂の熱機械特性に関する実験的評価。" * 10),
          "Japanese text is not judged garbled")
    check(not mm.page_is_garbled(", ; :"), "a short page is not judged at all")

    # Exercise the repair path without depending on a real broken font: swap in
    # an extractor seam. The page the "PDF library" garbles comes back from the
    # fallback; the page nobody can read is dropped, marker and all.
    orig_get = pymupdf.Page.get_text
    def soup_page_two(self, *a, **k):
        return soup if self.number == 1 else orig_get(self, *a, **k)
    pymupdf.Page.get_text = soup_page_two
    try:
        good = "The fallback read this page properly and at length. " * 10
        b1, p1, c1, k1, n1 = mm.extract_pdf_text(pdf_bytes, fallback=lambda d: ["", good])
        check("fallback read this page" in b1 and ",   , ," not in b1,
              "a garbled page is replaced by the fallback's reading")
        check("re-read with pdftotext" in n1, f"repair is noted ({n1!r})")
        b2, p2, c2, k2, n2 = mm.extract_pdf_text(pdf_bytes, fallback=lambda d: None)
        check("<!-- p. 2 -->" not in b2 and "<!-- p. 1 -->" in b2,
              "an unrepairable garbled page is dropped, marker and all")
        check("dropped" in n2, f"drop is noted ({n2!r})")
        b3, *_, n3 = mm.extract_pdf_text(pdf_bytes, fallback=lambda d: ["", soup])
        check("<!-- p. 2 -->" not in b3 and "dropped" in n3,
              "a fallback that is garbled too does not rescue the page")
    finally:
        pymupdf.Page.get_text = orig_get

    # Short documents are exempt: across one or two pages "repeated on most
    # pages" is meaningless, and a note should not be judged on it.
    two = ["Identical text on both pages."] * 2
    check(mm.content_chars(two) == sum(len(t) for t in two),
          "two-page document is not subjected to the repeat test")
    check(mm.content_chars([]) == 0, "no pages means no content")

    # OCR provenance must be legible in the file itself. An unmarked OCR extract
    # is indistinguishable later from the words off the page, and that confusion
    # is the whole thing text/ exists to prevent.
    ocr_md = mm.text_document(DOCS[0], "Muller2020Yield", body, pages, chars, ocr=True)
    check("ocr: true" in ocr_md, "OCR extract declares itself in the front matter")
    check("Read by OCR, not extracted" in ocr_md, "OCR extract carries a visible banner")
    check("get_pdf.py Muller2020Yield" in ocr_md,
          "banner names the command that shows the rendered page")

    md = mm.text_document(DOCS[0], "Muller2020Yield", body, pages, chars)
    check("ocr: false" in md, "an ordinary extract says so rather than staying silent")
    check("Read by OCR" not in md, "no OCR banner on a real text layer")
    check(md.startswith("---\ncitekey: Muller2020Yield"), "front matter leads the file")
    check('doi: "10.1002/aic.16789"' in md, "doi in front matter for citation")
    check("Equations and table structure do not survive" in md,
          "file warns about what extraction loses")
    check("<!-- p. 2 -->" in md, "body carried through with page markers")

    rows = [{"key": "Scan1999Old", "status": "no-text", "title": "An old scan",
             "detail": "3 characters across 12 pages"},
            {"key": "Bad2001File", "status": "failed", "title": "Broken", "detail": "boom"}]
    mm.write_extraction_report(rows, out, extracted=41)
    rep = (out / "extraction-report.md").read_text(encoding="utf-8")
    check("extracted this run: 41" in rep, "report counts extractions")
    check("Scan1999Old" in rep and "Bad2001File" in rep, "report names both failure kinds")
    check("invisible to any text search" in rep, "report says what the gap means")

    print("\nre-using PDFs already downloaded")
    h_out = tmp / "harvest"
    (h_out / "pdf").mkdir(parents=True)
    (h_out / "pdf" / "Muller2020Yield.pdf").write_bytes(pdf_bytes)

    class NoNetwork:
        def get(self, *a, **kw):
            raise AssertionError("downloaded a PDF that was already on disk")

    st = {}
    fetched, skipped, failed, rep_rows = mm.harvest_attachments(
        NoNetwork(), {"d1": [{"id": "x1", "mime_type": "application/pdf", "filehash": "h1"}]},
        {mm.qualify("d1"): "Muller2020Yield"}, {"d1": DOCS[0]}, h_out, st, "text")
    check(fetched == 1 and failed == 0, f"local PDF extracted without a download ({fetched=}, {failed=})")
    check((h_out / "text" / "Muller2020Yield.md").exists(), "text file written")
    # These two asserted the opposite until 2026-09-29, which is how the defect
    # lasted: the test staged a PDF on disk, ran the mode the hourly timer runs,
    # and required the file to be gone afterwards. That is the erosion written
    # down as a specification. What this block is really for -- re-using a local
    # PDF instead of re-downloading it -- is still proved by NoNetwork above.
    check((h_out / "pdf" / "Muller2020Yield.pdf").exists(),
          "a re-used PDF is still there afterwards: it was not this run's to delete")
    check((h_out / "pdf").exists(),
          "and pdf/ is not removed out from under an archive")
    check(st["files"][mm.qualify("x1")]["status"] == "ok", "state records the extraction")

    # An OCR'd attachment is skipped on every later refresh, because its extract
    # exists. It must still be listed under "Read by OCR" -- on 11 Sep the first
    # hourly refresh after the OCR run emptied that section for all 108 papers,
    # because the skip branch only re-listed no-text and failed entries.
    st["files"][mm.qualify("x1")].update(status="ocr", detail="read by OCR", pages=2, chars=1234)
    fetched, skipped, failed, rep_rows = mm.harvest_attachments(
        NoNetwork(), {"d1": [{"id": "x1", "mime_type": "application/pdf", "filehash": "h1"}]},
        {mm.qualify("d1"): "Muller2020Yield"}, {"d1": DOCS[0]}, h_out, st, "text")
    check(skipped == 1 and fetched == 0, "an OCR'd attachment is not re-read on a plain refresh")
    ocr_rows = [r for r in rep_rows if r["status"] == "ocr"]
    check(len(ocr_rows) == 1 and ocr_rows[0]["key"] == "Muller2020Yield",
          "a skipped OCR'd attachment stays in the report")
    check(ocr_rows and ocr_rows[0]["detail"] == "1234 characters across 2 pages",
          "its detail matches what the OCR run itself reported")
    mm.write_extraction_report(rep_rows, h_out, extracted=0)
    h_rep = (h_out / "extraction-report.md").read_text(encoding="utf-8")
    check("read by OCR: 1" in h_rep and "## Read by OCR" in h_rep,
          "report still counts and lists OCR'd papers after a refresh that read nothing")

    print("\npagination")
    check(mm._next_link('<https://api.mendeley.com/documents?marker=abc>; rel="next"')
          == "https://api.mendeley.com/documents?marker=abc", "next link parsed")
    check(mm._next_link('<https://a/1>; rel="last", <https://a/2>; rel="next"') == "https://a/2",
          "next link found among several")
    check(mm._next_link('<https://a/1>; rel="last"') is None, "no next link -> None")

    class FakeResp:
        def __init__(self, payload, link="", status=200):
            self._p, self.headers, self.status_code = payload, {"Link": link}, status
            self.ok, self.url, self.text = status < 400, "http://x", ""
        def json(self): return self._p
        def raise_for_status(self): pass

    pages = [FakeResp([{"id": "a"}], '<https://api.mendeley.com/documents?marker=2>; rel="next"'),
             FakeResp([{"id": "b"}])]
    client = mm.Mendeley.__new__(mm.Mendeley)
    client.get = lambda url, accept=None, params=None, **kw: pages.pop(0)
    check([d["id"] for d in client.paged("/documents", "documents")] == ["a", "b"],
          "paged() follows next links and concatenates")

    # annotations must not be requested at the documents page size (Mendeley 400s)
    seen = []

    def record(url, accept=None, params=None, **kw):
        seen.append(dict(params or {}))
        return FakeResp([{"id": "z"}])

    client.get = record
    client.paged("/annotations", "annotations", quiet=True)
    check(seen[0].get("limit") == 200, f"annotations requested at limit=200 (got {seen[0]})")
    seen.clear()
    client.paged("/documents", "documents", quiet=True)
    check(seen[0].get("limit") == 500, "documents still requested at limit=500")

    # a 400 on the first page should back the page size off, not kill the run
    responses = [FakeResp([], status=400), FakeResp([{"id": "q"}])]
    tried = []

    def flaky(url, accept=None, params=None, **kw):
        tried.append(dict(params or {}))
        return responses.pop(0)

    client.get = flaky
    got = client.paged("/annotations", "annotations", quiet=True)
    check([d["id"] for d in got] == ["q"] and tried[1]["limit"] == 50,
          f"400 backs page size off and retries (tried {[t.get('limit') for t in tried]})")

    print("\nscheduled-run bookkeeping")
    import datetime as _dt
    md = tmp / "sched" / ".mirror"
    md.mkdir(parents=True)
    sout = md.parent
    lock = mm.acquire_lock(md)
    check(lock is not None and lock.exists(), "first run takes the lock")
    check(lock.name == f"run.lock.{mm.host_name()}", "the lock is named per host")
    check(mm.acquire_lock(md) is None, "a second concurrent run backs off")
    os.utime(lock, (0, 0))  # pretend it was left behind days ago
    check(mm.acquire_lock(md) is None,
          "a live run keeps its lock however old -- the first full run takes hours")

    # a lock whose process is gone is stale at any age: that is the failure that
    # blocked every run for 9.5 h, a dead pid behind a lock only age could clear.
    dead = subprocess.Popen([sys.executable, "-c", ""])
    dead.wait()
    lock.write_text(json.dumps({"pid": dead.pid, "host": mm.host_name(),
                                "started": "2026-01-01T00:00:00+00:00"}),
                    encoding="utf-8")
    if mm._pid_alive(dead.pid):
        check(True, "(pid reused; skipping the dead-pid takeover case)")
    else:
        check(mm.acquire_lock(md) is not None,
              "a lock from a dead process is taken over immediately")

    # another machine's lock is advisory: a synced folder cannot carry a mutex,
    # and treating it as binding is what let one stale file stop every host.
    lock.unlink(missing_ok=True)  # the takeover above left our own live lock
    foreign = md / "run.lock.othermachine"
    foreign.write_text(json.dumps({"pid": 1, "host": "othermachine",
                                   "started": "2026-01-01T00:00:00+00:00"}),
                       encoding="utf-8")
    check(mm.acquire_lock(md) is not None, "another host's lock does not block us")
    check(foreign.exists(), "and we leave that host's lock alone")

    lock.unlink(missing_ok=True)
    legacy = md / "run.lock"
    legacy.write_text("{}", encoding="utf-8")
    check(mm.acquire_lock(md) is not None and not legacy.exists(),
          "a shared run.lock from an older version is cleared")

    t0 = _dt.datetime.now(_dt.timezone.utc)
    mm.write_status(sout, True, t0)
    st = (sout / "mirror-status.md").read_text(encoding="utf-8")
    check("**ok**" in st, "successful run reports ok")
    ok_stamp = re.search(r"last successful run: (.+)", st).group(1)

    mm.write_status(sout, False, t0, "HTTPError: 500 from /documents")
    st = (sout / "mirror-status.md").read_text(encoding="utf-8")
    check("**FAILED" in st and "HTTPError" in st, "failed run reports the error")
    check(f"last successful run: {ok_stamp}" in st,
          "a failure keeps the earlier success timestamp, not 'never'")
    check("possibly stale" in st, "failure tells the reader the folder may be stale")

    print("\ntelling a dead login apart from a dropped connection (ROADMAP 2)")

    # The distinction that matters: a rejected login never clears by itself and is
    # the signal to migrate; a dropped connection usually does clear.
    check(mm.classify_failure("Refresh token rejected; re-authorizing in the browser.") == "auth",
          "a rejected refresh token is an auth failure")
    check(mm.classify_failure("ConnectionError: Max retries exceeded with url: /documents") == "network",
          "a dropped connection is a network failure")
    check(mm.classify_failure("KeyboardInterrupt: ") == "interrupted",
          "Ctrl-C is neither")

    # The trap this ordering exists for. The message names the TOKEN endpoint, so a
    # naive keyword match calls it authentication and sends a person to log in over
    # a connection that is not there.
    check(mm.classify_failure(
        "Could not reach https://api.mendeley.com/oauth/token: ConnectionError") == "network",
        "failing to REACH the token endpoint is a network failure, not a dead login")

    # Messages the tool actually emits, taken from the tool rather than retyped
    # here -- a classifier tested only against invented strings is a classifier
    # tested against nothing.
    mm.NONINTERACTIVE = True
    try:
        mm.require_interactive("Browser authorization")
        real_exit = ""
    except SystemExit as exc:
        real_exit = str(exc)
    mm.NONINTERACTIVE = False
    check(mm.classify_failure(real_exit) == "auth",
          "the real scheduled-run message classifies as auth, not unknown")
    src_mm = Path("mendeley_mirror.py").read_text(encoding="utf-8")
    check("No documents returned" in src_mm
          and mm.classify_failure("No documents returned. If your library is not "
                                  "empty, try --reauth.") == "auth",
          "an empty library from a non-empty account routes to the auth advice")

    # The streak, which is the whole point of item 2: 'failed once' and 'has not
    # worked in nine days' must not read the same.
    hout = Path(tmp) / "health"; hout.mkdir()
    h1 = mm.record_health(hout, False, "network", "2026-09-24T00:00:00+00:00")
    check(h1["consecutive_failures"] == 1, "the first failure counts one")
    h2 = mm.record_health(hout, False, "network", "2026-09-25T00:00:00+00:00")
    h3 = mm.record_health(hout, False, "auth", "2026-09-26T00:00:00+00:00")
    check(h3["consecutive_failures"] == 3, "consecutive failures accumulate")
    check(h3["failing_since"] == "2026-09-24T00:00:00+00:00",
          "the streak dates from its FIRST failure, not its latest")
    check(h3["last_kind"] == "auth", "the kind is the latest one, not the first")
    ok_h = mm.record_health(hout, True, "", "2026-09-27T00:00:00+00:00")
    check(ok_h["consecutive_failures"] == 0, "one success clears the streak")
    check("failing_since" not in ok_h, "and clears the date it started")
    again = mm.record_health(hout, False, "network", "2026-09-28T00:00:00+00:00")
    check(again["failing_since"] == "2026-09-28T00:00:00+00:00",
          "a new streak dates from itself, not the old one")

    # And the status file has to SAY it, or none of the above is visible.
    mm.write_status(sout, False, t0, "Refresh token rejected", "auth", h3)
    st = (sout / "mirror-status.md").read_text(encoding="utf-8")
    check("**FAILED: authentication**" in st, "the status line names the kind of failure")
    check("3 consecutive failures" in st, "and how many in a row")
    check("2026-09-24" in st, "and since when")
    check("ROADMAP item 1" in st and "not something to wait out" in st,
          "an auth failure says migrate rather than wait")

    mm.write_status(sout, False, t0, "ConnectionError", "network",
                    {"consecutive_failures": 1, "failing_since": "2026-09-24T00:00:00+00:00"})
    st_n = (sout / "mirror-status.md").read_text(encoding="utf-8")
    check("may well clear by itself" in st_n, "one network failure is not alarming")
    check("**1 consecutive failure**" in st_n and "failures**" not in st_n,
          "and is counted in the singular")
    mm.write_status(sout, False, t0, "ConnectionError", "network",
                    {"consecutive_failures": 9, "failing_since": "2026-09-15T00:00:00+00:00"})
    st_m = (sout / "mirror-status.md").read_text(encoding="utf-8")
    check("stop assuming it will" in st_m, "nine of them is")
    check(st_n != st_m, "one failure and nine do not read the same")

    mm.NONINTERACTIVE = True
    try:
        mm.require_interactive("Browser authorization")
        check(False, "require_interactive exits on a scheduled run")
    except SystemExit as exc:
        check("scheduled run" in str(exc), "require_interactive explains itself and exits")
    mm.NONINTERACTIVE = False

    # 2026-09-24: namespacing made keymap.get(doc_id) miss on every document, so
    # every attachment was skipped by a guard written for the rare unkeyed record.
    # The run reported "0 extracted, 0 unchanged, 0 failed" and still wrote **ok**.
    # The fixtures did not catch it because THEY were keyed the old way too --
    # a fixture that models the format the code just left cannot fail with it.
    # 2026-09-24: 33 extracts carried NUL bytes. GNU grep treats such a file as
    # binary and reports NO MATCH -- silently, exit 1, no "Binary file matches".
    # `grep -rl text/` is what the library's CLAUDE.md prescribes for subject
    # search, so those papers answered a confident zero while sitting in
    # library.bib and index.md marked as having extracted text.
    print("\ncontrol characters that would hide an extract from grep")

    check(mm.strip_control("a\x00b") == "ab", "a NUL is removed")
    check(mm.strip_control("a\x01\x1f\x7fb") == "ab", "so are the other C0 controls and DEL")
    check(mm.strip_control("keep\tthis\nand this") == "keep\tthis\nand this",
          "tab and newline are text and are kept")
    # CR is a line ending, not junk: deleting one joins two lines and collides
    # the words either side. 820k survived the first version of this function.
    check(mm.strip_control("reactor\r\nflow") == "reactor\nflow",
          "CRLF becomes a newline, not a deletion")
    check(mm.strip_control("reactor\rflow") == "reactor\nflow",
          "and so does a lone CR, which older PDFs use as the line ending")
    check("\r" not in mm.clean_page_text("a\r\nb\rc"), "no CR survives extraction")
    check(mm.strip_control("reactor\r\nflow").count("\n") == 1,
          "CRLF does not become two newlines")
    check(mm.clean_page_text("Polyethylene\x00 Terephthalate").find("\x00") == -1,
          "clean_page_text strips them, so no extract is written with one")

    # The property that actually matters is not "the string differs" -- it is that
    # grep can still find the words. Test what the reader will do, with the real
    # binary, or this is a check that cannot fail.
    # -I is "ignore binary files", and it is what makes the paper vanish rather
    # than anything about grep's defaults: plain GNU grep and ripgrep both FIND a
    # NUL-bearing extract. It matters because the grep an agent session invokes
    # here is a shim that passes -I, so a subject sweep run by the library session
    # silently skips these files while a person at a terminal would not.
    import shutil as _sh, subprocess as _sp
    grep = _sh.which("grep")
    if grep:
        gdir = tmp / "grepcheck"; gdir.mkdir()
        dirty = gdir / "dirty.md"
        dirty.write_text("Polyethylene\x00 Terephthalate was milled", encoding="utf-8")
        rc_dirty = _sp.run([grep, "-I", "-l", "Terephthalate", str(dirty)],
                           capture_output=True).returncode
        check(rc_dirty == 1,
              "a NUL hides the paper from a binary-skipping search (the bug)")
        rc_plain = _sp.run([grep, "-l", "Terephthalate", str(dirty)],
                           capture_output=True).returncode
        check(rc_plain == 0,
              "while a search that reads binary files still finds it -- so the "
              "scope is the tool, not every grep")
        clean = gdir / "clean.md"
        clean.write_text(mm.strip_control("Polyethylene\x00 Terephthalate was milled"),
                         encoding="utf-8")
        rc_clean = _sp.run([grep, "-I", "-l", "Terephthalate", str(clean)],
                           capture_output=True).returncode
        check(rc_clean == 0, "and stripping it makes the paper findable by both")

    # The guard at the write site, for text built by some route that bypasses
    # clean_page_text. It must strip AND say so, not strip quietly.
    rows = [{"key": "Dirty2020Paper", "status": "control-chars",
             "title": "A paper with NULs", "detail": "3 control characters stripped"}]
    mm.write_extraction_report(rows, out, extracted=1)
    crep = (out / "extraction-report.md").read_text(encoding="utf-8")
    check("control characters stripped: 1" in crep, "the report counts them")
    check("Dirty2020Paper" in crep and "## Control characters stripped" in crep,
          "and names the paper under its own heading")
    check("silent zero" in crep, "and says what would have gone wrong")

    CIF_BYTES = b"data_K1\n_cell_length_a 5.43\n"
    PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

    # --attachments keep does NOT archive a library that is already mirrored:
    # the skip fires for every unchanged attachment and continues before any
    # download. Correct for a refresh, wrong for an evacuation -- and the run
    # reports success either way, so the emptiness is silent. --backfill is the
    # difference between "get the PDFs off this service" working and appearing to.
    print("\n--backfill archives what a keep run skips")

    # Serves bytes that MATCH the declared type. The stub used to hand PDF
    # bytes to a .cif attachment, which no longer passes unnoticed now that the
    # bytes decide: a fixture that lies about its own content cannot test a
    # sniff.
    class Serves:
        def __init__(self): self.n = 0
        def get(self, url, **kw):
            self.n += 1
            body = CIF_BYTES if "f2" in url else pdf_bytes
            class R:
                status_code = 200; ok = True; content = body
                def raise_for_status(self): pass
            return R()

    bf = tmp / "backfill"
    two = {"d1": [{"id": "f1", "mime_type": "application/pdf", "filehash": "h1"},
                  {"id": "f2", "mime_type": "chemical/x-cif", "filehash": "h2",
                   "file_name": "structure.cif"}]}
    bkm = {mm.qualify("d1"): "K1"}
    bdoc = {"d1": DOCS[0]}
    bst = {}
    mm.harvest_attachments(Serves(), two, bkm, bdoc, bf, bst, "text")
    check(not (bf / "pdf").exists(), "a text-mode refresh leaves no pdf/ behind")

    plain = Serves()
    mm.harvest_attachments(plain, two, bkm, bdoc, bf, bst, "keep")
    check(plain.n == 0,
          "keep alone downloads NOTHING once the extracts exist (the trap)")
    check(not list((bf / "pdf").glob("*")) if (bf / "pdf").exists() else True,
          "and archives nothing, while still reporting a successful run")

    filled = Serves()
    mm.harvest_attachments(filled, two, bkm, bdoc, bf, bst, "keep", backfill=True)
    got = sorted(p.name for p in (bf / "pdf").glob("*"))
    check(filled.n == 2, f"--backfill fetches the skipped attachments ({filled.n})")
    check(got == ["K1-2.cif", "K1.pdf"],
          f"archiving each under its own suffix, not all as .pdf ({got})")

    again = Serves()
    mm.harvest_attachments(again, two, bkm, bdoc, bf, bst, "keep", backfill=True)
    check(again.n == 0, "a second --backfill run fetches nothing: resumable by construction")

    # It must not disturb the mirror it is archiving.
    before = dict(bst["files"])
    once_more = Serves()
    mm.harvest_attachments(once_more, two, bkm, bdoc, bf, bst, "keep", backfill=True)
    check(bst["files"] == before, "and changes no extraction state")

    print("\na text refresh must not erode an archive it did not create")

    # The timer runs --attachments text with no arguments, so this is the
    # scheduled path, not a manual one. Before the fix it unlinked any PDF it
    # found after extracting: one changed paper, one archived PDF gone, and
    # nothing reported it, because deleting the PDF is that mode's documented
    # job. `from_disk` is the distinction -- a file already on disk when the run
    # started belongs to whoever put it there.
    kept = sorted(p_.name for p_ in (bf / "pdf").glob("*"))
    check(kept == ["K1-2.cif", "K1.pdf"], f"the archive is in place to be eroded ({kept})")

    # Changing the filehash is what stops the skip branch firing, which is
    # exactly what a paper edited in Mendeley does.
    changed = {"d1": [dict(two["d1"][0], filehash="h1-NEW"),
                      dict(two["d1"][1], filehash="h2-NEW")]}
    erode = Serves()
    mm.harvest_attachments(erode, changed, bkm, bdoc, bf, bst, "text")
    still = sorted(p_.name for p_ in (bf / "pdf").glob("*"))
    check(still == kept, f"an archived PDF survives a text-mode reprocess ({still})")
    # This asserted `== 1` until 2026-10-01, with "the .cif only" written beside
    # it as though re-fetching were expected. It was the hardcoded `.pdf` in the
    # reuse lookup: the .cif sat archived and unreachable, so every changed
    # attachment that was not a .pdf went back to the network. The third test in
    # this file found asserting its own defect, and the third found by changing
    # the code rather than by reading it.
    check(erode.n == 0,
          f"and BOTH were re-used from disk, whatever suffix they are "
          f"archived under ({erode.n} downloads)")

    # The one thing it should still clear: a zero-byte stub from a download that
    # died. That is not an archive, and leaving it would make the next run skip
    # a real fetch.
    stub = tmp / "stub"
    (stub / "pdf").mkdir(parents=True)
    (stub / "pdf" / "K1.pdf").write_bytes(b"")
    one = {"d1": [two["d1"][0]]}
    mm.harvest_attachments(Serves(), one, bkm, bdoc, stub, {}, "text")
    check(not (stub / "pdf" / "K1.pdf").exists(),
          "but a zero-byte stub is still cleared, since it is nobody's archive")

    print("\na pass that examines nothing is a failure, not a zero")

    def staged(name: str) -> Path:
        """A harvest dir with the PDF already on disk, so NoNetwork is not hit."""
        d = tmp / name
        (d / "pdf").mkdir(parents=True)
        (d / "pdf" / "Muller2020Yield.pdf").write_bytes(pdf_bytes)
        return d

    st2 = {}
    fetched2, _s2, _f2, _r2 = mm.harvest_attachments(
        NoNetwork(), {"d1": [{"id": "x1", "mime_type": "application/pdf", "filehash": "h1"}]},
        {mm.qualify("d1"): "Muller2020Yield"}, {"d1": DOCS[0]}, staged("ns"), st2, "text")
    check(fetched2 == 1,
          "a namespaced keymap resolves an API id that is bare, and extraction runs")

    # And the shape of the bug itself: nothing resolves, so nothing is examined.
    try:
        mm.harvest_attachments(
            NoNetwork(), {"d1": [{"id": "x1", "mime_type": "application/pdf", "filehash": "h1"}]},
            {"stale-unqualified-d1": "Muller2020Yield"}, {"d1": DOCS[0]},
            staged("ns2"), {}, "text")
        check(False, "a keymap that resolves nothing raises rather than reporting zeros")
    except RuntimeError as exc:
        check("not one was examined" in str(exc),
              "a keymap that resolves nothing raises rather than reporting zeros")
        check("bug in the tool" in str(exc),
              "and says it is the tool's fault, not the account's")

    # An genuinely unkeyed document among many must NOT raise -- that is the case
    # the guard was written for, and it still has to work.
    st3 = {}
    f3, _s3, _f3, _r3 = mm.harvest_attachments(
        NoNetwork(),
        {"d1": [{"id": "x1", "mime_type": "application/pdf", "filehash": "h1"}],
         "dX": [{"id": "x9", "mime_type": "application/pdf", "filehash": "h9"}]},
        {mm.qualify("d1"): "Muller2020Yield"}, {"d1": DOCS[0]}, staged("ns3"), st3, "text")
    check(f3 == 1, "one unkeyed document among several is skipped, not raised over")

    print("\na deliberate deletion is not a gap for --backfill to fill")

    # --backfill tests only whether a file is on disk, so before removed.tsv it
    # could not tell "never evacuated" from "evacuated, then deleted on
    # purpose". The evacuation procedure is documented as resumable and
    # re-running it is how an interrupted one is finished -- so the one command
    # written to be safe to repeat was the one that undid a deletion.
    check(mm.load_removed(tmp / "no-such-mirror") == {},
          "no removed.tsv at all is simply nothing removed")

    rm_out = tmp / "removed"
    rm_state = mm.mirror_state_dir(rm_out)
    (rm_state / "removed.tsv").write_text(
        "# taken out by hand, 2026-10-01\n"
        "\n"
        "K1-2\t2026-10-01\tnot a paper: a structure file\n"
        "BareStem\n",
        encoding="utf-8")
    rmap = mm.load_removed(rm_out)
    check(rmap.get("K1-2") == "2026-10-01 not a paper: a structure file",
          "a stem, its date and its reason are read back")
    check("BareStem" in rmap,
          "a stem on its own is still a stem: a hand-edit cannot fail to "
          "protect a file by leaving the reason off")
    check("#" not in "".join(rmap) and len(rmap) == 2,
          f"comments and blank lines are not stems ({sorted(rmap)})")

    rm_st = {}
    mm.harvest_attachments(Serves(), two, bkm, bdoc, rm_out, rm_st, "text")
    held = Serves()
    mm.harvest_attachments(held, two, bkm, bdoc, rm_out, rm_st, "keep",
                           backfill=True)
    got_rm = sorted(p_.name for p_ in (rm_out / "pdf").glob("*"))
    check(got_rm == ["K1.pdf"],
          f"--backfill archives the one that was never evacuated ({got_rm})")
    check("K1-2.cif" not in got_rm,
          "and does NOT restore the one a person deleted on purpose")
    check(held.n == 1, f"withholding means not fetching it either ({held.n})")

    # The dangerous direction: a tombstone that matches too much silently stops
    # archiving real papers. Exact stems only, never a prefix.
    pre_out = tmp / "prefix"
    (mm.mirror_state_dir(pre_out) / "removed.tsv").write_text(
        "K1\t2026-10-01\tthe first attachment only\n", encoding="utf-8")
    pre_st = {}
    mm.harvest_attachments(Serves(), two, bkm, bdoc, pre_out, pre_st, "text")
    mm.harvest_attachments(Serves(), two, bkm, bdoc, pre_out, pre_st, "keep",
                           backfill=True)
    got_pre = sorted(p_.name for p_ in (pre_out / "pdf").glob("*"))
    check(got_pre == ["K1-2.cif"],
          f"a tombstone on K1 withholds K1 and NOT K1-2 ({got_pre})")

    # removed.tsv must never become a reason to delete. An archive-eroding path
    # is the defect this repo has now fixed twice; a list of filenames the tool
    # consults is exactly how a third one would arrive.
    keep_out = tmp / "tombstoned-but-present"
    (mm.mirror_state_dir(keep_out) / "removed.tsv").write_text(
        "K1\t2026-10-01\tdeleted, then put back by hand\n", encoding="utf-8")
    (keep_out / "pdf").mkdir(parents=True, exist_ok=True)
    (keep_out / "pdf" / "K1.pdf").write_bytes(pdf_bytes)
    kst = {}
    mm.harvest_attachments(Serves(), two, bkm, bdoc, keep_out, kst, "text")
    mm.harvest_attachments(Serves(), two, bkm, bdoc, keep_out, kst, "keep",
                           backfill=True)
    check((keep_out / "pdf" / "K1.pdf").exists(),
          "a listed stem whose file IS present is left alone: the list "
          "withholds a fetch, it never causes a deletion")

    print("\nthe bytes decide what is a PDF, not the backend's opinion")

    # Mendeley reported a non-PDF MIME type for Vanommeslaeghe2009Charmm -- a
    # complete 20-page CGenFF paper, text layer intact -- so `"pdf" in
    # mime_type` skipped it, cached the skip, and the report never said so.
    check(mm.looks_like_pdf(pdf_bytes), "real PDF bytes are a PDF")
    check(mm.looks_like_pdf(b"\n\n" + pdf_bytes),
          "and so are PDF bytes behind a short preamble, which writers do emit")
    # What it must REJECT, or a sniff is just an accept-everything with a name:
    check(not mm.looks_like_pdf(PNG_BYTES), "a PNG is not")
    check(not mm.looks_like_pdf(CIF_BYTES), "nor is a structure file")
    check(not mm.looks_like_pdf(b""), "nor is nothing at all")
    check(not mm.looks_like_pdf(b"x" * 4096 + mm.PDF_MAGIC),
          "and a header 4 KB in is not a header: the window is bounded")

    print("\nthe archive is found under the suffix it was written with")

    adir = tmp / "arch"
    adir.mkdir()
    cif_rec = {"file_name": "structure.cif"}
    (adir / "K9-2.cif").write_bytes(CIF_BYTES)
    check(mm.find_archived(adir, "K9-2", cif_rec) == adir / "K9-2.cif",
          "a .cif archive is reachable, where a hardcoded .pdf saw nothing")
    (adir / "K9.pdf").write_bytes(pdf_bytes)
    check(mm.find_archived(adir, "K9", {"file_name": "paper.pdf"}) == adir / "K9.pdf",
          "and a .pdf still is")
    check(mm.find_archived(adir, "K8", cif_rec) is None,
          "a stem with no archive is None, never a neighbour's file")
    (adir / "K7.pdf").write_bytes(b"")
    check(mm.find_archived(adir, "K7", None) is None,
          "and a zero-byte stub is not an archive")

    print("\nan attachment whose MIME type lies is still extracted")

    class ServesPdf:
        def __init__(self): self.n = 0
        def get(self, url, **kw):
            self.n += 1
            class R:
                status_code = 200; ok = True; content = pdf_bytes
                def raise_for_status(self): pass
            return R()

    lying = {"d1": [{"id": "L1", "mime_type": "application/octet-stream",
                     "filehash": "hL", "file_name": "charmm_gen.-_charmm_g"}]}
    lkm = {mm.qualify("d1"): "Vanommeslaeghe2009Charmm"}
    liar, lst = tmp / "liar", {}
    lf, _ls, _lx, lrep = mm.harvest_attachments(
        ServesPdf(), lying, lkm, {"d1": DOCS[0]}, liar, lst, "text")
    check(lf == 1, "a PDF behind a wrong MIME type is extracted")
    check((liar / "text" / "Vanommeslaeghe2009Charmm.md").exists(),
          "and its extract exists, where the library has none today")
    check(not any(r["status"] == "not-pdf" for r in lrep),
          "and it is not filed as a non-PDF, because it is not one")

    print("\nan attachment that really is not a PDF is reported, every run")

    class ServesPng:
        def __init__(self): self.n = 0
        def get(self, url, **kw):
            self.n += 1
            class R:
                status_code = 200; ok = True; content = PNG_BYTES
                def raise_for_status(self): pass
            return R()

    notpdf = {"d1": [{"id": "P1", "mime_type": "image/png", "filehash": "hP",
                      "file_name": "equation.png"}]}
    pkm = {mm.qualify("d1"): "Hoover1979Exact"}
    png, pst = tmp / "png", {}
    pf, _ps, _px, prep = mm.harvest_attachments(
        ServesPng(), notpdf, pkm, {"d1": DOCS[0]}, png, pst, "text")
    check(pf == 0, "a PNG is not extracted, which was always correct")
    rows_np = [r for r in prep if r["status"] == "not-pdf"]
    check(len(rows_np) == 1 and rows_np[0]["key"] == "Hoover1979Exact",
          "and it IS reported: the silence was the defect, not the skip")
    check("image/png" in rows_np[0]["detail"] and "PNG" in rows_np[0]["detail"],
          f"with what the bytes were, so a figure and a paper can be told "
          f"apart ({rows_np[0]['detail']})")
    check(not (png / "text" / "Hoover1979Exact.md").exists(),
          "and no extract is invented for it")

    # The run that SKIPS it must report it too, or the row lives exactly one
    # refresh. That is how 108 OCR rows emptied out of the report in September.
    again_png = ServesPng()
    _f, sk, _x, prep2 = mm.harvest_attachments(
        again_png, notpdf, pkm, {"d1": DOCS[0]}, png, pst, "text")
    check(sk == 1 and again_png.n == 0, "a second run skips it on cached state")
    check(any(r["status"] == "not-pdf" and r["key"] == "Hoover1979Exact"
              for r in prep2),
          "and reports it anyway -- a cached skip is still a missing extract")

    nrows = [{"key": "Hoover1979Exact", "status": "not-pdf",
              "title": "Exact hard-disk free volumes",
              "detail": "image/png, 72 bytes beginning b'\\x89PNG'"}]
    mm.write_extraction_report(nrows, out, extracted=0)
    nrep = (out / "extraction-report.md").read_text(encoding="utf-8")
    check("not a PDF, nothing to extract: 1" in nrep, "the report counts them")
    check("## Not a PDF" in nrep and "Hoover1979Exact" in nrep,
          "and names each under its own heading")
    check("ONLY attachment" in nrep,
          "and says which of these rows are papers rather than figures")
    check("still in Mendeley" not in nrep,
          "and no longer promises the PDF is safe in an account that has "
          "already been downgraded")

    print("\na refresh reads its own archive instead of the network")

    reuse, rst = tmp / "reuse", {}
    (reuse / "pdf").mkdir(parents=True)
    (reuse / "pdf" / "Vanommeslaeghe2009Charmm.-_charmm_g").write_bytes(pdf_bytes)
    net = ServesPdf()
    rf, _rs, _rx, _rr = mm.harvest_attachments(
        net, lying, lkm, {"d1": DOCS[0]}, reuse, rst, "text")
    check(rf == 1 and net.n == 0,
          f"an archive under its real suffix is reused, not re-fetched from an "
          f"account being retired (downloads={net.n})")
    check((reuse / "pdf" / "Vanommeslaeghe2009Charmm.-_charmm_g").exists(),
          "and a text-mode run still does not erode it")

    print("\nsecond attachments are reachable (get_pdf.py <key>-2)")
    import get_pdf as getpdf

    check(getpdf.split_attachment("Abrams2012Fly-2") == ("Abrams2012Fly", 2),
          "a -2 suffix names the second attachment")
    check(getpdf.split_attachment("Abrams2012Fly") == ("Abrams2012Fly", 1),
          "a plain key is the first")
    check(getpdf.split_attachment("Brenner1990Empirical-3") == ("Brenner1990Empirical", 3),
          "and -3 the third")
    # Citation keys can legitimately end in a digit-ish tail; only -N with N>=2
    # is an attachment selector, and -1 is not one because nothing writes it.
    check(getpdf.split_attachment("Abrams2013Enhanced-1") == ("Abrams2013Enhanced-1", 1),
          "-1 is not an attachment selector: the mirror never writes that name")
    check(getpdf.split_attachment("Covid19Study") == ("Covid19Study", 1),
          "a key with digits inside is not split")

    pdfs = [{"id": "a", "mime_type": "application/pdf"},
            {"id": "b", "mime_type": "application/pdf"}]
    got, why = getpdf.choose_attachment(pdfs, out, "Whatever", 2)
    check(got and got["id"] == "b" and "2 of 2" in why, "the second is chosen and named")
    got1, _ = getpdf.choose_attachment(pdfs, out, "Whatever", 1)
    check(got1 and got1["id"] == "a", "the first is still the default")
    none, why3 = getpdf.choose_attachment(pdfs, out, "Whatever", 3)
    check(none is None and "only 2" in why3,
          "asking for one that does not exist says so rather than serving another")
    empty, why0 = getpdf.choose_attachment([], out, "Whatever", 1)
    check(empty is None and "no PDF" in why0, "and no attachment at all is its own message")

    # The Brenner shape: the extract for -2 records 14 pages, the served file has
    # 1. That mismatch is what the post-download check exists to catch.
    (out / "text").mkdir(exist_ok=True)
    (out / "text" / "Brenner1990Empirical-2.md").write_text(
        "\n".join(f"<!-- p. {i} -->\n\ntext\n" for i in range(1, 15)), encoding="utf-8")
    check(getpdf.extract_pages(out, "Brenner1990Empirical-2") == 14,
          "page count is read from the -2 extract, not the base one")
    one_pager = out / "one.pdf"
    one_pager.write_bytes(pdf_bytes)      # the fixture PDF has 2 pages, not 14
    check(getpdf.cache_is_the_right_paper(one_pager, out, "Brenner1990Empirical-2") is False,
          "a served file with the wrong page count is refused for a -2 key too")

    print("\nattachment inventory: orphans and duplicates, without downloading")

    inv = tmp / "inv"; (inv / "text").mkdir(parents=True)
    BODY = "".join(f"<!-- p. {n} -->\n\nbody text\n" for n in range(1, 4))
    def _ex(name, body=BODY, front=True):
        head = f"---\nkey: {name}\n---\n*Cite as `{name}`*\n\n" if front else ""
        (inv / "text" / f"{name}.md").write_text(head + body, encoding="utf-8")
    _ex("Real2020Paper"); _ex("Real2020Paper-2", BODY + "genuinely different\n")
    _ex("Orphan2009Methods"); _ex("Orphan2009Methods-2")      # same body: duplicate too
    _ex("Solo2001Single")
    _ex("Odd2020Name-2")            # a citekey that itself ends in -2
    bk = {"Real2020Paper": "d1", "Orphan2009Methods": "d2",
          "Solo2001Single": "d3", "Odd2020Name-2": "d4"}

    found = getpdf.local_extracts(inv, bk)
    check(found["Real2020Paper"] == [1, 2], "both extracts of a record are seen")
    check(found["Solo2001Single"] == [1], "a single extract is seen as one")
    check(found["Odd2020Name-2"] == [1],
          "a citation key that itself ends in -2 is a key, not an attachment number")

    # The front matter names the key, so these files always differ as bytes.
    # Comparing bodies is what distinguishes a second document from a second copy.
    a = inv / "text" / "Orphan2009Methods.md"
    b = inv / "text" / "Orphan2009Methods-2.md"
    check(a.read_bytes() != b.read_bytes(), "the two files differ as bytes")
    check(getpdf.extract_body(a) == getpdf.extract_body(b),
          "but their bodies are identical -- the same PDF attached twice")
    check(getpdf.extract_body(inv / "text" / "Real2020Paper-2.md")
          != getpdf.extract_body(inv / "text" / "Real2020Paper.md"),
          "while a genuine second document differs")

    class FilesStub:
        """Mendeley reports two attachments for d1 and only one for d2."""
        def paged(self, path, kind, **kw):
            return [{"document_id": "d1", "mime_type": "application/pdf"},
                    {"document_id": "d1", "mime_type": "application/pdf"},
                    {"document_id": "d2", "mime_type": "application/pdf"},
                    {"document_id": "d2", "mime_type": "text/plain"},
                    {"document_id": "d3", "mime_type": "application/pdf"}]

    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = getpdf.attachment_inventory(FilesStub(), inv, bk, [])
    report = buf.getvalue()
    check(rc == 1, "an orphan makes the sweep exit non-zero")
    check("Orphan2009Methods" in report and "ORPHAN" in report,
          "the record whose -2 has no attachment is named")
    check("DUPLICATE" in report, "and a -2 that merely repeats the base is called out")
    check("Real2020Paper" in report and "ORPHAN: -2" not in report.split("Orphan")[0],
          "a record whose attachments match its extracts is not flagged as orphaned")
    check("Solo2001Single" not in report,
          "a single-extract record is not in a sweep about multiple attachments")

    # The gap literature found: comparing each -N against the BASE examines
    # nothing when there is no base. Shan2011How's first attachment was not a
    # PDF, so only -2 and -3 exist and its two extracts of one paper went
    # unflagged. And exact equality would have missed them anyway -- they are
    # 0.9935 alike, not identical.
    nb = tmp / "nobase"; (nb / "text").mkdir(parents=True)
    long_body = "".join(f"<!-- p. {n} -->\n\n" + ("the same paper text " * 40) + "\n"
                        for n in range(1, 6))
    (nb / "text" / "NoBase2011How-2.md").write_text(long_body, encoding="utf-8")
    (nb / "text" / "NoBase2011How-3.md").write_text(
        long_body.replace("same paper text", "same paper txet", 1), encoding="utf-8")
    bk2 = {"NoBase2011How": "d9"}
    check(getpdf.local_extracts(nb, bk2)["NoBase2011How"] == [2, 3],
          "a record with no base extract is still seen, as -2 and -3")

    class OneEach:
        def paged(self, path, kind, **kw):
            return [{"document_id": "d9", "mime_type": "application/pdf"}] * 3

    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        getpdf.attachment_inventory(OneEach(), nb, bk2, [])
    r2 = buf2.getvalue()
    check("NO BASE" in r2, "and is reported as having produced no first extract")
    check("NEAR-DUPLICATE" in r2 and "-3 is" in r2,
          "its two extracts are compared against EACH OTHER, not against a base "
          "that does not exist")
    # A near-duplicate pair can be a proof and its published version. That is the
    # one difference that changes what may be CITED: Abrams2012Fly's two extracts
    # are 98.34% alike and differ in xxx -> 547, xxx-xxx -> 114-119.
    check(getpdf.is_proof("in press, xxx\u2013xxx, 2012") == 1,
          "placeholder page range is recognized as a proof")
    check(getpdf.is_proof("J. Am. Chem. Soc. XXXX, XXX, 000\u2013000") == 2,
          "the galley form counts both of its placeholders")
    check(getpdf.is_proof("Chem. Phys. Lett. 547, 114\u2013119") == 0,
          "a real page range is not a proof")
    check(getpdf.is_proof("sample 000-000 of the xxx series") >= 1,
          "and the zero form is caught with a plain hyphen too")

    pf = tmp / "proofpair"; (pf / "text").mkdir(parents=True)
    common = "".join(f"<!-- p. {n} -->\n\n" + ("shared sentence here. " * 60) + "\n"
                     for n in range(1, 4))
    (pf / "text" / "Prf2012Paper.md").write_text(
        common + "Chem. Phys. Lett. xxx, xxx\u2013xxx\n", encoding="utf-8")
    (pf / "text" / "Prf2012Paper-2.md").write_text(
        common + "Chem. Phys. Lett. 547, 114\u2013119\n", encoding="utf-8")
    bk3 = {"Prf2012Paper": "d7"}

    class TwoEach:
        def paged(self, path, kind, **kw):
            return [{"document_id": "d7", "mime_type": "application/pdf"}] * 2

    buf3 = io.StringIO()
    with contextlib.redirect_stdout(buf3):
        getpdf.attachment_inventory(TwoEach(), pf, bk3, [])
    r3 = buf3.getvalue()
    check("PAGINATION" in r3, "a proof/published pair is called out as such")
    check("cite -2" in r3, "and the reader is told WHICH one to cite")
    check("100.00%" not in r3,
          "a ratio short of equality never prints as 100.00% beside DUPLICATE rows")

    # The case that gating PAGINATION on similarity missed. A proof and its
    # published version have different page breaks, so the markers move and the
    # pair drifts apart: Caparco2018Effect's two extracts are 4% alike and its
    # base is the proof. Similarity is the wrong gate -- the closer the pair, the
    # likelier it would be caught, which is backwards.
    far = tmp / "farpair"; (far / "text").mkdir(parents=True)
    (far / "text" / "Far2018Effect.md").write_text(
        "<!-- p. 1 -->\n\n" + ("alpha beta gamma delta. " * 200)
        + "\nAIChE J, 00: 000\u2013000, 2018\n", encoding="utf-8")
    (far / "text" / "Far2018Effect-2.md").write_text(
        "<!-- p. 1 -->\n\n" + ("entirely unrelated wording throughout. " * 200)
        + "\nAIChE J, 64: 2934\u20132946, 2018\n", encoding="utf-8")
    import difflib as _dl
    ratio = _dl.SequenceMatcher(
        None, getpdf.extract_body(far / "text" / "Far2018Effect.md"),
        getpdf.extract_body(far / "text" / "Far2018Effect-2.md")).ratio()
    check(ratio < getpdf.NEAR,
          f"the pair is nowhere near duplicate ({ratio:.2f}), as the real one is not")
    buf4 = io.StringIO()
    with contextlib.redirect_stdout(buf4):
        getpdf.attachment_inventory(TwoEach.__class__("S", (), {
            "paged": lambda self, path, kind, **kw:
                [{"document_id": "d8", "mime_type": "application/pdf"}] * 2})(),
            far, {"Far2018Effect": "d8"}, [])
    r4 = buf4.getvalue()
    check("PAGINATION" in r4 and "cite -2" in r4,
          "and it is STILL flagged as proof-versus-published, similarity aside")
    check("NEAR-DUPLICATE" not in r4, "without being called a near-duplicate, which it is not")
    check("placeholder pagination (1)" in r4,
          "and the library-wide tail counts the proof extract on its own")

    # The worst combination: the extract that cannot be re-fetched is the one
    # with real page numbers, and the copy Mendeley still holds is the proof.
    # Shan2011How is exactly this. Losing it loses no content -- the pair is
    # 99.3% alike -- only the pagination, which is the only thing it is for.
    irr = tmp / "irreplaceable"; (irr / "text").mkdir(parents=True)
    shared = "<!-- p. 1 -->\n\n" + ("identical body sentence. " * 120) + "\n"
    (irr / "text" / "Irr2011How-2.md").write_text(
        shared + "J. Am. Chem. Soc. XXXX, XXX, 000\u2013000\n", encoding="utf-8")
    (irr / "text" / "Irr2011How-3.md").write_text(
        shared + "J. Am. Chem. Soc. 2011, 133, 9181\u20139183\n", encoding="utf-8")

    class OnlyTwo:
        """Mendeley holds two attachments; the mirror has extracts -2 and -3, so
        -3 is the orphan -- and -3 is the one with real pages."""
        def paged(self, path, kind, **kw):
            return [{"document_id": "dz", "mime_type": "application/pdf"}] * 2

    buf5 = io.StringIO()
    with contextlib.redirect_stdout(buf5):
        rc5 = getpdf.attachment_inventory(OnlyTwo(), irr, {"Irr2011How": "dz"}, [])
    r5 = buf5.getvalue()
    check("ORPHAN" in r5, "the orphan is still reported")
    check("IRREPLACEABLE" in r5, "and the worse case is called out separately")
    check("-3" in r5 and "Do not delete it" in r5,
          "naming the extract that must not be deleted")
    check("ONLY citable copy" in r5, "and summarized at the end")
    check(rc5 == 1, "an orphan still makes the sweep exit non-zero")

    # The ordinary orphan -- where what is lost is a duplicate -- must NOT be
    # escalated, or the loud case stops being loud.
    ord_ = tmp / "ordinaryorphan"; (ord_ / "text").mkdir(parents=True)
    (ord_ / "text" / "Ord2009Methods.md").write_text(shared, encoding="utf-8")
    (ord_ / "text" / "Ord2009Methods-2.md").write_text(shared, encoding="utf-8")

    class JustOne:
        def paged(self, path, kind, **kw):
            return [{"document_id": "dy", "mime_type": "application/pdf"}]

    buf6 = io.StringIO()
    with contextlib.redirect_stdout(buf6):
        getpdf.attachment_inventory(JustOne(), ord_, {"Ord2009Methods": "dy"}, [])
    r6 = buf6.getvalue()
    check("ORPHAN" in r6 and "IRREPLACEABLE" not in r6,
          "an orphan whose loss is only a duplicate is not escalated")

    b2 = getpdf.extract_body(nb / "text" / "NoBase2011How-2.md")
    b3 = getpdf.extract_body(nb / "text" / "NoBase2011How-3.md")
    check(b2 != b3, "the two bodies are not identical, so exact equality finds nothing")
    check("only copy" in report, "the sweep says why an orphan must not be deleted")

    print("\non-demand PDF fetch (get_pdf.py)")
    (out / ".mirror").mkdir(exist_ok=True)
    (out / ".mirror" / "citekeys.json").write_text(json.dumps(keymap), encoding="utf-8")
    gp = str(Path(__file__).parent / "get_pdf.py")

    def run_gp(*a):
        return subprocess.run([sys.executable, gp, "--out", str(out), *a],
                              capture_output=True, text=True, timeout=60)

    r = run_gp("--search", "packed bed")
    check("Nguyen2019Study" in r.stdout, f"--search finds a key by title ({r.stdout.strip()[:60]})")
    r = run_gp("Nguyen2019Stud")
    check("did you mean" in r.stdout and "Nguyen2019Study" in r.stdout,
          "a mistyped key suggests the right one")
    check("input" not in r.stderr.lower() and "EOF" not in r.stderr,
          "a bad key does not trigger the credential prompt")
    dest = tmp / "dest"
    dest.mkdir()
    (dest / "Nguyen2019Study.pdf").write_bytes(b"%PDF-1.4 stub")
    r = run_gp("--dest", str(dest), "Nguyen2019Study")
    check(r.stdout.strip().endswith("Nguyen2019Study.pdf") and r.returncode == 0,
          "a cached PDF is returned without authenticating")

    print("\ninbox closing report")
    import inbox

    # THE CACHE OVERWRITE. inbox.py named the cached copy after the citation key
    # alone, so a second attachment on the same record -- Supporting Information,
    # a corrigendum -- landed on top of the first. Nothing crashed; get_pdf.py then
    # served the SI to anyone who asked for the article.
    _cd = tempfile.mkdtemp()
    try:
        cache, inboxdir = Path(_cd) / "cache", Path(_cd) / "in"
        cache.mkdir(); inboxdir.mkdir()
        article = cache / "Khare2018Quantitative.pdf"
        article.write_bytes(b"THE ARTICLE, twelve pages of it")

        si = inboxdir / "si.pdf"; si.write_bytes(b"the supporting information")
        got = inbox.cache_target(cache, "Khare2018Quantitative", si)
        check(got.name == "Khare2018Quantitative-2.pdf",
              f"a second attachment is suffixed, not written over the first (got {got.name})")
        check(article.read_bytes() == b"THE ARTICLE, twelve pages of it",
              "and the first file still holds the article")

        # Re-filing the very same bytes is not a second attachment.
        same = inboxdir / "again.pdf"; same.write_bytes(b"THE ARTICLE, twelve pages of it")
        check(inbox.cache_target(cache, "Khare2018Quantitative", same) == article,
              "an identical file reuses its name rather than piling up copies")

        # A third distinct one keeps counting.
        (cache / "Khare2018Quantitative-2.pdf").write_bytes(b"the supporting information")
        third = inboxdir / "third.pdf"; third.write_bytes(b"a corrigendum")
        check(inbox.cache_target(cache, "Khare2018Quantitative", third).name
              == "Khare2018Quantitative-3.pdf", "and a third distinct file gets -3")

        # The read side, independently: a cache hit is checked against the page
        # count the mirror's own extract records, which needs no network.
        import get_pdf
        mirror = Path(_cd) / "mirror"; (mirror / "text").mkdir(parents=True)
        (mirror / "text" / "Khare2018Quantitative.md").write_text(
            "".join(f"<!-- p. {n} -->\nbody\n" for n in range(1, 13)), encoding="utf-8")
        check(get_pdf.extract_pages(mirror, "Khare2018Quantitative") == 12,
              "the extract's page markers give the expected page count")
        check(get_pdf.extract_pages(mirror, "NeverHeardOf2020") == 0,
              "and an unknown key gives zero rather than raising")

        check(get_pdf.cache_is_the_right_paper(article, mirror, "NoExtract2020") is True,
              "with no extract to compare against, a cache hit is served (unknowable, not wrong)")
    finally:
        shutil.rmtree(_cd, ignore_errors=True)

    # A garbled text layer must be repaired here exactly as a refresh repairs it.
    # It was not, and the asymmetry was expensive: a correctly named paper could
    # not be identified from its own first page, while the same file once filed
    # produced a clean extract. The failure reads "the title is not on page 1",
    # which is true of what pymupdf returned and says nothing about the file.
    class _FakePage:
        def __init__(self, t): self._t = t
        def get_text(self): return self._t

    class _FakeDoc:
        def __init__(self, pages): self._p = pages; self.page_count = len(pages)
        def __getitem__(self, i): return _FakePage(self._p[i])
        def __enter__(self): return self
        def __exit__(self, *a): return False

    GARBLED = "\x01\x02\x03\x04\x05\x06 \x07\x08\x09\x0b\x0c " * 40
    CLEAN = "Computer simulation of structure and properties of crosslinked polymers "
    real_open, real_pdftotext = inbox.pymupdf.open, inbox.pdftotext_pages
    _td = tempfile.mkdtemp()
    fake_pdf = Path(_td) / "paper.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 not really a pdf, never parsed here")
    try:
        inbox.pymupdf.open = lambda _p: _FakeDoc([GARBLED, GARBLED])
        inbox.pdftotext_pages = lambda _b: [CLEAN, CLEAN]
        got = inbox.pdf_text(fake_pdf)
        check("crosslinked polymers" in got,
              "a garbled first page is re-read with pdftotext, as the mirror does")
        check("\x01" not in got, "and the control characters do not survive")

        # If pdftotext is unavailable the old text is still returned: degraded, but
        # nothing is lost and nothing raises.
        inbox.pdftotext_pages = lambda _b: None
        got = inbox.pdf_text(fake_pdf)
        check(got.strip().startswith("\x01"), "without pdftotext it degrades rather than failing")

        # A page that is NOT garbled must be left alone -- the repair must not
        # replace good text with pdftotext's differently-wrapped version.
        inbox.pymupdf.open = lambda _p: _FakeDoc([CLEAN, CLEAN])
        inbox.pdftotext_pages = lambda _b: ["WRONG WRONG WRONG", "WRONG WRONG WRONG"]
        check("WRONG" not in inbox.pdf_text(fake_pdf),
              "clean text is never replaced by the fallback")
    finally:
        inbox.pymupdf.open, inbox.pdftotext_pages = real_open, real_pdftotext
        shutil.rmtree(_td, ignore_errors=True)

    # A DOI is allowed to contain brackets, and old Elsevier and Wiley DOIs are
    # full of them. Excluding those characters from the pattern truncated such a
    # DOI mid-string. NOTE, because the first telling of this got it wrong: this
    # bites the page-text path only. filename_dois carries a second pattern,
    # 10\.\d{4,9}[_-][^\s/]+, which never used the restrictive class, so a file
    # NAMED for its bracketed DOI always resolved. A file that must be identified
    # from its own first page did not.
    def dois(text):
        return [inbox.clean_doi(m) for m in inbox.DOI_RE.findall(text)]

    check(dois("10.1016/s0032-3861(01)00634-6") == ["10.1016/s0032-3861(01)00634-6"],
          "an Elsevier PII DOI survives its parentheses")
    check(dois("10.1016/S0076-6879(09)66015-8") == ["10.1016/s0076-6879(09)66015-8"],
          "and so does the one that sat in rejected/")
    SICI = "10.1002/(SICI)1097-4628(19990906)73:10<1927::AID-APP12>3.0.CO;2-O"
    check(dois(SICI) == [SICI.lower()],
          "a Wiley SICI DOI survives brackets, angle brackets and a semicolon")
    check(dois("10.1021/ma00078a014") == ["10.1021/ma00078a014"],
          "a plain DOI is unchanged")

    # The reason the brackets were excluded in the first place: prose puts a DOI
    # in parentheses, and that closing bracket is the sentence's, not the DOI's.
    # Balance decides which -- a closer with no opener before it is punctuation.
    check(dois("see the paper (10.1234/abc) for detail") == ["10.1234/abc"],
          "a bracket the prose opened is still trimmed")
    check(dois("cite <10.1234/ang> and [10.1234/sq]") == ["10.1234/ang", "10.1234/sq"],
          "so are angle brackets and square brackets")
    check(dois("DOI: 10.1234/xyz.") == ["10.1234/xyz"], "and a sentence-ending period")
    check(dois("10.1234/foo(2020)") == ["10.1234/foo(2020)"],
          "but a DOI that really ends in a balanced bracket keeps it")

    check(inbox.filename_dois("10.1016_s0032-3861(01)00634-6.pdf")
          == ["10.1016/s0032-3861(01)00634-6"],
          "the file-name path handles brackets too")

    # A clean run: the refresh instruction appears, and nothing else.
    r = inbox.closing_report(["a.pdf"], [], [], refresh_cmd="REFRESH")
    check("REFRESH" in r, "a successful run says to refresh")
    check("NOT filed" not in r, "a successful run reports no failures")

    # A failed run must NOT tell you to go pull down text that was never sent.
    r = inbox.closing_report([], [], [("a.pdf", "upload refused (403)")], refresh_cmd="REFRESH")
    check("REFRESH" not in r,
          "a run that attached nothing does not tell you to refresh")
    check("NOT filed" in r and "a.pdf" in r and "403" in r,
          "the failure, the file name and the reason are all reported")

    # The mixed case is the dangerous one: `| tail` shows only the end, so the
    # failure has to be the LAST thing printed, after the refresh instruction.
    r = inbox.closing_report(["good.pdf"], [], [("bad.pdf", "boom")], refresh_cmd="REFRESH")
    check("REFRESH" in r, "the part that worked still says to refresh")
    lines = [ln for ln in r.strip().splitlines() if ln.strip()]
    check(r.index("NOT filed") > r.index("REFRESH"),
          "the failure block comes after the success line, so tail shows it")
    check("bad.pdf" in "\n".join(lines[-3:]), "the failed file name survives a tail -3")


    # Which refresh command gets printed is a safety question, not a cosmetic one:
    # naming run_mirror.sh where the systemd unit exists invites a run that
    # overlaps the timer, and two at once make Syncthing conflict files in
    # .mirror/. Probing is injected so the result does not depend on this host.
    class _P:
        def __init__(self, rc, out): self.returncode, self.stdout = rc, out

    unit = inbox.REFRESH_UNIT
    have_unit = lambda *a, **k: _P(0, f"UNIT FILE                STATE\n{unit}  enabled\n")
    check(inbox.refresh_command(which=lambda n: "/usr/bin/systemctl", run=have_unit)
          == f"systemctl --user start {unit}",
          "with the unit installed, it names the service, which cannot double-start")

    no_unit = lambda *a, **k: _P(0, "UNIT FILE   STATE\n")
    check(inbox.refresh_command(which=lambda n: "/usr/bin/systemctl", run=no_unit)
          == "./run_mirror.sh",
          "systemd present but no unit falls back to the launcher")

    check(inbox.refresh_command(which=lambda n: None, run=have_unit) == "./run_mirror.sh",
          "no systemctl at all falls back to the launcher")

    def _boom(*a, **k): raise OSError("systemd is not running")
    check(inbox.refresh_command(which=lambda n: "/usr/bin/systemctl", run=_boom)
          == "./run_mirror.sh",
          "a systemctl that errors must not take the whole run down with it")

    check("run_mirror.sh" in inbox.closing_report(["a.pdf"], [], [],
          refresh_cmd="./run_mirror.sh"),
          "an injected command is what gets printed")

    # Skipped files are left in the inbox too, and saying so is not an error.
    r = inbox.closing_report(["a.pdf"], [("b.pdf", "Key2020Word already has a PDF")], [], refresh_cmd="REFRESH")
    check("--replace" in r and "b.pdf" in r, "a skipped file is reported with the escape hatch")
    check("NOT filed" not in r, "a skipped file is not counted as a failure")

    check(inbox.closing_report([], [], [], dry_run=True) == "",
          "a clean dry run prints no closing block")
    r = inbox.closing_report([], [], [("a.pdf", "cannot identify: no DOI")], dry_run=True, refresh_cmd="REFRESH")
    check("REFRESH" not in r and "a.pdf" in r,
          "a dry run reports what it could not identify but never says to refresh")

    print("\ninbox: an empty download is not a scan")
    import pymupdf as _fitz

    # The file-name path deliberately trusts a DOI in the name over missing text,
    # so a scan with no text layer stays filable. A failed interlibrary-loan fetch
    # returns a PDF that is blank, and named for its DOI it would otherwise become
    # a reference with nothing behind it.
    blank = tmp / "blank.pdf"
    d = _fitz.open(); d.new_page(); d.save(blank); d.close()
    check(inbox.has_content(blank) is False, "a blank page is not content")

    drawn = tmp / "drawn.pdf"
    d = _fitz.open(); pg = d.new_page()
    pg.draw_rect(_fitz.Rect(20, 20, 200, 200))          # marks but no text: a scan
    d.save(drawn); d.close()
    check(inbox.has_content(drawn) is True,
          "a page with marks and no text IS content -- a real scan must stay filable")

    texted = tmp / "texted.pdf"
    d = _fitz.open(); pg = d.new_page(); pg.insert_text((72, 72), "hello")
    d.save(texted); d.close()
    check(inbox.has_content(texted) is True, "an ordinary text page is content")

    check(inbox.has_content(tmp / "does-not-exist.pdf") is False,
          "an unreadable file is not content, and does not raise")

    print("\ninbox: finding a reference the library already holds")
    ibx = tmp / "ibx"
    (ibx / ".mirror").mkdir(parents=True)
    (ibx / "library.bib").write_text("""
@article{Doe2025Widget,
  author  = {Doe, Jane},
  title   = {{A Study of Widgets: the Sequel}},
  journal = {arXiv},
  year    = {2025},
  eprint  = {2501.00001},
}

@article{Roe2024Gadget,
  author  = {Roe, Richard},
  title   = {{On Gadgets}},
  journal = {J. Things},
  year    = {2024},
  doi     = {10.1000/gadget},
}
""", encoding="utf-8")
    (ibx / ".mirror" / "citekeys.json").write_text(
        json.dumps({"m1": "Doe2025Widget", "m2": "Roe2024Gadget"}), encoding="utf-8")

    check(inbox.existing_document(ibx, "10.1000/gadget")[1] == "Roe2024Gadget",
          "a DOI that is in the bib still matches by DOI")
    check(inbox.existing_document(ibx, "10.1000/gadget")[0] == "m2",
          "the matched entry resolves to its Mendeley id")

    # The regression this guards: an arXiv reference with no doi field. Its PDF
    # carries a resolvable DOI, so the DOI search runs and finds nothing -- and
    # the title fallback used to be skipped entirely whenever a DOI was present,
    # reporting "new to the library" and duplicating a paper already held.
    doc, key = inbox.existing_document(ibx, "10.48550/arxiv.2501.00001",
                                       "A Study of Widgets: the Sequel")
    check(key == "Doe2025Widget",
          f"a DOI that matches nothing falls back to the title (got {key!r})")
    check(doc == "m1", "the title-matched entry resolves to its Mendeley id")

    check(inbox.existing_document(ibx, "10.9999/nope", "A Paper Nobody Has") == ("", ""),
          "a genuinely new paper is still reported as new")
    check(inbox.existing_document(ibx, "", "On Gadgets")[1] == "Roe2024Gadget",
          "the no-DOI title path still works")
    check(inbox.existing_document(ibx, "10.9999/nope", "A Study of Widgets")[1] == "",
          "a partial title does not match -- equality is still exact")

    print("\ninbox exit status (offline: an unreadable PDF needs no network)")
    ibox = out / "inbox"
    ibox.mkdir(exist_ok=True)
    (ibox / "junk.pdf").write_bytes(b"not a pdf at all")
    r = subprocess.run([sys.executable, str(Path(__file__).parent / "inbox.py"),
                        "--out", str(out), "--dry-run"],
                       capture_output=True, text=True, timeout=120)
    check(r.returncode != 0, f"a file it cannot identify exits nonzero (got {r.returncode})")
    check("junk.pdf" in r.stdout and "NOT filed" in r.stdout,
          "the unidentifiable file is named in the closing block")
    tail3 = "\n".join([ln for ln in r.stdout.strip().splitlines() if ln.strip()][-3:])
    check("junk.pdf" in tail3 or "NOT filed" in tail3,
          f"the problem survives a tail -3 (tail was: {tail3[:80]!r})")
    (ibox / "junk.pdf").unlink()

    print("\npdbxref.py: ranking the gap by demand, not by deposition size")
    import pdbxref as px

    ENTRIES = [
        {"rcsb_id": "1AAA", "rcsb_primary_citation":
            {"pdbx_database_id_DOI": "10.1000/HELD", "year": 2001, "journal_abbrev": "J. Held"}},
        {"rcsb_id": "2BBB", "rcsb_primary_citation":
            {"pdbx_database_id_DOI": "10.1000/wanted", "year": 2004, "journal_abbrev": "Neuron",
             "title": "A title\nsplit over\tlines"}},
        {"rcsb_id": "3CCC", "rcsb_primary_citation":
            {"pdbx_database_id_DOI": "10.1000/bulk", "year": 2010, "journal_abbrev": "J. Bulk"}},
        {"rcsb_id": "4DDD", "rcsb_primary_citation":
            {"pdbx_database_id_DOI": "10.1000/bulk", "year": 2010, "journal_abbrev": "J. Bulk"}},
        {"rcsb_id": "5EEE", "rcsb_primary_citation":
            {"pdbx_database_id_DOI": "10.1000/bulk", "year": 2010, "journal_abbrev": "J. Bulk"}},
        {"rcsb_id": "6FFF", "rcsb_primary_citation": {"pdbx_database_id_DOI": ""}},
    ]
    HAVE = ({"10.1000/held"}, {"778899"}, {"a held title by name"})
    A2P = {"1AAA": {"p1"}, "2BBB": {"p1", "p2", "p3", "p4"},
           "3CCC": {"p9"}, "4DDD": {"p9"}, "5EEE": {"p9"}}

    ranked, held, no_doi = px.crossref(ENTRIES, HAVE, A2P)
    check(held == 1, "a citation already in the library is not in the gap")
    check(no_doi == 1, "an entry with no primary DOI is counted separately, not dropped silently")

    # RCSB returns mixed-case DOIs; without lowering, every Science entry looks missing.
    ranked2, held2, _ = px.crossref(
        [{"rcsb_id": "7GGG", "rcsb_primary_citation": {"pdbx_database_id_DOI": "10.1000/HELD"}}],
        HAVE, {})
    check(held2 == 1 and not ranked2, "DOI comparison is case-insensitive")

    # 17% of this library's records carry no DOI, only a PMID. A DOI-only test
    # reported Kwong 2000 as missing and it was downloaded a second time.
    _, held3, _ = px.crossref([{"rcsb_id": "8HHH", "rcsb_primary_citation":
        {"pdbx_database_id_DOI": "10.1000/unknown", "pdbx_database_id_PubMed": 778899}}], HAVE, {})
    check(held3 == 1, "a PMID match counts as held when the DOI does not")
    _, held4, _ = px.crossref([{"rcsb_id": "9III", "rcsb_primary_citation":
        {"pdbx_database_id_DOI": "10.1000/unknown", "title": "A Held Title, By Name."}}], HAVE, {})
    check(held4 == 1, "a normalized title match counts as held")
    check(px.norm_title("A {Held} Title, By Name.") == "a held title by name",
          "titles normalize past braces, case and punctuation")

    # The whole point: one paper wanted by four beats a bulk deposition wanted by one.
    check(ranked[0][0] == "10.1000/wanted",
          f"ranked by citing papers, not by structure count (got {ranked[0][0]})")
    check(len(ranked[0][1]["ids"]) == 1 and len(ranked[0][1]["papers"]) == 4,
          "...even though the runner-up deposited three structures")
    check(len(ranked[1][1]["ids"]) == 3 and len(ranked[1][1]["papers"]) == 1,
          "the bulk deposition is grouped as one paper, ranked below")

    check(px.flat("A title\nsplit over\tlines") == "A title split over lines",
          "titles are flattened -- a newline in a title corrupted a TSV once")
    check(ranked[0][1]["title"] == "A title split over lines",
          "and crossref flattens them on the way in")

    print("\npdbrefs.py: an accession counts only when the text says it is one")
    import pdbrefs as pr

    got = pr.accessions_in("structures were taken from PDB ID 1ABC and PDB code: 2DEF.")
    check(set(got) == {"1ABC", "2DEF"}, f"the common cue forms are read ({sorted(got)})")

    # Papers gloss each entry as they list it; without stepping over the
    # parentheses the list ends at the first gloss.
    got = pr.accessions_in("such as PDB:1H2S (sensory rhodopsin II), 2A65 (a transporter), 1SU4 (an ATPase)")
    check(set(got) == {"1H2S", "2A65", "1SU4"},
          f"a glossed list keeps going past the first parenthetical ({sorted(got)})")

    # The trap that put a piece of software in the library's top ten structures.
    check(pr.accessions_in("prepared with PDB2PQR and pdb2gmx") == {},
          "an accession glued inside a word is not an accession")
    check(pr.accessions_in("prepared with PDB 2PQR59,60 as described") == {},
          "...nor when extraction splits that word with a space")

    # Shape alone matches years, and a bibliography is full of them.
    check(pr.accessions_in("Protein Data Bank, 1997, and see PDB 2001") == {},
          "years are not accessions even after a cue")

    # No cue, no accession -- which is what keeps mangled table cells out.
    check(pr.accessions_in("1SU44 rect 452 62,760 2A654 hexa 386") == {},
          "bare four-character tokens in a table are ignored")

    got = pr.accessions_in("deposited in the Protein Data Bank under accession code 6VXX")
    check(set(got) == {"6VXX"}, f"the deposition sentence form is read ({sorted(got)})")
    got = pr.accessions_in("see https://www.rcsb.org/structure/5FUU for details")
    check(set(got) == {"5FUU"}, f"an rcsb.org URL is read ({sorted(got)})")

    check(pr.accessions_in("PDB ID 1abc")["1ABC"] == 1, "accessions are normalized to upper case")

    print("\nfinding.py: a record is refused unless the quote is really there")
    import finding as fnd

    EXTRACT = (
        "---\nfrontmatter\n---\n\n"
        "<!-- p. 1 -->\n\nJournal of Things 12 (2020) 100-108 100\n"
        "The quick brown fox jumps over the lazy dog.\n\n"
        "<!-- p. 2 -->\n\nSome other text con\ufb01guration \u201cquoted\u201d here.\n"
        "Journal of Things 12 (2020) 100-108 101\n\n"
        "<!-- p. 3 -->\n\nThird page.\nJournal of Things 12 (2020) 100-108 102\n\n"
        "<!-- p. 4 -->\n\nFourth page.\nJournal of Things 12 (2020) 100-108 103\n"
    )

    check("quick brown fox" in fnd.page_text(EXTRACT, 1),
          "page_text returns the text under one marker")
    check("quick brown fox" not in (fnd.page_text(EXTRACT, 2) or ""),
          "and does not bleed into the next page")
    check(fnd.page_text(EXTRACT, 9) is None, "a missing marker page is None, not an exception")

    # Normalization: a quote must match across ligatures, curly quotes and wrapping.
    check(fnd.normalize("con\ufb01guration \u201cquoted\u201d")
          == fnd.normalize("configuration \"quoted\""),
          "ligatures and typographic quotes normalize to the same text")
    check(fnd.normalize("a\nb   c") == "a b c", "line wrapping does not defeat a quote")

    # The offset is DERIVED from recurring edge numbers, never taken on trust.
    check(fnd.derive_offset(EXTRACT) == (99, 4),
          f"a running footer yields the offset ({fnd.derive_offset(EXTRACT)})")

    # A number that appears on every page but is not a page number gives a different
    # offset each time, so it never accumulates a majority.
    NOISE = EXTRACT.replace("Journal of Things 12 (2020) 100-108", "Journal of Things 12 (2020)")
    check(fnd.derive_offset(NOISE) is None or fnd.derive_offset(NOISE)[0] != 99
          or fnd.derive_offset(NOISE)[1] >= 3,
          "removing the page number does not invent one")

    # THE BUG THAT PROMPTED EDGE_CHARS AND THE TIE RULE (Kendrick1990Calculated,
    # Hamerton1996Molecular). A real running head at the page edge competes with
    # small integers -- figure numbers, section numbers -- deeper in the text. The
    # window used to be wide enough to reach them, and the tie-break then preferred
    # the SMALLER offset, which is the coincidence every time: a wrong journal page,
    # stated with a derivation behind it.
    FILLER = "lorem ipsum dolor sit amet " * 12          # pushes the decoys past EDGE_CHARS
    TRAP = "".join(
        f"<!-- p. {n} -->\n\nJ. Mater. Sci. 31 (1996) {310 + n}\n"
        f"{FILLER}\nsee Figure {n} and Table {n} for detail\n{FILLER}\n\n"
        for n in (1, 2, 3, 4))
    got = fnd.derive_offset(TRAP)
    check(got is not None and got[0] == 310,
          f"the running head wins over decoys deeper in the page (got {got})")

    # And when the two really are tied, no answer is the answer. Every page carries
    # the head once and the decoy once, so both offsets have identical support.
    TIED = "".join(f"<!-- p. {n} -->\n\n{610 + n}\n\n{n}\n" for n in (1, 2, 3, 4))
    check(fnd.derive_offset(TIED) is None,
          "two equally supported offsets yield None rather than the smaller one")

    # Guard against the fix being 'tighten until nothing derives': the plain case
    # above must still derive, and a head at the very end of the page must too.
    TRAILING = "".join(f"<!-- p. {n} -->\n\nbody text here\n{FILLER}\n{500 + n}\n"
                       for n in (1, 2, 3, 4))
    check(fnd.derive_offset(TRAILING) == (500, 4),
          "a footer at the end of a long page is still found")

    # Two pages can agree by chance; the threshold demands a majority of pages.
    SHORT = "<!-- p. 1 -->\n\nx 5 y\n\n<!-- p. 2 -->\n\nx 6 y\n"
    check(fnd.derive_offset(SHORT) is None,
          "a two-page paper yields no offset rather than a coincidence")

    print("\ncsl_year: the issue year, not the online one")
    from mendeley_push import csl_year

    # The real shape of CHARMM36m's Crossref record: online Nov 2016, issue Jan 2017.
    check(csl_year({"published-online": {"date-parts": [[2016, 11, 7]]},
                    "published-print":  {"date-parts": [[2017, 1]]},
                    "issued":           {"date-parts": [[2016, 11, 7]]}}) == 2017,
          "published-print beats issued (the Advance Access case)")

    # Some records carry the issue date only under journal-issue.
    check(csl_year({"journal-issue": {"published-print": {"date-parts": [[2014]]}},
                    "issued": {"date-parts": [[2013, 11, 22]]}}) == 2014,
          "journal-issue.published-print is used when there is no published-print")

    # A preprint has no print date at all, and issued is then correct.
    check(csl_year({"issued": {"date-parts": [[2025, 7, 10]]}}) == 2025,
          "issued is the fallback for anything never printed")
    check(csl_year({"published-print": {"date-parts": [[2018]]},
                    "issued": {"date-parts": [[2018]]}}) == 2018,
          "agreement is not disturbed")
    check(csl_year({}) is None, "no date at all yields None, not a crash")
    check(csl_year({"issued": {"date-parts": [[None]]}}) is None,
          "a null date-part yields None")

    print("\nHTML entities from Crossref/Mendeley")
    # Real case: Crossref hands back "Molecular Systems Design &amp; Engineering".
    # Escaping without decoding first left "\\&amp;", which renders the entity
    # literally in the bibliography.
    check(mm.tex_escape("Molecular Systems Design &amp; Engineering")
          == r"Molecular Systems Design \& Engineering",
          "&amp; in a journal name decodes to a real ampersand")
    check(mm.tex_escape("ACS Applied Materials &amp; Interfaces")
          == r"ACS Applied Materials \& Interfaces",
          "the other affected journal name too")

    # Decoding must not stop a literal ampersand from being escaped.
    check(mm.tex_escape("Smith & Jones") == r"Smith \& Jones",
          "a literal & is still escaped")
    check(mm.tex_escape("AT&T") == r"AT\&T", "an & inside a word is still escaped")

    # Exactly one level, so a genuinely double-encoded string survives as text.
    check(mm.tex_escape("&amp;amp;") == r"\&amp;", "only one level of decoding is undone")

    check(mm.tex_escape("a &lt;b&gt; c") == "a <b> c", "&lt; and &gt; decode")
    check(mm.tex_escape("caf&eacute;") == "café", "named character entities decode")

    # URLs are emitted raw, so they carry their own decode -- a query string with
    # &amp; between parameters does not resolve when clicked.
    check(mm.html_decode("http://x/?a=1&amp;b=2") == "http://x/?a=1&b=2",
          "&amp; in a URL query string decodes")

    print("\nmendeley_edit: what gets PATCHed")
    import mendeley_edit as me

    DOC = {"title": "A paper", "year": 2016, "type": "generic",
           "source": "Nature Methods",
           "identifiers": {"doi": "10.1038/old", "issn": "1548-7091", "pmid": "27819658"}}

    # A field already equal to Mendeley's value must not be re-sent -- this is
    # what makes a re-run after a partial failure safe.
    patch, _ = me.plan_patch(DOC, {"year": 2016, "source": "Nature Methods"})
    check(patch == {}, f"fields already correct are skipped (got {patch})")

    patch, lines = me.plan_patch(DOC, {"year": 2017})
    check(patch == {"year": 2017}, "a changed field is patched")
    check(any("2016" in l for l in lines) and any("2017" in l for l in lines),
          "the diff shows both the old and the new value")

    # The rule that keeps a DOI correction from destroying other identifiers.
    patch, _ = me.plan_patch(DOC, {"identifiers": {"doi": "10.1126/new"}})
    check(patch["identifiers"]["doi"] == "10.1126/new", "the DOI is replaced")
    check(patch["identifiers"]["issn"] == "1548-7091"
          and patch["identifiers"]["pmid"] == "27819658",
          "identifiers is MERGED -- issn and pmid survive a DOI correction")

    patch, _ = me.plan_patch(DOC, {"identifiers": {"doi": "10.1038/old"}})
    check(patch == {}, "an identifiers block that changes nothing is not sent")

    # A field the document does not have yet is an addition, not a skip.
    patch, _ = me.plan_patch(DOC, {"pages": "71--73"})
    check(patch == {"pages": "71--73"}, "a field absent from the document is added")

    # null REMOVES. The merge above protects good identifiers; without an
    # explicit removal it also made a bad one impossible to delete, and a DOI
    # that resolves to an unrelated paper is worse than no DOI at all.
    patch, _ = me.plan_patch(DOC, {"identifiers": {"doi": None}})
    check(patch["identifiers"] == {"issn": "1548-7091", "pmid": "27819658"},
          "a null identifier is removed and the others survive")
    patch, _ = me.plan_patch(DOC, {"identifiers": {"doi": None, "isbn": "978"}})
    check(patch["identifiers"] == {"issn": "1548-7091", "pmid": "27819658", "isbn": "978"},
          "removal and addition compose in one edit")
    patch, _ = me.plan_patch(DOC, {"identifiers": {"doi": None, "issn": None, "pmid": None}})
    check(patch == {"identifiers": {}}, "removing every identifier sends an empty object")
    patch, _ = me.plan_patch(DOC, {"identifiers": {"arxiv": None}})
    check(patch == {}, "removing an identifier the document does not have is a no-op")

    patch, _ = me.plan_patch({"identifiers": {}}, {"identifiers": {"doi": "10.1/x"}})
    check(patch == {"identifiers": {"doi": "10.1/x"}},
          "merging into an empty identifiers block works")

    # Lin1999Effect: the footer is followed by a Wiley licence block of constant
    # length, so the printed page number sits ~410 characters from the end of
    # EVERY page -- outside any edge window, but in the same place every time.
    # The edge scan refuses, and refusing blocks a correct record: the operator's
    # only route was to drop --journal-page and lose the locator.
    BANNER = ("Downloaded from https://example.com/doi/10.1/x by Example University, "
              "Wiley Online Library on [21/09/2026]. See the Terms and Conditions "
              "on Wiley Online Library for rules of use; OA articles are governed "
              "by the applicable Creative Commons License")
    # The fixture carries BOTH sub-causes of the real failure: figure numbers that
    # vote for offset zero, and a licence block that pushes the true footer out of
    # the edge window. Either alone is survivable; together they produced a
    # confident "printed 8 (offset +0, derived from 7 pages)" -- silent, wrong, and
    # written into an append-only findings file.
    LIN = "".join(
        f"<!-- p. {n} -->\n\n{'body sentence here. ' * 12}Figure {n} shows the trend, "
        f"and Table {n} lists the values.\n{'more body text. ' * 12}\n"
        f"{1926 + n} LIN, SU, AND HONG\n{BANNER}\n\n"
        for n in range(1, 13))
    check(fnd.derive_offset(LIN) is None,
          f"no offset is invented from figure numbers when the footer is displaced "
          f"(got {fnd.derive_offset(LIN)})")
    check(fnd.confirm_offset(LIN, 8, 1934) is not None,
          "but the operator's own page number is confirmed by its consistent position")

    # The confirmation must be able to FAIL, or it is worse than no check at all.
    check(fnd.confirm_offset(LIN, 8, 1935) is None, "an off-by-one claim is not confirmed")
    check(fnd.confirm_offset(LIN, 8, 1933) is None, "nor is it off by one the other way")
    check(fnd.confirm_offset(LIN, 8, 8) is None, "nor a claim that the marker is the page")

    # A number that matches by coincidence moves around the page; a running head
    # does not. "Figure N" on marker N is the exact decoy that broke the old scan.
    SCATTER = "".join(
        f"<!-- p. {n} -->\n\n{'filler words here. ' * (3 * n)}Figure {n} shows the result.\n"
        f"{'more filler text. ' * (36 - 3 * n)}\n\n"
        for n in range(1, 13))
    check(fnd.confirm_offset(SCATTER, 8, 8) is None,
          "a number at a different place on every page confirms nothing")

    # And the tolerance is a real number, not a wish: a head that wanders further
    # than the bands between pages is not treated as the same place. The wander
    # has to be real in BOTH readings -- the body wraps onto more lines as it
    # grows, the way a text layer actually does, so the number moves in lines as
    # well as in characters. An earlier version of this decoy put the whole body
    # on one long line, which drifted the characters while pinning the number to
    # line 3 of every page -- that is a running head, and was passing only
    # because nothing yet counted lines.
    DRIFT = "".join(
        f"<!-- p. {n} -->\n\n" + "body.\n" * (10 + 25 * n) + f"{1926 + n}\n"
        + "tail padding.\n" * (2 + 20 * n) + "\n"
        for n in range(1, 13))
    check(fnd.confirm_offset(DRIFT, 8, 1934) is None,
          "a page number that drifts far between pages is not a running head")

    # The converse, and why LINE_BAND exists. Knight2015Memgen prints its running
    # head two lines from the end of all three pages, but the title page carries a
    # citation block below it, so the character distance reads 278 there against
    # 140 on the last page. One layout, two numbers, further apart than
    # POSITION_BAND -- and the operator's correct page number was refused.
    HEAD = "".join(
        f"<!-- p. {n} -->\n\n" + "body sentence.\n" * 20
        + f"{2896 + n} Knight and Hub\n"
        + "Downloaded from https://academic.oup.com/example by a user\n"
        + ("Bioinformatics, 31(17), 2015, 2897-2899 doi: 10.1093/example\n" if n == 1 else "")
        + "\n"
        for n in range(1, 4))
    check(fnd.confirm_offset(HEAD, 2, 2898) is not None,
          "a running head at a steady LINE is confirmed though the characters drift")
    check(fnd.confirm_offset(HEAD, 2, 2899) is None,
          "and that reading still refuses an off-by-one claim")

    print("\nzotero_attach: pairing, and the protocol it speaks")
    import zotero_attach as za

    # 149 linked_url bookmarks came across in the migration. They are links, not
    # attachments, and counting them is how the import's 2,894 became 2,745.
    kids = [{"key": "A1", "data": {"linkMode": "imported_file", "filename": "x.pdf"}},
            {"key": "A2", "data": {"linkMode": "linked_url"}},
            {"key": "A3", "data": {"linkMode": "imported_url", "filename": "y.pdf"}}]
    got_if = za.imported_files(kids)
    check([c["key"] for c in got_if] == ["A1"],
          "only imported_file children can receive bytes")

    one_local = [Path("K1.pdf")]
    one_item = [{"key": "A1", "data": {"linkMode": "imported_file", "filename": "orig.pdf"}}]
    pairs, prob = za.pair_attachments(one_local, one_item)
    check(prob is None and pairs == [(one_local[0], one_item[0])],
          "one archived file and one attachment is unambiguous")

    # Distinct extensions on both sides: pairable without trusting any ordering.
    two_local = [Path("K1.pdf"), Path("K1-2.cif")]
    two_items = [{"key": "B2", "data": {"linkMode": "imported_file", "filename": "s.cif"}},
                 {"key": "B1", "data": {"linkMode": "imported_file", "filename": "p.pdf"}}]
    pairs2, prob2 = za.pair_attachments(two_local, two_items)
    check(prob2 is None, f"distinct extensions pair ({prob2})")
    check(dict((p_.name, it["key"]) for p_, it in pairs2) == {"K1.pdf": "B1", "K1-2.cif": "B2"},
          "and pair by extension, not by the order Zotero happened to return")

    # What it must REFUSE. A wrong pairing files one paper's PDF under another's
    # citation key -- silent, and very hard to find later.
    same = [Path("K1.pdf"), Path("K1-2.pdf")]
    same_items = [{"key": "C1", "data": {"linkMode": "imported_file", "filename": "a.pdf"}},
                  {"key": "C2", "data": {"linkMode": "imported_file", "filename": "b.pdf"}}]
    _p, prob3 = za.pair_attachments(same, same_items)
    check(prob3 and "unambiguous" in prob3,
          f"two PDFs on one record are reported, never guessed ({prob3})")
    _p, prob4 = za.pair_attachments(same, one_item)
    check(prob4 and "against" in prob4, f"a count mismatch is reported ({prob4})")
    _p, prob5 = za.pair_attachments(one_local, [])
    check(prob5 and "no imported_file" in prob5,
          f"an archived file with nowhere to go is reported, not invented ({prob5})")
    check(za.pair_attachments([], one_item) == ([], None),
          "and a record with nothing archived is simply nothing to do")

    check(za.needs_upload({"data": {"filename": "x.pdf"}}), "no md5 means no bytes yet")
    check(not za.needs_upload({"data": {"filename": "x.pdf", "md5": "abc"}}),
          "an md5 means Zotero already holds it -- resumable by construction")

    check(za.upload_body("PRE", b"\x00bytes", "POST") == b"PRE\x00bytesPOST",
          "the upload body is exactly prefix + file + suffix")

    adir2 = tmp / "attach-order"
    adir2.mkdir()
    for name in ("Abrams2013Enhanced.pdf", "Abrams2013Enhanced-2.pdf",
                 "Abrams2013Enhanced-10.pdf", "Abrams2013Other.pdf",
                 "Vanommeslaeghe2009Charmm.-_charmm_g"):
        (adir2 / name).write_bytes(b"x")
    order = [p_.name for p_ in za.archived_for(adir2, "Abrams2013Enhanced")]
    check(order == ["Abrams2013Enhanced.pdf", "Abrams2013Enhanced-2.pdf",
                    "Abrams2013Enhanced-10.pdf"],
          f"attachments come back in archive order, 10 after 2 ({order})")
    check(all("Other" not in n for n in order),
          "and a different key that shares a prefix is not swept in")
    odd = za.archived_for(adir2, "Vanommeslaeghe2009Charmm")
    check([p_.name for p_ in odd] == ["Vanommeslaeghe2009Charmm.-_charmm_g"],
          f"a mangled extension is still that key's attachment 1 ({odd})")

    print("\nzotero_attach speaks the v3 upload protocol exactly")

    class FakeResp:
        def __init__(self, code=200, payload=None, headers=None):
            self.status_code, self._payload = code, payload or {}
            self.headers = headers or {}
        def json(self): return self._payload
        def raise_for_status(self):
            if self.status_code >= 400:
                raise AssertionError(f"HTTP {self.status_code}")

    class FakeSession:
        def __init__(self, replies): self.replies, self.calls = replies, []
        def post(self, url, data=None, headers=None, timeout=None):
            self.calls.append({"url": url, "data": data, "headers": headers or {}})
            return self.replies.pop(0)

    class FakeZ:
        def __init__(self, session): self.base, self.session = "https://api/users/9", session

    auth_payload = {"url": "https://s3/upload", "contentType": "multipart/form-data; boundary=x",
                    "prefix": "PRE", "suffix": "SUF", "uploadKey": "UK1"}
    sess = FakeSession([FakeResp(200, auth_payload)])
    w = za.ZoteroWriter(FakeZ(sess))
    got_auth = w.authorize("IT1", "paper.pdf", b"hello", 1700000000000)
    call = sess.calls[0]
    check(call["url"] == "https://api/users/9/items/IT1/file",
          f"authorization goes to the item's file endpoint ({call['url']})")
    check(call["headers"].get("If-None-Match") == "*",
          "with If-None-Match: * -- this attachment has no file yet")
    check(call["data"]["md5"] == _hashlib.md5(b"hello").hexdigest(),
          "the md5 is of the bytes being sent")
    check(call["data"]["filesize"] == 5 and call["data"]["mtime"] == 1700000000000,
          "filesize in bytes and mtime in MILLISECONDS, which the API insists on")
    check(got_auth["uploadKey"] == "UK1", "and the authorization is returned to the caller")

    check(za.upload_body(auth_payload["prefix"], b"hello", auth_payload["suffix"])
          == b"PREhelloSUF", "the body sent to S3 is the concatenation, not a re-encode")

    sess2 = FakeSession([FakeResp(204)])
    za.ZoteroWriter(FakeZ(sess2)).register("IT1", "UK1")
    check(sess2.calls[0]["data"] == {"upload": "UK1"},
          "registration carries only the upload key")
    check(sess2.calls[0]["headers"].get("If-None-Match") == "*",
          "and the same precondition, so a race cannot overwrite a file that arrived meanwhile")

    # 429 with Retry-After: 0 so the test does not sit there sleeping.
    sess3 = FakeSession([FakeResp(429, {}, {"Retry-After": "0"}),
                         FakeResp(200, auth_payload)])
    w3 = za.ZoteroWriter(FakeZ(sess3))
    again = w3.authorize("IT1", "paper.pdf", b"hello", 1)
    check(again["uploadKey"] == "UK1" and len(sess3.calls) == 2,
          f"a 429 is retried after Retry-After rather than failing the run ({len(sess3.calls)})")

    print("\nzotero_attach makes Zotero describe the file it is given")

    # Vanommeslaeghe2009Charmm: application/octet-stream for a complete PDF,
    # under Mendeley's mangled filename. Both crossed the migration intact.
    bad = {"key": "V1", "version": 42,
           "data": {"linkMode": "imported_file",
                    "filename": "2010-CHARMM_general_force_field_biolo.-_charmm_g",
                    "contentType": "application/octet-stream"}}
    want = za.plan_metadata(bad, Path("Vanommeslaeghe2009Charmm.pdf"), True)
    check(want == {"filename": "Vanommeslaeghe2009Charmm.pdf",
                   "contentType": "application/pdf"},
          f"a mangled name and a wrong type are both corrected ({want})")

    good = {"key": "G1", "version": 7,
            "data": {"linkMode": "imported_file", "filename": "Abrams2013Enhanced.pdf",
                     "contentType": "application/pdf"}}
    check(za.plan_metadata(good, Path("Abrams2013Enhanced.pdf"), True) == {},
          "an attachment already describing itself correctly is left alone, "
          "so a re-run after a partial failure sends nothing")

    # Must REFUSE to invent a type. This function knows what a PDF looks like
    # and nothing else; guessing 'application/pdf' for bytes that are not one
    # is how a library acquires metadata nobody can trust.
    notpdf_item = {"key": "N1", "version": 3,
                   "data": {"linkMode": "imported_file", "filename": "old.avi",
                            "contentType": "video/x-msvideo"}}
    want_np = za.plan_metadata(notpdf_item, Path("Shan2011How.avi"), False)
    check(want_np == {"filename": "Shan2011How.avi"},
          f"a non-PDF is renamed but keeps its declared type ({want_np})")

    check(za.item_version({"version": 42, "data": {}}) == 42, "the item version is read")
    check(za.item_version({"data": {"version": 11}}) == 11, "from data when it is only there")
    check(za.item_version({"data": {}}) is None,
          "and is None when absent -- a PATCH without one is refused, not guessed")

    class FakePatchSession(FakeSession):
        def patch(self, url, json=None, headers=None, timeout=None):
            self.calls.append({"url": url, "json": json, "headers": headers or {}})
            return self.replies.pop(0)

    ps = FakePatchSession([FakeResp(204)])
    ok_patch = za.ZoteroWriter(FakeZ(ps)).patch_item("V1", 42, {"contentType": "application/pdf"})
    check(ok_patch is True, "a 204 is a successful patch")
    check(ps.calls[0]["url"] == "https://api/users/9/items/V1",
          f"PATCH addresses the item ({ps.calls[0]['url']})")
    check(ps.calls[0]["headers"].get("If-Unmodified-Since-Version") == "42",
          "carrying the version it read, so a concurrent edit wins instead of us")
    check(ps.calls[0]["json"] == {"contentType": "application/pdf"},
          "and sends ONLY the changed field -- Zotero leaves the rest untouched")

    ps2 = FakePatchSession([FakeResp(412)])
    check(za.ZoteroWriter(FakeZ(ps2)).patch_item("V1", 1, {"filename": "x.pdf"}) is False,
          "a 412 is reported as a conflict, never retried: a retry would "
          "overwrite whatever edit caused it")

    print("\nPEP 723 headers: eleven copies of the dependency list, kept honest")

    # There is no pyproject.toml here on purpose -- every script carries its own
    # inline header so `uv run --script` needs nothing installed, which is what
    # makes this work on a bare Windows laptop. The cost is eleven copies of the
    # same facts, and nothing but this check would notice them drifting apart:
    # a script whose header forgets a dependency fails at import time on a
    # machine that has not run its sibling first, which is exactly the machine
    # nobody is watching.
    HERE = Path(__file__).parent
    PINS = {"requests": "requests>=2.31", "pymupdf": "pymupdf>=1.24"}
    scripts = sorted(HERE.glob("*.py"))
    check(len(scripts) >= 9, f"found the scripts to check ({len(scripts)})")

    for script in scripts:
        src = script.read_text(encoding="utf-8")
        head = re.search(r"^# /// script$(.*?)^# ///$", src, re.S | re.M)
        if not head:
            check(False, f"{script.name} has a PEP 723 header")
            continue
        dep = re.search(r"dependencies\s*=\s*\[([^\]]*)\]", head.group(1))
        declared = set(re.findall(r'"([^"]+)"', dep.group(1) if dep else ""))

        # What it actually needs: a direct import, or mendeley_mirror's own
        # requests (pymupdf is imported lazily there, so it is NOT transitive).
        body = src[head.end():]
        wants = set()
        if re.search(r"^\s*import requests", body, re.M) or "from mendeley_mirror import" in body:
            wants.add(PINS["requests"])
        if re.search(r"^\s*import pymupdf", body, re.M):
            wants.add(PINS["pymupdf"])

        check(declared == wants,
              f"{script.name} declares exactly what it imports "
              f"(declared {sorted(declared)}, needs {sorted(wants)})")

    print("\nthe version is written down once, and the mirror records it")
    check(re.fullmatch(r"\d+\.\d+\.\d+", mm.__version__) is not None,
          f"__version__ is a three-part version ({mm.__version__})")
    check(sum(1 for s_ in scripts if re.search(r'^__version__\s*=', s_.read_text(encoding="utf-8"), re.M)) == 1,
          "exactly one file defines __version__")

    print("\n" + ("ALL CHECKS PASSED" if not fails else f"{len(fails)} FAILURES: {fails}"))
    print("sample entry:\n")
    print(bib.split("@")[1][:600])
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
