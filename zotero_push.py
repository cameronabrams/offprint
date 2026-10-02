#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]
# ///
"""
zotero_push.py -- add one reference to Zotero, from a DOI or an arXiv id.

The counterpart of `mendeley_push.py`, and the last of the four gaps the library
session found after the cutover. For a paper with no PDF behind it yet: the
record goes in, and `zotero_inbox.py` attaches the file later if one arrives.

    uv run --script zotero_push.py --doi 10.1088/2632-2153/ae4b07
    uv run --script zotero_push.py --arxiv 2507.07887 --yes

Resolution is `mendeley_push.py`'s and is reused unchanged -- `from_doi` goes
through doi.org content negotiation, so it answers for DataCite DOIs that
`api.crossref.org` returns 404 for, and `from_arxiv` handles the preprint case.
Those were never Mendeley-specific; only the create half was.

**`csl_year()` decides the year, and that is not a detail.** CSL `issued` is the
earliest date a work appeared, which for an Advance Access paper is the online
date, and it is what every exporter takes. CHARMM36m went online in November
2016, appeared in the January 2017 issue, and was filed and cited as 2016 before
anyone noticed. The issue year lives in `published-print`. Any future path that
creates a record calls `csl_year()` rather than reading a date-part itself.

**It will not create a record the library already has.** The DOI is checked
against `library.bib` first, then the normalized title, through the same
`existing_document` the inbox uses. A duplicate record is not a tidy-up later --
it is two citation keys for one paper, and `assign_citekeys` will mint the
second one happily.

**It does not assign a citation key.** The key comes from the next refresh,
where `assign_citekeys` mints it once and keeps it forever. A key invented here
would be a second authority for the one thing in this system that must have
exactly one.

**Fields are checked against Zotero's own template for the item type**, fetched
from `/items/new`. Zotero ignores an unknown field silently rather than
refusing it, so a value sent to a field the type does not have simply vanishes;
anything with nowhere to go is reported instead, which is how you find out that
a `report` has no `volume` rather than wondering where the volume went.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mendeley_mirror import DEFAULT_OUT, __version__  # noqa: E402
from mendeley_push import from_arxiv, from_doi  # noqa: E402
from inbox import existing_document  # noqa: E402
from zotero_migrate import Zotero, load_zotero_credentials  # noqa: E402

ZOTERO_API = "https://api.zotero.org"

# The inverse of `zotero_source.ITEM_TYPES`. Several Zotero types collapse into
# one Mendeley type on the way in, so coming back out needs a canonical choice
# per type rather than a reversal of the dict.
TO_ZOTERO_TYPE = {
    "journal": "journalArticle",
    "magazine_article": "magazineArticle",
    "newspaper_article": "newspaperArticle",
    "book": "book",
    "book_section": "bookSection",
    "encyclopedia_article": "encyclopediaArticle",
    "conference_proceedings": "conferencePaper",
    "thesis": "thesis",
    "report": "report",
    "working_paper": "preprint",
    "web_page": "webpage",
    "computer_program": "computerProgram",
    "patent": "patent",
    "generic": "document",
}

# Where a venue goes depends on the type, the same way it does on the way in.
VENUE_FIELD = {
    "journalArticle": "publicationTitle",
    "magazineArticle": "publicationTitle",
    "newspaperArticle": "publicationTitle",
    "bookSection": "bookTitle",
    "encyclopediaArticle": "encyclopediaTitle",
    "conferencePaper": "proceedingsTitle",
    "preprint": "repository",
    "webpage": "websiteTitle",
}


def die(msg: str):
    sys.exit(f"error: {msg}")


def item_template(item_type: str) -> dict:
    """Zotero's own empty item for a type: the authoritative field list.

    Asked rather than assumed, because Zotero **ignores an unknown field
    silently** instead of refusing it. A value sent to a field the type does
    not have does not fail; it vanishes, and nothing says so. This is the same
    hazard `zotero_edit.py` guards by requiring a field to exist on the item it
    is editing -- here there is no item yet, so the template is the equivalent.
    """
    resp = requests.get(f"{ZOTERO_API}/items/new", params={"itemType": item_type},
                        timeout=60,
                        headers={"Zotero-API-Version": "3",
                                 "User-Agent": f"offprint/{__version__}"})
    if resp.status_code == 400:
        die(f"Zotero does not know an item type called {item_type!r}")
    resp.raise_for_status()
    return resp.json()


def doc_to_zotero(doc: dict, template: dict) -> tuple[dict, list]:
    """A Mendeley-shaped document as a Zotero item. Returns `(item, dropped)`.

    `dropped` names every value the item type has nowhere to put. It is
    returned rather than discarded because that is exactly what Zotero would do
    with it, and silently -- the point of consulting the template is to turn a
    disappearance into a line of output.
    """
    item_type = template.get("itemType", "document")
    item = {"itemType": item_type}
    dropped: list = []

    def put(field: str, value, label: str = ""):
        if value in ("", None, []):
            return
        if field in template:
            item[field] = value
        else:
            dropped.append(f"{label or field}={value!r} ({item_type} has no "
                           f"{field})")

    put("title", (doc.get("title") or "").strip())
    creators = [{"creatorType": "author",
                 "firstName": (p.get("first_name") or "").strip(),
                 "lastName": (p.get("last_name") or "").strip()}
                for p in doc.get("authors") or []
                if (p.get("last_name") or p.get("first_name"))]
    if creators:
        item["creators"] = creators
    if doc.get("year"):
        put("date", str(doc["year"]))
    venue = (doc.get("source") or "").strip()
    if venue:
        put(VENUE_FIELD.get(item_type, "publicationTitle"), venue, "venue")
    for src, dst in (("publisher", "publisher"), ("city", "place"),
                     ("volume", "volume"), ("issue", "issue"),
                     ("pages", "pages"), ("abstract", "abstractNote")):
        put(dst, (doc.get(src) or "").strip() if isinstance(doc.get(src), str)
            else doc.get(src), src)

    ids = doc.get("identifiers") or {}
    put("DOI", ids.get("doi"))
    put("ISBN", ids.get("isbn"))
    put("ISSN", ids.get("issn"))
    if ids.get("arxiv"):
        # arXiv has no field of its own; Zotero's own translators put it in
        # `extra`, which is where zotero_source reads it back from.
        extra = (item.get("extra") or "").strip()
        item["extra"] = (extra + "\n" if extra else "") + f"arXiv: {ids['arxiv']}"
    return item, dropped


def create_item(z: Zotero, item: dict) -> str:
    """POST one top-level item and return its key."""
    resp = z.session.post(
        f"{z.base}/items", json=[item],
        headers={"Zotero-Write-Token": secrets.token_hex(16),
                 "Content-Type": "application/json"}, timeout=60)
    if resp.status_code == 403:
        die("Zotero refused the key (403). Creating a record needs WRITE access.")
    resp.raise_for_status()
    payload = resp.json()
    failed = payload.get("failed") or {}
    if failed:
        die(f"Zotero refused the record: {failed}")
    saved = (payload.get("successful") or {}).get("0") or {}
    key = saved.get("key") or (saved.get("data") or {}).get("key")
    if not key:
        die(f"Zotero accepted the record but returned no key: {payload}")
    return key


def main() -> int:
    ap = argparse.ArgumentParser(description="Add one reference to Zotero.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--doi", help="DOI, e.g. 10.1088/2632-2153/ae4b07")
    src.add_argument("--arxiv", help="arXiv identifier, e.g. 2507.07887")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="mirror directory")
    ap.add_argument("--yes", action="store_true",
                    help="actually create it; without this it is a dry run")
    ap.add_argument("--version", action="version", version=f"offprint {__version__}")
    args = ap.parse_args()

    doc = from_arxiv(args.arxiv) if args.arxiv else from_doi(args.doi)
    doi = (doc.get("identifiers") or {}).get("doi", "")
    title = doc.get("title", "")

    print(f"offprint {__version__} zotero_push: "
          f"{'creating' if args.yes else 'DRY RUN -- nothing is sent'}\n")
    print(f"  {title[:78]}")
    print(f"  {', '.join(p.get('last_name', '') for p in doc.get('authors') or [])[:78]}")
    print(f"  {doc.get('source', '')} {doc.get('volume', '')} "
          f"({doc.get('year', '')}) {doc.get('pages', '')}".rstrip())
    if doi:
        print(f"  doi {doi}")

    _mid, citekey = existing_document(args.out, doi, title)
    if citekey:
        print(f"\n  ! the library already has this as {citekey}. Not creating a "
              "second record -- that is two citation keys for one paper, and "
              "assign_citekeys will mint the second one happily.")
        return 1

    item_type = TO_ZOTERO_TYPE.get(doc.get("type", "generic"), "document")
    template = item_template(item_type)
    item, dropped = doc_to_zotero(doc, template)
    print(f"\n  itemType: {item_type}")
    for k, v in sorted(item.items()):
        if k != "itemType":
            print(f"    {k}: {str(v)[:70]}")
    for line in dropped:
        print(f"  ! nowhere to put {line}")

    if not args.yes:
        print("\nDry run. Nothing was sent.")
        return 0

    # Credentials are only needed now, which is why a dry run works without a
    # key at all -- useful for checking what a DOI resolves to before deciding
    # whether the paper belongs in the library.
    api_key, user_id = load_zotero_credentials()
    key = create_item(Zotero(api_key, user_id), item)
    print(f"\ncreated {key}")
    print("No citation key yet: the next refresh assigns one and keeps it. "
          "Attach a PDF with zotero_inbox.py once you have one.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except requests.HTTPError as exc:
        die(str(exc))
