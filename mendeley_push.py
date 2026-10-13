#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]
# ///
"""
mendeley_push.py -- add one reference TO Mendeley, from an arXiv ID or a DOI.

The mirror is one-way: Mendeley is the source of truth and everything in this
folder is generated from it. This script is the one deliberate exception. It
does not touch the mirror at all -- it POSTs a new document to Mendeley itself,
and the next refresh brings it back down like any other reference.

    uv run --script mendeley_push.py --arxiv 2507.07887
    uv run --script mendeley_push.py --doi 10.1088/2632-2153/ae4b07
    uv run --script mendeley_push.py --arxiv 2507.07887 --dry-run   # look first

Metadata comes from arXiv's Atom API or, for a DOI, from doi.org content
negotiation -- which routes to whichever agency registered the DOI, so OSTI
reports and Zenodo deposits resolve as readily as Crossref journal articles.
You get real authors and a real year rather than a stub to fix up by hand.

The document type follows the metadata: a book is filed as a book, a thesis as
a thesis, a report as a report. Only arXiv preprints are deliberately filed as
"journal", because Mendeley has no preprint type and journal + source is the
convention.

Credentials are the mirror's: it reuses config.json and tokens.json from the
same per-machine store (~/.config/mendeley-mirror, or %LOCALAPPDATA% on
Windows), and the app registration already asks for scope "all", so no
re-authorization is needed on a machine where the mirror already runs. On a
machine where it does not, run the mirror once first to log in.

Nothing is sent until you confirm, unless you pass --yes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mendeley_mirror import (  # noqa: E402
    API,
    Mendeley,
    config_dir,
    get_app_config,
    load_json,
)

DOC_CT = "application/vnd.mendeley-document.1+json"


def csl_year(meta: dict) -> int | None:
    """The year a citation should carry: the ISSUE year, not the online one.

    CSL "issued" is the earliest date a work appeared, so for an Advance
    Access paper it is the online date -- and that is what every exporter
    takes. The issue year lives in "published-print", or on some records only
    in "journal-issue.published-print".

    Preferring those is not pedantry. CHARMM36m went online in November 2016
    and appeared in the January 2017 issue; taking "issued" filed it as 2016,
    and it was cited that way in a manuscript before anyone noticed. CGenFF,
    APBS and the AlphaFold database were wrong the same way.

    Falls back to "issued" when there is no print date at all, which is the
    right answer for a preprint, a data set, or a born-digital journal.
    """
    for src in (meta.get("published-print"),
                (meta.get("journal-issue") or {}).get("published-print"),
                meta.get("issued")):
        parts = ((src or {}).get("date-parts") or [[None]])[0]
        if parts and parts[0]:
            return parts[0]
    return None


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def split_name(full: str) -> dict:
    """Mendeley wants first_name / last_name separately."""
    parts = full.replace("\n", " ").split()
    if not parts:
        return {"first_name": "", "last_name": ""}
    if len(parts) == 1:
        return {"first_name": "", "last_name": parts[0]}
    return {"first_name": " ".join(parts[:-1]), "last_name": parts[-1]}


def from_arxiv(arxiv_id: str) -> dict:
    arxiv_id = re.sub(r"^arxiv[:/]", "", arxiv_id.strip(), flags=re.I)
    bare = re.sub(r"v\d+$", "", arxiv_id)
    r = requests.get(
        "https://export.arxiv.org/api/query",
        params={"id_list": bare, "max_results": 1},
        timeout=60,
    )
    r.raise_for_status()
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entry = ET.fromstring(r.text).find("a:entry", ns)
    if entry is None or entry.find("a:title", ns) is None:
        die(f"arXiv returned no entry for {bare}")
    title = " ".join(entry.find("a:title", ns).text.split())
    published = (entry.find("a:published", ns).text or "")[:4]
    authors = [
        split_name(a.find("a:name", ns).text)
        for a in entry.findall("a:author", ns)
        if a.find("a:name", ns) is not None
    ]
    doi_el = entry.find("a:doi", ns)
    ids = {"arxiv": bare}
    if doi_el is not None and doi_el.text:
        ids["doi"] = doi_el.text
    return {
        "type": "journal",          # Mendeley has no "preprint"; journal + source is the convention
        "title": title,
        "authors": authors,
        "year": int(published) if published.isdigit() else None,
        "source": "arXiv",
        "identifiers": ids,
        "websites": [f"https://arxiv.org/abs/{bare}"],
    }


def one(v) -> str:
    """CSL-JSON gives a string where the Crossref API gives a list.

    Crossref also ships markup inside titles -- <i>g</i> in CHAPERONg,
    <i>n</i>-propyl, <sub>, <scp> -- which would otherwise land in the .bib
    verbatim and print as literal tags. Strip the tags, keep the text.
    """
    if isinstance(v, (list, tuple)):
        v = v[0] if v else ""
    text = re.sub(r"<[^>]+>", "", str(v or ""))
    return " ".join(text.split())


# CSL-JSON type -> Mendeley document type. Anything unlisted becomes "generic",
# which is Mendeley's own catch-all and is better than mislabeling.
# Crossref emits "journal-article" where the CSL spec says "article-journal";
# both appear in the wild, so both are mapped.
CSL_TO_MENDELEY = {
    "journal-article": "journal", "article-journal": "journal",
    "article": "journal", "review": "journal", "preprint": "journal",
    "book": "book", "edited-book": "book", "monograph": "book",
    "chapter": "book_section", "book-chapter": "book_section",
    "book-part": "book_section", "book-section": "book_section",
    "paper-conference": "conference_proceedings",
    "proceedings": "conference_proceedings",
    "proceedings-article": "conference_proceedings",
    "thesis": "thesis", "dissertation": "thesis",
    "report": "report", "posted-content": "journal",
    "webpage": "web_page", "patent": "patent",
    "magazine-article": "magazine_article", "newspaper-article": "newspaper_article",
}


def from_doi(doi: str) -> dict:
    """Metadata for any DOI, whoever registered it.

    doi.org content negotiation routes to the owning registration agency, so
    this answers for DataCite DOIs -- OSTI reports, Zenodo deposits, many
    theses -- that api.crossref.org returns 404 for.
    """
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/)", "", doi.strip(), flags=re.I)
    r = requests.get(
        f"https://doi.org/{doi}", allow_redirects=True, timeout=60,
        headers={"Accept": "application/vnd.citationstyles.csl+json",
                 "User-Agent": "mendeley_push (mailto:cfa22@drexel.edu)"},
    )
    if r.status_code == 404:
        die(f"no registration agency has a record for {doi}")
    r.raise_for_status()
    if "json" not in (r.headers.get("Content-Type") or ""):
        die(f"{doi} did not resolve to metadata -- got {r.headers.get('Content-Type')}")
    m = r.json()

    authors = [
        {"first_name": a.get("given", ""), "last_name": a.get("family", "")}
        for a in m.get("author", []) if a.get("family")
    ]
    doc = {
        "type": CSL_TO_MENDELEY.get((m.get("type") or "").lower(), "generic"),
        "title": one(m.get("title")),
        "authors": authors,
        "year": csl_year(m),
        "source": one(m.get("container-title")),
        "identifiers": {"doi": doi.lower()},
    }
    if doi.lower().startswith("10.48550/arxiv"):
        # An arXiv DOI resolves with no container title. Follow the same
        # convention as the --arxiv path so the entry is not journal-less.
        doc.setdefault("source", "")
        doc["source"] = doc["source"] or "arXiv"
        doc["identifiers"]["arxiv"] = doi.split("arxiv.", 1)[-1]
    if doc["type"] in ("book", "book_section", "thesis", "report"):
        doc["publisher"] = one(m.get("publisher"))
        isbn = one(m.get("ISBN"))
        if isbn:
            doc["identifiers"]["isbn"] = isbn
    for k, v in (("volume", m.get("volume")), ("issue", m.get("issue")),
                 ("pages", m.get("page"))):
        if v:
            doc[k] = one(v)
    return doc


def main() -> None:
    ap = argparse.ArgumentParser(description="Add one reference to Mendeley.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--arxiv", help="arXiv identifier, e.g. 2507.07887")
    src.add_argument("--doi", help="DOI, e.g. 10.1088/2632-2153/ae4b07")
    ap.add_argument("--dry-run", action="store_true", help="show the payload, send nothing")
    ap.add_argument("--yes", action="store_true", help="do not ask before sending")
    args = ap.parse_args()

    doc = from_arxiv(args.arxiv) if args.arxiv else from_doi(args.doi)
    doc = {k: v for k, v in doc.items() if v not in (None, "", [], {})}

    print(json.dumps(doc, indent=2, ensure_ascii=False))
    if args.dry_run:
        print("\n--dry-run: nothing sent.")
        return

    tokens = load_json(config_dir() / "tokens.json", {})
    if not tokens.get("access_token"):
        die(
            "no Mendeley tokens on this machine.\n"
            "       This script reuses the mirror's credentials; run the mirror once\n"
            "       here first to log in:  ./run_mirror.sh"
        )
    cfg = get_app_config()

    if not args.yes:
        reply = input("\nAdd this to Mendeley? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("aborted.")
            return

    client = Mendeley(cfg, tokens)
    resp = client.session.post(
        f"{API}/documents",
        headers={
            **client._auth_header(),
            "Content-Type": DOC_CT,
            "Accept": DOC_CT,
        },
        data=json.dumps(doc),
        timeout=60,
    )
    if resp.status_code not in (200, 201):
        die(f"Mendeley refused it ({resp.status_code}): {resp.text[:400]}")

    created = resp.json()
    print(f"\nadded: {created.get('title')}")
    print(f"  id: {created.get('id')}")
    print("\nIt will appear in this folder after the next mirror refresh.")


if __name__ == "__main__":
    main()
