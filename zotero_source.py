#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pymupdf>=1.24", "requests>=2.31"]
# ///
"""Read a Zotero library in the shape the mirror's generators already consume.

The first piece of the Zotero-backed refresh (ROADMAP item 1). It translates
Zotero items into the document dicts that `bib_entry`, `assign_citekeys`,
`write_index` and `annotation_markdown` already take, so the generators do not
change at all and the thing that changes is where documents come from.

**It can be verified before it is integrated, which is why it was built first.**
`~/Sync/library/library.bib` is a frozen, known-good rendering of the same
2,739 records produced by the Mendeley path, and `citekeys.json` holds a
verified bijection between the two backends' ids. So this module can regenerate
every entry from Zotero and diff it against that file:

    uv run --script zotero_source.py --compare

A difference is a translation defect or a real improvement, and the diff makes
you say which. That is a better check than any fixture, because the oracle was
produced by an independent path over the same library.

**Citation keys must not move.** `citekeys.json` already holds a
`zotero:<itemKey>` entry for all 2,739 keys, so `assign_citekeys` finds each key
already assigned and keeps it — which is the whole reason the ids were
namespaced on 2026-09-24 before any key was assigned from a second source. The
backend must therefore be passed explicitly rather than taken from
`mendeley_mirror.BACKEND`, whose default is still `"mendeley"`. The repo's
CLAUDE.md names this as the rule a second backend will be tempted to break.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mendeley_mirror import (DEFAULT_OUT, __version__, assign_citekeys,  # noqa: E402
                             bib_entry, harvest_attachments, load_json,
                             annotation_markdown, mirror_state_dir, qualify,
                             require_ocr_stack,
                             save_json, write_bibtex, write_extraction_report,
                             write_folders, write_index, write_status)
from zotero_attach import load_pairings  # noqa: E402
from zotero_migrate import Zotero, load_zotero_credentials, parse_bib, year_of  # noqa: E402

BACKEND = "zotero"

# Zotero's itemType to the vocabulary TYPE_MAP already speaks. Anything absent
# falls through to "generic", which TYPE_MAP renders as @misc -- the same place
# an unrecognised Mendeley type went, so an unmapped type degrades rather than
# raising.
ITEM_TYPES = {
    "journalArticle": "journal",
    "magazineArticle": "magazine_article",
    "newspaperArticle": "newspaper_article",
    "book": "book",
    "bookSection": "book_section",
    "encyclopediaArticle": "encyclopedia_article",
    "dictionaryEntry": "encyclopedia_article",
    "conferencePaper": "conference_proceedings",
    "thesis": "thesis",
    "report": "report",
    "preprint": "working_paper",
    "manuscript": "working_paper",
    "webpage": "web_page",
    "blogPost": "web_page",
    "computerProgram": "computer_program",
    "patent": "patent",
}

# Where the venue lives depends on the type, and getting this wrong is not
# cosmetic: four entry types were silently dropping the venue until 2026-09-30,
# which left 28 references carrying volume, pages and a DOI and no journal.
VENUE_FIELDS = ("publicationTitle", "bookTitle", "proceedingsTitle",
                "encyclopediaTitle", "dictionaryTitle", "blogTitle",
                "websiteTitle", "repository")

PMID_RE = re.compile(r"\bPMID:\s*(\d+)", re.I)

# The migration parked the venue of every NON-journalArticle in `extra`, as a
# "Publication Title:" line, and reading only the typed fields missed 26 records
# entirely. The typed field still wins where it exists -- for the 28
# journalArticles it is the right answer and `extra` holds nothing.
EXTRA_VENUE_RE = re.compile(r"^[ \t]*Publication Title[ \t]*:[ \t]*(.+)$", re.M | re.I)


# Zotero's PRIMARY creator type depends on the item type, and reading only
# "author" dropped every inventor on a patent -- 11 of them across this
# library's three, including Cameron's own, which generated with no author at
# all under a citation key that says Abrams. A creator type in neither set is
# treated as an author and NAMED: an extra author is visible in the
# bibliography, a missing one is not, so the fail-visible direction is the one
# to default to.
PRIMARY_CREATORS = {"author", "inventor", "programmer", "presenter", "director",
                    "podcaster", "interviewee", "artist", "cartographer",
                    "performer", "sponsor"}
SECONDARY_CREATORS = {"editor", "seriesEditor", "translator", "contributor",
                      "bookAuthor", "reviewedAuthor", "commenter", "wordsBy",
                      "scriptwriter", "producer", "guest", "counsel",
                      "attorneyAgent", "recipient", "castMember"}


def creator_role(c: dict) -> str:
    """`primary`, `editor`, `secondary`, or `unknown` -- never silently dropped."""
    t = c.get("creatorType") or "author"
    if t in PRIMARY_CREATORS:
        return "primary"
    if t == "editor":
        return "editor"
    if t in SECONDARY_CREATORS:
        return "secondary"
    return "unknown"


def looks_like_a_person(name: str) -> str:
    """Why a single-field creator looks like a person rather than an organisation.

    Reporting only. Zotero storing a name in one field is Zotero asserting the
    name is unstructured, and `{Braces}` are the faithful rendering of that --
    which is correct for `{WHO}` and wrong for `{J. B. Ames}`, where BibTeX then
    reads the whole string as a surname. Telling the two apart is a judgement
    about someone's library, so this names candidates and changes nothing.
    """
    n = (name or "").strip()
    if re.match(r"^(?:[A-Z]\.\s*)+[A-Z][a-z]", n):
        return "initials then a capitalised word"
    if n.count(" ") in (1, 2) and n.replace(" ", "").isalpha() and n.istitle():
        return "two or three capitalised words, no organisation marker"
    return ""


def person(c: dict) -> dict:
    """One Zotero creator as the generators expect a person.

    Zotero stores either `firstName`/`lastName` or a single `name` for a
    corporate or single-field author. `first_author_surname` reads `last_name`
    first and falls back to `name`, so a single-field creator is carried as
    `name` rather than being split on a guess -- splitting "Research Council of
    Norway" into a first and last name is how a bibliography acquires an author
    called Norway.
    """
    if c.get("name"):
        return {"name": c["name"]}
    return {"first_name": c.get("firstName", ""), "last_name": c.get("lastName", "")}


def identifiers_of(data: dict) -> dict:
    """DOI, ISSN, ISBN and PMID, in the nested shape `bib_entry` reads.

    Zotero keeps these as separate top-level fields, which is why
    `zotero_edit.py` needs no `identifiers` merge. PMID has no field of its own
    and conventionally lives in `extra` as `PMID: 12345`, which is where
    Zotero's own translators put it.
    """
    out = {}
    for src, dst in (("DOI", "doi"), ("ISSN", "issn"), ("ISBN", "isbn")):
        if (data.get(src) or "").strip():
            out[dst] = data[src].strip()
    m = PMID_RE.search(data.get("extra") or "")
    if m:
        out["pmid"] = m.group(1)
    return out


def item_to_doc(item: dict) -> dict:
    """A Zotero item as the document dict every generator in the mirror takes.

    The `id` is the Zotero item key, bare. Stored ids are namespaced and live
    ones are not -- the mirror's second structural rule -- and this is a live
    one, so `qualify()` is applied where it is stored and not here.
    """
    data = item.get("data") or item
    creators = data.get("creators") or []
    authors = [person(c) for c in creators
               if creator_role(c) in ("primary", "unknown")]
    editors = [person(c) for c in creators if creator_role(c) == "editor"]

    venue = ""
    for field in VENUE_FIELDS:
        if (data.get(field) or "").strip():
            venue = data[field].strip()
            break
    if not venue:
        # Checked for EVERY type, not only the ones a hypothesis predicted.
        # The library session's first reading of the 54 missing venues looked
        # only here and concluded 15 were unrecoverable; its second looked only
        # at publicationTitle and concluded they were all a fix landing. Both
        # were wrong in the same way -- the answer needed every venue-bearing
        # field, and no record's venue turned out to be lost at all.
        m = EXTRA_VENUE_RE.search(data.get("extra") or "")
        if m:
            venue = m.group(1).strip()

    # A patent carries no `date`; its year is in `issueDate`. All three of this
    # library's patents read as year-less without this, which is exactly how the
    # migration checker lost them and scored 2,735 of 2,735.
    year = year_of(data.get("date") or data.get("issueDate") or "")

    doc = {
        "id": item.get("key") or data.get("key"),
        "type": ITEM_TYPES.get(data.get("itemType"), "generic"),
        "title": (data.get("title") or "").strip(),
        "authors": authors,
        "editors": editors,
        "year": int(year) if year.isdigit() else None,
        "source": venue,
        "publisher": (data.get("publisher") or "").strip(),
        "city": (data.get("place") or "").strip(),
        "volume": (data.get("volume") or "").strip(),
        "issue": (data.get("issue") or "").strip(),
        "pages": (data.get("pages") or data.get("numPages") or "").strip(),
        "abstract": (data.get("abstractNote") or "").strip(),
        "identifiers": identifiers_of(data),
        "keywords": [t.get("tag") for t in (data.get("tags") or []) if t.get("tag")],
        "institution": (data.get("university") or data.get("institution")
                        or data.get("assignee") or "").strip(),
    }
    if (data.get("url") or "").strip():
        doc["websites"] = [data["url"].strip()]
    return {k: v for k, v in doc.items() if v not in ("", [], None, {})} | {"id": doc["id"]}


def annotation_to_mendeley(a: dict) -> dict:
    """One Zotero annotation in the shape `annotation_markdown` already renders.

    Zotero keeps annotations as child items of the ATTACHMENT, not of the
    record, which is why a run that only walks an item's own children sees none
    of them. The fields are `annotationType`, `annotationText` (the highlighted
    passage), `annotationComment` (what the reader wrote about it),
    `annotationPageLabel` and `annotationSortIndex`.

    **Ordering comes from `annotationSortIndex`, not from the rectangles.**
    It is `pageIndex|offset|y`, zero-padded, and already in reading order --
    whereas PDF rectangle coordinates increase *upward*, so sorting by a raw
    `y` would print every page backwards. `annotation_markdown` sorts on
    `(page, top_left.y)`, so the offset field goes in as that `y` and the
    existing sort comes out right.

    The printed page label is preferred over the index when it is a number,
    because a page label is what a reader cites; `pageIndex` is zero-based and
    counts the cover.
    """
    data = a.get("data") or a
    sort = (data.get("annotationSortIndex") or "").split("|")
    page_label = (data.get("annotationPageLabel") or "").strip()
    if page_label.isdigit():
        page = int(page_label)
    elif sort and sort[0].strip().isdigit():
        page = int(sort[0]) + 1
    else:
        page = 0
    y = int(sort[1]) if len(sort) > 1 and sort[1].strip().isdigit() else 0

    kind = data.get("annotationType") or "annotation"
    text = (data.get("annotationText") or "").strip()
    comment = (data.get("annotationComment") or "").strip()
    if kind in ("highlight", "underline"):
        kind = "highlight"
        # A highlight carrying a comment is two things, and the comment is the
        # reader's own words -- the part that cannot be recovered from the PDF.
        if comment:
            text = f"{text}\n  \n  — {comment}" if text else comment
    else:
        kind, text = "note", comment or text
    return {"type": kind, "text": text,
            "positions": [{"page": page, "top_left": {"y": y}}]}


class ZoteroSource:
    """List documents, files and annotations from a Zotero library.

    Deliberately the same four questions the Mendeley client is asked, so the
    refresh can take either. `fetch_file` is NOT among them: Zotero served no
    attachment bytes for a large minority of this library before 2026-10-02 and
    the local archive is the reliable source, so locating bytes belongs to
    `find_archived` and not to the backend.
    """

    def __init__(self, z: Zotero):
        self.z = z

    def documents(self) -> list:
        items = [i for i in self.z.items_top()
                 if (i.get("data") or {}).get("itemType") not in ("attachment", "note")]
        return [item_to_doc(i) for i in items]

    def files_by_doc(self, doc_ids: list) -> dict:
        out = {}
        for key in doc_ids:
            kids = self.z.paged(f"items/{key}/children", quiet=True)
            files = []
            for c in kids:
                d = c.get("data") or {}
                if d.get("linkMode") not in ("imported_file", "imported_url"):
                    continue
                files.append({"id": c.get("key"), "file_name": d.get("filename") or "",
                              "mime_type": d.get("contentType") or "",
                              "filehash": d.get("md5") or ""})
            if files:
                out[key] = files
        return out

    def annotations_by_doc(self, files_by_doc: dict) -> dict:
        """Annotations for each record, in ONE query, grouped by parent.

        Zotero hangs an annotation off the attachment rather than off the
        record, so these have to be matched back through the attachment map.

        **It asks for every annotation at once rather than walking each
        attachment's children.** The first version made one request per
        attachment -- 2,745 of them -- and `items/HNH4IS55/children`, the `.avi`
        on `Shan2011How`, answers **400**. One attachment the API would not
        describe took down a run that had 2,732 extractions still to do. Asking
        `itemType=annotation` once is faster by three orders of magnitude and
        has no per-attachment failure to be killed by.

        A failure here degrades to "no annotations" and says so. Annotations are
        the smallest thing this refresh produces -- this library has roughly two
        -- and they have no business being able to stop the extraction.
        """
        owner = {f["id"]: doc_id
                 for doc_id, files in files_by_doc.items() for f in files}
        out: dict = {}
        try:
            anns = self.z.paged("items", quiet=True, itemType="annotation")
        except Exception as exc:
            print(f"  ! annotations could not be read ({type(exc).__name__}: "
                  f"{exc}); continuing without them")
            return {}
        for a in anns:
            parent = (a.get("data") or {}).get("parentItem")
            doc_id = owner.get(parent)
            if doc_id:
                out.setdefault(doc_id, []).append(annotation_to_mendeley(a))
        return out

    def collections(self) -> tuple:
        """Zotero collections as `(folders, folder_docs)`, the shape write_folders takes.

        The migration put the four Mendeley folders inside an import wrapper, so
        their names gain one level of prefix -- recorded in ROADMAP item 1 when
        the import was done, and visible here as the parent in each name.
        """
        folders, folder_docs = [], {}
        try:
            raw = self.z.paged("collections", quiet=True)
        except Exception as exc:
            print(f"  ! collections could not be read ({type(exc).__name__}: "
                  f"{exc}); continuing without them")
            return [], {}
        for c in raw:
            data = c.get("data") or {}
            key = c.get("key") or data.get("key")
            folders.append({"id": key, "name": data.get("name") or "(unnamed)"})
            folder_docs[key] = [i.get("key") for i in
                                self.z.paged(f"collections/{key}/items/top", quiet=True)]
        return folders, folder_docs


ISSN_RE = re.compile(r"^\d{4}-\d{3}[\dXx]$")


def looks_like_issn(v: str) -> bool:
    return bool(ISSN_RE.match((v or "").strip()))


def looks_like_isbn(v: str) -> bool:
    bare = re.sub(r"[^0-9Xx]", "", v or "")
    return len(bare) in (10, 13)


ARXIV_RE = re.compile(r"(?:arxiv\s*:\s*)?([a-z-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?",
                      re.I)


def norm_arxiv_all(extra: str) -> set:
    """Every arXiv id already present in `extra`, normalised."""
    return {m.group(1).lower() for m in ARXIV_RE.finditer(extra or "")}


def norm_arxiv(value: str) -> str:
    """An arXiv id without its prefix or version, for comparing forms.

    The frozen bib holds all three shapes -- `arXiv:1401.0387v1`, `1106.1296`,
    `cond-mat/0510639` -- and a containment test on the literal string matched
    none of them against what Zotero already had. 14 of 31 were skipped as
    "already present" on the strength of the word "arXiv" appearing somewhere in
    `extra`, which is not the same question.
    """
    m = ARXIV_RE.search(value or "")
    return m.group(1).lower() if m else (value or "").strip().lower()


def same_identifier(a: str, b: str) -> bool:
    """Two identifiers that differ only in punctuation or case are the same one."""
    norm = lambda v: re.sub(r"[^0-9A-Za-z]", "", v or "").upper()
    return bool(norm(a)) and norm(a) == norm(b)


ISSN_TOKEN = re.compile(r"\b\d{4}-?\d{3}[\dXx]\b")


def issn_tokens(value: str) -> set:
    """Every ISSN-shaped token in a string, normalised.

    The word boundaries matter: they are what stops an eight-digit window
    inside `1493605872292` from reading as an ISSN. A thirteen-digit run has no
    boundary in the middle of it, so a genuine junk string yields nothing and a
    real ISSN yields itself.
    """
    return {re.sub(r"[^0-9Xx]", "", t).upper() for t in ISSN_TOKEN.findall(value or "")}


def classify_isbn_value(value: str, zotero_issn: str) -> str:
    """`same`, `differs` or `junk` -- three facts behind one action.

    The frozen bib's `isbn` field often holds a PAIR with labels, as PubMed
    renders them: `1091-6490 (Electronic)\r0027-8424 (Linking)`. That is the
    journal's electronic and print ISSN, and Zotero stores one of the two --
    `Yun2008Mutation` and `Yao2015Viral` carry the identical bib value and
    Zotero kept the opposite member in each. A whole-string comparison calls
    both of those "not this record's identifier in any format", which is false,
    and it was about to be presented as a finding someone would act on.

    So the test is whether the value CONTAINS an ISSN-shaped token matching the
    record's, not whether the whole string equals it. What is left over splits
    again: a value with ISSN structure that matches nothing is a real
    disagreement about which ISSN this journal has, and a value with no ISSN
    structure at all -- `1060510618`, `3014024724` -- is the junk worth
    reporting. Same action, three different things to say.
    """
    toks = issn_tokens(value)
    if not toks:
        return "junk"
    if toks & issn_tokens(zotero_issn):
        return "same"
    return "differs"


def clean_url(value: str) -> str:
    """A single http(s) URL, or "" if the value is not one.

    `Alberty1958Application`'s `url` in the frozen bib is three URLs joined by a
    literal `\n`: a publisher link, a `papers2://` scheme, and a local Mac path
    under someone else's home directory. Offering that as a `url` put a stranger's
    filesystem into a live library.

    It does NOT take the first of several and offer that. A concatenation is a
    defect in the source and which part is canonical is a judgement about
    someone's library -- the same reason the single-field author names are
    reported rather than split. The value is refused and named, with its parts
    shown, so the decision is cheap for whoever makes it.
    """
    v = (value or "").strip()
    if not v or "\\n" in v or "\n" in v or any(c.isspace() for c in v):
        return ""
    if not re.match(r"^https?://[^\s]+$", v, re.I):
        return ""
    return v


def can_hold(data: dict, field: str) -> bool:
    """Does this item's TYPE have that field at all?

    Zotero returns every field valid for an item type, including the empty
    ones, so absence from `data` means the type has no such field -- and
    `zotero_edit.py` refuses an edit naming one, correctly. A generator that
    emits what its own consumer refuses has a missing check, not a typo: this
    one assigned a typed `ISBN` to 103 journalArticles, which have none, and the
    refusal then blocked 1,174 PMIDs queued behind it in the same file.
    """
    return field in data


def extra_with(extra: str, label: str, value: str) -> str:
    """Zotero's `extra`, with `label: value` added and whatever was there kept.

    `extra` is free text and a PATCH replaces it, so an edit that restores a
    PMID has to carry everything else the field already held. Appending blind
    would also duplicate a line on a re-run, which is why an existing label is
    matched before anything is added.
    """
    lines = [ln for ln in (extra or "").splitlines() if ln.strip()]
    if any(ln.strip().lower().startswith(f"{label.lower()}:") for ln in lines):
        return extra or ""
    return "\n".join(lines + [f"{label}: {value}"])


def rescue_identifiers(out: Path) -> int:
    """Emit a zotero_edit.py edits file restoring identifiers Zotero does not have.

    `--compare` found 1,174 PMIDs and 31 arXiv ids present in the frozen
    `library.bib` and absent from Zotero. The migration did not carry them, and
    **the frozen mirror is currently the only place they exist** -- which makes
    overwriting `library.bib` with a Zotero-backed refresh a destructive act
    until this has run.

    Two things it deliberately does not do.

    It does not restore an `isbn` that is not shaped like one. 45 of the frozen
    bib's 201 `isbn` values are ISSNs that Mendeley mislabelled, and copying a
    known defect into the live library because it is in a file we trust is how a
    defect becomes permanent. Those are routed to `ISSN` and reported.

    And it does not append blindly to `extra`: the field is free text, a PATCH
    replaces it, and a re-run must not double a line.

    **Population note**, because the counts here differ from a count taken off
    the bib directly: this walks every entry in the frozen bib, but the `isbn`
    branch skips any record whose Zotero item **already holds an ISBN** --
    there is nothing to restore for those. A tally of "every isbn-bearing record
    with a zotero key" is therefore larger by exactly the books Zotero already
    has right, and the difference is not a gap in what `--compare` called lost.
    """
    bib = out / "library.bib"
    keymap = load_json(mirror_state_dir(out) / "citekeys.json", {})
    by_citekey = {v: k.split(":", 1)[1] for k, v in keymap.items()
                  if k.startswith("zotero:")}
    frozen = {k: f for k, _t, f in parse_bib(bib.read_text(encoding="utf-8"))}

    api_key, user_id = load_zotero_credentials()
    z = Zotero(api_key, user_id)
    items = {i.get("key"): (i.get("data") or {}) for i in z.items_top()}

    edits: dict = {}
    counts = {"pmid": 0, "eprint": 0, "issn": 0, "isbn": 0, "doi": 0, "url": 0,
              "reclassified": 0, "already_same": 0, "issn_differs": 0,
              "discarded_junk": 0}
    skipped: list = []
    no_field: list = []
    junk: list = []
    differs: list = []
    bad_url: list = []
    for citekey, fields in sorted(frozen.items()):
        item_key = by_citekey.get(citekey)
        data = items.get(item_key or "")
        if data is None:
            skipped.append(f"{citekey}: no Zotero item")
            continue
        edit, extra = {}, data.get("extra") or ""
        want_pmid = (fields.get("pmid") or "").strip()
        if want_pmid and "pmid" not in extra.lower():
            extra = extra_with(extra, "PMID", want_pmid)
            counts["pmid"] += 1
        want_arxiv = (fields.get("eprint") or "").strip()
        if want_arxiv and norm_arxiv(want_arxiv) not in norm_arxiv_all(extra):
            extra = extra_with(extra, "arXiv", want_arxiv)
            counts["eprint"] += 1
        if extra != (data.get("extra") or ""):
            edit["extra"] = extra

        # Every typed field is checked against the item TYPE before it is
        # offered. Shape alone cannot decide where a value belongs: an
        # ISBN-shaped value on a journalArticle is a mislabel in the source,
        # exactly like the 45 ISSN-shaped `isbn` values, and the type is what
        # says so.
        for field, zfield in (("doi", "DOI"), ("issn", "ISSN"), ("url", "url")):
            want = (fields.get(field) or "").strip()
            if not want or (data.get(zfield) or "").strip():
                continue
            if not can_hold(data, zfield):
                no_field.append(f"{citekey}: {zfield} on a {data.get('itemType')}")
                continue
            if zfield == "url" and not clean_url(want):
                bad_url.append(f"{citekey}: {want[:120]!r}")
                continue
            edit[zfield] = want
            counts[field] += 1
        want_isbn = (fields.get("isbn") or "").strip()
        if want_isbn and not (data.get("ISBN") or "").strip():
            # Three outcomes that call for OPPOSITE actions, and they were one
            # message until 2026-10-02: "on a journalArticle, which holds
            # neither" was printed for 91 records whose type holds an ISSN
            # perfectly well and already has one. A reader would have concluded
            # Zotero cannot store an ISSN on a journal article.
            if looks_like_isbn(want_isbn) and can_hold(data, "ISBN"):
                edit["ISBN"] = want_isbn
                counts["isbn"] += 1
            elif (data.get("ISSN") or "").strip():
                # Two different FACTS behind one identical action, and merging
                # them was the same conflation as "holds neither" a level down.
                # Only the second says the frozen bib is carrying junk.
                verdict = classify_isbn_value(want_isbn, data["ISSN"])
                if verdict == "same":
                    counts["already_same"] += 1
                elif verdict == "differs":
                    counts["issn_differs"] += 1
                    differs.append(f"{citekey}: bib {want_isbn!r} vs Zotero ISSN "
                                   f"{data['ISSN']!r} -- both ISSN-shaped, neither matches")
                else:
                    counts["discarded_junk"] += 1
                    junk.append(f"{citekey}: bib isbn {want_isbn!r} has no ISSN "
                                f"structure at all (Zotero has {data['ISSN']!r})")
            elif can_hold(data, "ISSN") and (looks_like_issn(want_isbn)
                                             or not can_hold(data, "ISBN")):
                edit["ISSN"] = want_isbn
                counts["reclassified"] += 1
            elif not can_hold(data, "ISSN") and not can_hold(data, "ISBN"):
                no_field.append(f"{citekey}: a {data.get('itemType')} has neither "
                                f"an ISSN nor an ISBN field for {want_isbn!r}")
            else:
                skipped.append(f"{citekey}: isbn {want_isbn!r} is neither shape "
                               "and the record has no ISSN to compare it against")
        if edit:
            edits[citekey] = edit

    path = out / ".mirror" / "rescue-identifiers.json"
    path.write_text(json.dumps(edits, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"# rescue-identifiers — offprint {__version__}\n")
    if not edits:
        # Said FIRST and in these words. This list was split four times, and
        # every split was a real distinction -- but a breakdown that needs a
        # fifth axis before it stops implying a problem is a breakdown that
        # should lead with the verdict. Roughly half of what follows is not a
        # defect in anything, and the rest sits in a field that no longer feeds
        # any output, on records that already carry a correct identifier.
        print("**Nothing to send. No action is required of anyone.**")
        print("Everything the frozen library.bib held uniquely is in Zotero.")
        print("The breakdown below is for the curious, not a work list.\n")
    print(f"- records needing an edit: {len(edits)}")
    for k, v in counts.items():
        print(f"- {k}: {v}")
    print(f"- already in Zotero, same value differently formatted: "
          f"{counts['already_same']}")
    print(f"- bib and Zotero hold DIFFERENT ISSNs for the record: {len(differs)}")
    print(f"- bib value has no ISSN structure at all: {len(junk)}")
    print(f"- url refused as malformed: {len(bad_url)}")
    print(f"- not restorable because the item type has no such field: {len(no_field)}")
    print(f"- not restorable, named below: {len(skipped)}")
    print(f"\nwritten to {path}")
    print("Review it, then: uv run --script zotero_edit.py --edits "
          f"{path} --dry-run")
    for line in skipped:
        print(f"  ! {line}")
    if differs:
        print(f"\nThese {len(differs)} hold a DIFFERENT ISSN on each side, and "
              "most are not a defect: a journal has a print and an electronic "
              "ISSN, both correct, and the two sources kept different members "
              "of the pair. `Brooks2009Charmm` has 1096-987X against 0192-8651 "
              "and Crossref calls both of them the Journal of Computational "
              "Chemistry. Resolving an ISSN through Crossref's /journals is how "
              "to tell those from a real mismatch -- `Bajaj1987Tertiary` is one, "
              "BBA general against BBA Protein Structure. Nothing is sent.")
        for line in differs[:40]:
            print(f"  - {line}")
        if len(differs) > 40:
            print(f"  ... and {len(differs) - 40} more NOT SHOWN")
    if junk:
        print(f"\nThese {len(junk)} hold a digit string with no ISSN structure "
              "at all in the frozen bib's `isbn` field. They harm nothing: each "
              "record already carries a correct ISSN, and that bib field no "
              "longer feeds any output. Recorded because it says something "
              "about how the bib was built, not because anyone should act.")
        for line in junk[:40]:
            print(f"  - {line}")
        if len(junk) > 40:
            print(f"  ... and {len(junk) - 40} more NOT SHOWN")
    if bad_url:
        print(f"\nThese {len(bad_url)} urls are refused: a url must be one "
              "http(s) URL with no embedded newline. The frozen bib has records "
              "holding several joined by a literal backslash-n, including local "
              "filesystem paths from someone else's machine. Which part is "
              "canonical is a judgement, so they are named rather than split.")
        for line in bad_url:
            print(f"  - {line}")
    if no_field:
        print("\nThe item type has nowhere to put these. That is TYPE_MAP damage "
              "frozen into Zotero's item types, not a rescue gap: a journal "
              "article typed as a report cannot hold its own volume. Fixing it "
              "needs the item type changed, which zotero_edit.py refuses by "
              "design because the PATCH drops every field the new type lacks.")
        for line in no_field:
            print(f"  - {line}")
    return 0


def order_by_pairings(citekey: str, files: list, pairings: dict) -> tuple:
    """Order one record's attachments to match a hand-decided `pairings.tsv`.

    Returns `(ordered, problems)`.

    **Two things assign a stem and only one of them was reading the decision.**
    `zotero_attach.py` consulted `.mirror/pairings.tsv` when it chose which
    attachment to fill; the refresh numbered attachments by the order Zotero's
    children endpoint happened to return, and never looked at the file at all.
    On 2026-10-03 they disagreed on two records, and because the refresh writes
    the extracts, it won: `text/Hirota2000Effect.md` came to hold the sibling
    paper under the key anyone cites. Silently, and only visible by reading
    page 1.

    A stem the file names is placed at its own index — `<key>` at 0, `<key>-N`
    at N-1 — and everything else keeps its existing relative order in the gaps.
    So a record the file does not mention is untouched, which is the case that
    must not break: every ordinary article-plus-supplement pair.

    A pairing naming an attachment this record does not have is a **problem**,
    not a silent skip. It means the file is stale against the library, and the
    whole point of it is to be the authority.
    """
    relevant = {stem: att for stem, att in pairings.items()
                if stem == citekey or (stem.startswith(citekey + "-")
                                       and stem[len(citekey) + 1:].isdigit())}
    if not relevant:
        return files, []

    by_id = {f.get("id"): f for f in files}
    slots, problems, claimed = {}, [], set()
    for stem, att in sorted(relevant.items()):
        idx = 0 if stem == citekey else int(stem[len(citekey) + 1:]) - 1
        if att not in by_id:
            problems.append(f"{stem} -> {att}: not an attachment on this record")
            continue
        if idx in slots:
            problems.append(f"{stem}: two pairings claim position {idx + 1}")
            continue
        if idx >= len(files):
            problems.append(f"{stem}: position {idx + 1} but the record has "
                            f"{len(files)} attachment(s)")
            continue
        slots[idx] = by_id[att]
        claimed.add(att)

    rest = [f for f in files if f.get("id") not in claimed]
    ordered, it = [], iter(rest)
    for i in range(len(files)):
        ordered.append(slots[i] if i in slots else next(it))
    return ordered, problems


def zotero_fetch(z: Zotero):
    """Bytes for one attachment, from Zotero, as a fallback.

    `harvest_attachments` tries `find_archived` first, so on this library every
    attachment comes off local disk and this is never called. It exists because
    a miss should be a fetch rather than a failure -- and because Zotero turned
    out to hold bytes for a large minority of attachments even before the
    upload, which is the claim this repo got wrong by reading `md5: None` as
    "no file".
    """
    def fetch(item_key: str) -> bytes:
        resp = z.session.get(f"{z.base}/items/{item_key}/file", timeout=180)
        resp.raise_for_status()
        return resp.content
    return fetch


def refresh(out: Path, ocr: bool = False, dry_run: bool = False,
            reassess: bool = False) -> int:
    """Rebuild the mirror from Zotero.

    The generators are `mendeley_mirror`'s and are untouched; what changed is
    where documents come from and where bytes come from. Three things this does
    NOT do, each said out loud rather than left to be discovered:

    - **It does not delete anything.** Records removed from Zotero leave their
      `text/` and `pdf/` files behind as orphans; `get_pdf.py --attachments`
      finds those.
    - **It writes `annotations/` only for records that have some**, so a record
      whose annotations were removed keeps a stale file. Deleting is a separate
      decision from refreshing and this does not make it.

    It supersedes `.mirror/retired.json` rather than being blocked by it: that
    marker says the mirror is frozen *as a Mendeley mirror*, and a Zotero
    refresh is precisely the thing that stops being true.
    """
    # Checked here, once, because the alternative is what happened on
    # 2026-10-02: extract_pdf_text imports pymupdf lazily, this script's header
    # did not declare it, and the ModuleNotFoundError was caught by the
    # per-attachment `except Exception` and counted as a failure. 2,734 times,
    # with the reason discarded each time. A missing dependency is a property of
    # the run, not of an attachment, and it should be said once and stop.
    try:
        import pymupdf  # noqa: F401
    except ImportError:
        sys.exit("This needs pymupdf and it is not installed. Run it through "
                 "uv, which reads the header: uv run --script zotero_source.py")
    if ocr:
        require_ocr_stack()

    api_key, user_id = load_zotero_credentials()
    z = Zotero(api_key, user_id)
    src = ZoteroSource(z)
    mirror = mirror_state_dir(out)
    started = datetime.now(timezone.utc)

    print(f"offprint {__version__} zotero refresh"
          f"{' -- DRY RUN, nothing is written' if dry_run else ''}")
    docs = src.documents()
    print(f"  documents: {len(docs)}")

    keymap_path = mirror / "citekeys.json"
    before = set(load_json(keymap_path, {}))
    keymap = assign_citekeys(docs, keymap_path, backend=BACKEND)
    minted = len(set(keymap) - before)
    # A first Zotero refresh should mint ZERO: all 2,739 zotero: entries were
    # written by zotero_migrate.py in September precisely so no key would move.
    # A non-zero count here is the failure CLAUDE.md warns about, arriving.
    print(f"  citation keys: {len(keymap)} known, {minted} newly assigned")
    if minted:
        print(f"  ! {minted} keys were MINTED. On this library that should be 0 -- "
              "every Zotero id already had one. Check the backend namespacing "
              "before trusting anything below.")

    files = src.files_by_doc([d["id"] for d in docs])
    print(f"  attachments: {sum(len(v) for v in files.values())}")

    # The hand-decided order wins over whatever Zotero returned.
    pairings = load_pairings(out)
    reordered, pair_problems = 0, []
    if pairings:
        for doc_id, flist in files.items():
            ck = keymap.get(qualify(doc_id, BACKEND))
            if not ck:
                continue
            new_order, probs = order_by_pairings(ck, flist, pairings)
            pair_problems += [f"{ck}: {p}" for p in probs]
            if [f.get("id") for f in new_order] != [f.get("id") for f in flist]:
                files[doc_id] = new_order
                reordered += 1
        print(f"  pairings.tsv: {len(pairings)} entries, {reordered} record(s) reordered")
        for line in pair_problems:
            print(f"  ! {line}")
    if dry_run:
        print("\nDry run. Nothing written: no keys saved, no bib, no extracts.")
        return 0

    save_json(keymap_path, keymap)
    write_bibtex(docs, keymap, out, include_abstract=True, backend=BACKEND)
    docs_by_id = {d["id"]: d for d in docs}

    # EXTRACTION FIRST. Annotations and collections used to run before it, so
    # when one attachment's children endpoint answered 400 the exception left
    # 2,732 extractions undone and no status written at all. Order the expensive
    # irreplaceable work ahead of the cheap optional work, so that a failure in
    # the second costs only the second.
    try:
        state = load_json(mirror / "state.json", {})
        fetched, skipped, failed, report = harvest_attachments(
            None, files, keymap, docs_by_id, out, state, "text",
            state_path=mirror / "state.json", ocr=ocr, reassess=reassess,
            fetch=zotero_fetch(z), backend=BACKEND)
        save_json(mirror / "state.json", state)
        write_extraction_report(report, out, fetched)
        print(f"  text: {fetched} extracted, {skipped} unchanged, {failed} failed")
    except Exception as exc:
        # A crash must not leave the previous run's status standing. Without
        # this, mirror-status.md keeps whatever it last said -- which on
        # 2026-10-02 would have been the word of a run that never finished.
        write_status(out, False, started, f"{type(exc).__name__}: {exc}", "unknown")
        print(f"\n! the refresh raised: {type(exc).__name__}: {exc}")
        print("  mirror-status.md records the failure.")
        raise

    ann_by_doc = src.annotations_by_doc(files)
    n_ann = sum(len(v) for v in ann_by_doc.values())
    print(f"  annotations: {n_ann} across {len(ann_by_doc)} record(s)")
    if ann_by_doc:
        ann_dir = out / "annotations"
        ann_dir.mkdir(parents=True, exist_ok=True)
        for doc_id, anns in ann_by_doc.items():
            key = keymap[qualify(doc_id, BACKEND)]
            (ann_dir / f"{key}.md").write_text(
                annotation_markdown(docs_by_id[doc_id], key, anns), encoding="utf-8")

    folders, folder_docs = src.collections()
    print(f"  collections: {len(folders)}")
    if folders:
        write_folders(folders, folder_docs, keymap, out, backend=BACKEND)
    write_index(docs, keymap, files, ann_by_doc, out, backend=BACKEND)

    # `write_status(out, True, ...)` was hardcoded here until 2026-10-02, so a
    # run in which all 2,734 extractions failed wrote **ok** and "Everything in
    # this folder is current". That is the defect this repo has recorded four
    # times and I built a fresh one in the newest code. The status reflects the
    # run.
    ok = failed == 0
    retired = mirror / "retired.json"
    if ok and retired.exists():
        retired.unlink()
        print("  cleared .mirror/retired.json -- this mirror is live again, "
              "backed by Zotero")
    elif retired.exists():
        print("  .mirror/retired.json LEFT IN PLACE: a run that failed does not "
              "get to declare the mirror live again")
    if ok:
        write_status(out, True, started)
    else:
        write_status(out, False, started,
                     f"{failed} of {fetched + failed} attachments failed to "
                     f"extract; see extraction-report.md and the log above",
                     "unknown")
        print(f"\n! {failed} attachments FAILED. mirror-status.md says so.")
    print(f"\nDone. {len(docs)} references in {out / 'library.bib'}")
    return 0 if ok else 1


def compare(out: Path) -> int:
    """Regenerate every entry from Zotero and diff it against the frozen bib.

    The frozen `library.bib` was produced by an independent path over the same
    library, and `citekeys.json` pairs the two backends' ids, so this is real
    corroboration rather than a file agreeing with itself.
    """
    bib = out / "library.bib"
    if not bib.is_file():
        sys.exit(f"no library.bib at {bib} to compare against")
    keymap = load_json(mirror_state_dir(out) / "citekeys.json", {})
    by_item = {k.split(":", 1)[1]: v for k, v in keymap.items() if k.startswith("zotero:")}
    if not by_item:
        sys.exit("citekeys.json holds no zotero: entries")

    old = {key: body for key, _t, body in
           [(k, t, f) for k, t, f in parse_bib(bib.read_text(encoding="utf-8"))]}
    api_key, user_id = load_zotero_credentials()
    src = ZoteroSource(Zotero(api_key, user_id))
    docs = src.documents()

    same = differs = unmapped = 0
    diffs: list = []
    for doc in docs:
        citekey = by_item.get(doc["id"])
        if not citekey:
            unmapped += 1
            continue
        if citekey not in old:
            diffs.append(f"{citekey}: in Zotero, not in the frozen library.bib")
            differs += 1
            continue
        fresh = {}
        for line in bib_entry(doc, citekey).splitlines():
            m = re.match(r"\s{2}(\w+)\s*=\s*\{(.*)\},?$", line)
            if m:
                fresh[m.group(1)] = m.group(2)
        # Counted from THIS record's own differences, not by scanning a window
        # of the accumulated list. A record is same or differs; nothing about
        # that is a search problem, and making it one is how a count starts
        # reporting something adjacent to what its label says.
        # NFC BOTH sides first. The frozen bib is decomposed and Zotero is
        # precomposed, so 17 of 31 author differences were one combining
        # diaeresis against one precomposed u-umlaut -- noise that hid the
        # three patents with no authors at all, which is the one real loss in
        # the set. A diff that reports a difference nobody can act on trains a
        # reader to skim it.
        def nfc(v):
            return unicodedata.normalize("NFC", v) if isinstance(v, str) else v
        mine = [f"{citekey}  {field}\n    frozen {old[citekey].get(field)!r}"
                f"\n    zotero {fresh.get(field)!r}"
                for field in sorted(set(old[citekey]) | set(fresh))
                if nfc(old[citekey].get(field)) != nfc(fresh.get(field))]
        diffs.extend(mine)
        differs += bool(mine)
        same += not mine

    print(f"# zotero_source --compare — offprint {__version__}\n")
    print(f"- documents read from Zotero: {len(docs)}")
    print(f"- entries identical to the frozen library.bib: {same}")
    print(f"- entries that differ: {differs}")
    print(f"- Zotero items with no citation key in citekeys.json: {unmapped}")
    print(f"- citation keys in the frozen bib not seen from Zotero: "
          f"{len(set(old) - {by_item.get(d['id']) for d in docs})}\n")
    for d in diffs:
        print(f"- {d}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Read a Zotero library as mirror documents.")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="mirror directory")
    ap.add_argument("--refresh", action="store_true",
                    help="rebuild library.bib, index.md and text/ from Zotero")
    ap.add_argument("--ocr", action="store_true",
                    help="with --refresh: OCR attachments with no text layer")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --refresh: read and report, write nothing")
    ap.add_argument("--reassess", action="store_true",
                    help="with --refresh: re-read attachments whose extract was "
                         "produced under older extraction rules")
    ap.add_argument("--rescue-identifiers", action="store_true",
                    help="write a zotero_edit.py edits file restoring identifiers "
                         "the frozen library.bib has and Zotero does not")
    ap.add_argument("--compare", action="store_true",
                    help="regenerate every BibTeX entry from Zotero and diff it "
                         "against the frozen library.bib")
    ap.add_argument("--version", action="version", version=f"offprint {__version__}")
    args = ap.parse_args()
    if args.refresh:
        return refresh(args.out, ocr=args.ocr, dry_run=args.dry_run,
                       reassess=args.reassess)
    if args.rescue_identifiers:
        return rescue_identifiers(args.out)
    if args.compare:
        return compare(args.out)
    ap.error("pick a mode: --refresh, --compare or --rescue-identifiers.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
