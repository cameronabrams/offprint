#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]
# ///
"""Read a Zotero library in the shape the mirror's generators already consume.

The first piece of the Zotero-backed refresh (ROADMAP item 1). It translates
Zotero items into the document dicts that `bib_entry`, `assign_citekeys`,
`write_index` and `annotation_markdown` already take, so the generators do not
change at all and the thing that changes is where documents come from.

**It can be verified before it is integrated, which is why it was built first.**
`~/Sync/mendeley/library.bib` is a frozen, known-good rendering of the same
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
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mendeley_mirror import DEFAULT_OUT, __version__, bib_entry, load_json, mirror_state_dir  # noqa: E402
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
    authors = [person(c) for c in creators if (c.get("creatorType") or "author") == "author"]
    editors = [person(c) for c in creators if c.get("creatorType") == "editor"]

    venue = ""
    for field in VENUE_FIELDS:
        if (data.get(field) or "").strip():
            venue = data[field].strip()
            break

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
        for field in sorted(set(old[citekey]) | set(fresh)):
            a, b = old[citekey].get(field), fresh.get(field)
            if a != b:
                diffs.append(f"{citekey}  {field}\n    frozen {a!r}\n    zotero {b!r}")
        if any(d.startswith(citekey + "  ") for d in diffs[-40:]):
            differs += 1
        else:
            same += 1

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
    ap.add_argument("--compare", action="store_true",
                    help="regenerate every BibTeX entry from Zotero and diff it "
                         "against the frozen library.bib")
    ap.add_argument("--version", action="version", version=f"offprint {__version__}")
    args = ap.parse_args()
    if args.compare:
        return compare(args.out)
    ap.error("nothing to do yet: --compare is the only mode. The refresh itself "
             "is ROADMAP item 1 and is not wired up.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
