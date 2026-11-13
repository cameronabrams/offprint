#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31", "pymupdf>=1.24"]
# ///
"""
identify.py -- work out which paper a PDF is, and whether the library has it.

Not a CLI. This is the identification half of filing a paper, factored out so
the scripts that file one -- `zotero_inbox.py` and `zotero_push.py` -- all ask
the question the same way. Its public names are `identify`, `existing_document`,
`has_content` and `title_of`.

Identification, in order, stopping at the first candidate that checks out:

    the DOI in the file name, if you renamed it that way
    the DOI in the PDF's own metadata
    a DOI printed on the first two pages
    a Crossref search on the text of the first page

**Every candidate is verified before it is used**: the title Crossref returns
has to actually appear on the PDF's first page. A PDF that cannot be identified
that way is refused and reported, because attaching a paper to the wrong
reference files one paper's PDF under another paper's citation key, silently.

Grey literature -- DTIC/NTIS reports, theses, conference proceedings -- often
has no DOI to identify it by. For those, drop a sidecar JSON next to the PDF
with the same stem (report.pdf + report.json) holding the record's fields:

    {"type": "report", "title": "...", "year": 1991,
     "source": "NRL Memorandum Report 6848 (DTIC ADA239276)",
     "authors": [{"first_name": "Arthur W.", "last_name": "Snow"}]}

A sidecar overrides identification entirely; nothing is looked up.

It reads the mirror and the public Crossref API. It writes nothing, and it
holds no credentials for anything.
"""

from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from pathlib import Path

import pymupdf
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from mirror import page_is_garbled, pdftotext_pages
    from fromdoi import CSL_TO_TYPE, csl_year, one
except ImportError as exc:
    sys.exit(f"identify.py must sit beside the other mirror scripts ({exc})")
# A DOI may contain brackets, angle brackets and semicolons, and old Elsevier and
# Wiley DOIs routinely do: 10.1016/s0032-3861(01)00634-6, and the SICI form
# 10.1002/(SICI)1097-4628(19990906)73:10<1927::AID-APP12>3.0.CO;2-O. Excluding
# those characters here truncated such a DOI mid-string -- 10.1016/s0032-3861(01)
# 00634-6 became "10.1016/s0032-3861(01", which then matched nothing and reported
# the paper as unidentifiable. So match everything up to whitespace or a quote,
# and let clean_doi decide which trailing characters are punctuation.
DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"']+", re.I)

CLOSERS = {")": "(", "]": "[", "}": "{", ">": "<"}
UA = "offprint identify.py (mailto:cfa22@drexel.edu)"

STOP = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on",
        "or", "the", "to", "with", "its", "their", "using", "via"}


def deaccent(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def words(s: str) -> set:
    return {w for w in re.sub(r"[^a-z0-9 ]+", " ", deaccent(s or "").lower()).split()
            if len(w) > 3 and w not in STOP}


def clean_doi(raw: str) -> str:
    """Trim what is punctuation around a DOI without eating part of the DOI.

    Publishers run a DOI straight into the next word on a wrapped line, and prose
    puts one in brackets -- "(see 10.1234/abc)" -- so a trailing bracket usually is
    not part of it. Usually. A DOI may genuinely end in a bracket, and old Elsevier
    and Wiley DOIs are full of them, so the test is whether the bracket BALANCES:
    a closer with no opener before it was the prose's, and one that has an opener
    is the DOI's own.
    """
    doi = raw.strip()
    while doi:
        if doi[-1] in ".,;:":
            doi = doi[:-1]
            continue
        if doi[-1] in CLOSERS and doi.count(doi[-1]) > doi.count(CLOSERS[doi[-1]]):
            doi = doi[:-1]
            continue
        break
    return doi.lower()


def filename_dois(name: str) -> list[str]:
    """DOIs written into a file name, where "/" cannot appear literally.

    Saving as 10.1021_acspolymersau.5c00022.pdf is the usual dodge, so treat
    the first separator after the registrant prefix as the slash.
    """
    name = re.sub(r"\.pdf$", "", name, flags=re.I)     # not part of the DOI
    out = [clean_doi(m) for m in DOI_RE.findall(name)]
    for m in re.findall(r"\b10\.\d{4,9}[_-][^\s/]+", name, re.I):
        head, sep, tail = m.partition("_") if "_" in m else m.partition("-")
        out.append(clean_doi(f"{head}/{tail}"))
    return out


# --------------------------------------------------------------------------
# reading the PDF
# --------------------------------------------------------------------------

def pdf_text(path: Path, pages: int = 2) -> str:
    """The first pages as text, repairing a garbled text layer the way a refresh does.

    Some PDFs carry a broken font encoding, so pymupdf returns control characters
    where the words should be. The refresh has re-read those with pdftotext
    since 2026-09-16, and identification did not -- so a paper whose text layer is
    garbled could not be identified from its own page, while the very same file,
    once filed, produced a perfectly readable extract. That asymmetry cost a
    correctly named paper a sidecar workaround and sent two investigations after
    the wrong cause, because the failure reads as "the title is not on page 1",
    which is true and not the point.
    """
    with pymupdf.open(path) as doc:
        out = [doc[i].get_text() for i in range(min(pages, doc.page_count))]
    if not any(page_is_garbled(t) for t in out):
        return " ".join(out)
    repaired = pdftotext_pages(path.read_bytes())
    if not repaired:
        return " ".join(out)     # no pdftotext on this machine: nothing better to offer
    for i, t in enumerate(out):
        if page_is_garbled(t) and i < len(repaired) and not page_is_garbled(repaired[i]):
            out[i] = repaired[i]
    return " ".join(out)


def has_content(path: Path, probe: int = 4) -> bool:
    """Does this PDF actually carry anything -- text, an image, or vector marks?

    A scan with no text layer is legitimate and must stay filable, so the file-name
    path deliberately trusts a DOI in the name over the absence of text. That trust
    has one hole: a delivery failure. An interlibrary-loan fetch can return a PDF
    that is empty or all-blank, and named for its DOI it would sail through as
    "unverified: no text" and become a reference with nothing behind it.

    A real scan has page images. An empty delivery has no pages, or pages with
    nothing drawn on them.
    """
    try:
        with pymupdf.open(path) as doc:
            if doc.page_count == 0:
                return False
            for i in range(min(probe, doc.page_count)):
                page = doc[i]
                if page.get_text().strip() or page.get_images() or page.get_drawings():
                    return True
        return False
    except Exception:
        return False        # unreadable is not "has content"


def pdf_metadata_dois(path: Path) -> list[str]:
    out = []
    with pymupdf.open(path) as doc:
        blob = " ".join(str(v) for v in (doc.metadata or {}).values() if v)
        try:
            blob += " " + (doc.get_xml_metadata() or "")
        except Exception:
            pass
    out += [clean_doi(m) for m in DOI_RE.findall(blob)]
    return out


def normalize(raw: dict) -> dict:
    """Both metadata shapes, reduced to the fields a mirror record needs."""
    return {
        # Share the pusher's type map so a book filed through the inbox is a
        # book, not a journal article.
        "type": CSL_TO_TYPE.get((raw.get("type") or "").lower(), "generic"),
        "publisher": one(raw.get("publisher")),
        "doi": (raw.get("DOI") or raw.get("doi") or "").lower(),
        "title": one(raw.get("title")),
        "source": one(raw.get("container-title")),
        # The issue year, not the online one -- see csl_year in fromdoi.
        "year": csl_year(raw),
        "volume": one(raw.get("volume")),
        "issue": one(raw.get("issue")),
        "pages": one(raw.get("page")),
        "authors": [
            {"first_name": a.get("given", ""), "last_name": a.get("family", "")}
            for a in (raw.get("author") or []) if a.get("family")
        ],
    }


def resolve_doi(doi: str) -> dict | None:
    """Metadata for any DOI, whoever registered it.

    doi.org routes to the owning agency, so this answers for OSTI reports and
    Zenodo deposits that api.crossref.org returns 404 for.
    """
    r = requests.get(f"https://doi.org/{doi}", allow_redirects=True, timeout=60,
                     headers={"Accept": "application/vnd.citationstyles.csl+json",
                              "User-Agent": UA})
    if r.status_code != 200 or "json" not in (r.headers.get("Content-Type") or ""):
        return None
    try:
        return normalize(r.json())
    except ValueError:
        return None


# Crossref indexes more than papers, and a bibliographic search ranks by text
# similarity alone. On 2026-10-16 `library` found a title search for
# `Braig1994Crystal` returning the PDB DATASET `10.2210/pdb1grl/pdb` at 0.94
# similarity -- a deposited structure carrying the paper's own title. Filing a
# PDF against that DOI would put a dataset's metadata on a journal article.
#
# A deny list rather than an allowlist of `journal-article`: proceedings,
# chapters, theses, reports and preprints are all papers somebody files, and
# excluding them to be rid of datasets would cost more than it saves.
NOT_A_PAPER = {
    "dataset", "component", "peer-review", "grant", "journal", "journal-issue",
    "journal-volume", "book-series", "book-set", "proceedings-series",
    "report-series", "standard-series", "database", "entry",
}


def crossref_search(text: str) -> dict | None:
    """Ask Crossref what paper this first page belongs to."""
    query = " ".join(text.split()[:60])
    r = requests.get("https://api.crossref.org/works",
                     params={"query.bibliographic": query, "rows": 5},
                     headers={"User-Agent": UA}, timeout=60)
    if r.status_code != 200:
        return None
    items = r.json()["message"].get("items") or []
    for item in items:
        if (item.get("type") or "").lower() in NOT_A_PAPER:
            continue
        return normalize(item)
    return None


def title_of(meta: dict) -> str:
    return meta.get("title", "")


# Elsevier's default download name: `1-s2.0-<PII>-main.pdf`. The PII is a
# publisher identifier, not a DOI, and the `1-s2.0-` prefix and `-main` suffix
# are the template rather than anything about the article.
ELSEVIER_DOWNLOAD = re.compile(r"1-s2\.0-S?[\dX-]{8,}(-main)?", re.I)


def stem_conflict(stem: str, doi: str) -> bool:
    """True when the file name looks like it names a DIFFERENT DOI than `doi`.

    Only fires when the stem is recognizably a DOI suffix -- "387527a0",
    "science.1116480", "16.18.5572" -- so ordinary names like "kwong2000" or
    "casino" never trigger it. Those stems are a deliberate statement about which
    paper this is, and when the PDF's own text disagrees, the text is the thing to
    doubt: a downloaded issue scan frequently begins on the tail of the preceding
    article.
    """
    # Look for the digit run in the RAW name. Stripping separators first glues
    # neighbours together -- "October 23  1995" becomes "231995" and invents a
    # six-digit run in a perfectly ordinary publisher filename.
    #
    # FIVE consecutive digits, not four: a four-digit run is a YEAR, and names like
    # "doyle1996" or "cowen-jacob2005" are how people actually save papers. Requiring
    # four fired on twenty files in a forty-four file batch and refused most of them.
    if not re.search(r"\d{5}", stem):
        return False                      # not DOI-suffix-shaped; no opinion

    # A PUBLISHER'S OWN DOWNLOAD NAME IS NOT A CLAIM ABOUT WHICH PAPER THIS IS.
    # This guard exists for a name a HUMAN chose, or that was derived from a
    # DOI: "the file says Smith2001, the text says something else, trust the
    # name." Elsevier's default is `1-s2.0-S2211124720314170-main.pdf`, built
    # from the PII -- a different identifier for the same article, sharing no
    # characters with `10.1016/j.celrep.2020.108428` and therefore unable to
    # match any DOI by containment, ever. Every Elsevier download would be
    # refused as its own name disagreeing with its own text.
    #
    # Reported by `library` 2026-10-05, hours after the browser-full-DOI case.
    # Two failures of one assumption: that a five-digit run means the stem is
    # a DOI suffix. It can equally be a PII, an issue number, or a timestamp.
    # The guard cannot be loosened in general -- an unrecognized stem must keep
    # defaulting to "conflict", because the case it was built for is a stem
    # that names the right paper while page 1 shows the previous article's
    # tail, and there is nothing in that stem to recognize either. So each
    # publisher template gets excluded by name as it turns up, which is slow
    # but cannot weaken the default.
    if ELSEVIER_DOWNLOAD.search(stem):
        return False
    st = re.sub(r"[^a-z0-9.]", "", stem.lower())
    if len(st) < 6:
        return False
    # Compared against the WHOLE DOI as well as its suffix. The docstring above
    # assumed a stem is suffix-shaped -- "387527a0", "science.1116480" -- which
    # is how a person names a file. A browser names it after the entire DOI,
    # prefix included, and then "10.1002sici..." could never be found inside a
    # tail that starts after the slash. Every such file read as a conflict
    # between its own name and its own text.
    #
    # Found 2026-10-05: library reported `10.1002_(SICI)1097-4628(19970404)64.pdf`
    # refused with "none matched the text on page 1", and diagnosed it as the
    # title matcher losing a hyphen at a line break. The title matched perfectly
    # (overlap 1.00, phrase found); this is what refused it. The browser had also
    # truncated the name at the ':', so the stem is a PREFIX of the real DOI --
    # which containment already handles once both sides are the same shape.
    norm = lambda v: re.sub(r"[^a-z0-9.]", "", v.lower())
    full = norm(doi)
    tail = norm(doi.lower().split("/", 1)[-1])
    return not any(st in hay or hay in st for hay in (full, tail))


def looks_right(msg: dict, page_text: str) -> bool:
    """Does the paper this metadata describes actually match this PDF?

    Word overlap alone is not enough, and the failure is not hypothetical. A
    Science reprint carries its issue's other titles in the related-content and
    reference matter, so "Constraining Exoplanet Mass from Transmission
    Spectroscopy" scored 83% against an HIV envelope paper -- every word present,
    scattered, the phrase nowhere. Four papers in one batch were about to be filed
    against unrelated references that way.

    So a title must also appear as a PHRASE: some run of four consecutive title
    words, in order, in the normalized text. A real title is printed on page one
    and survives that; a bag of coincidental words does not. Short titles have no
    four-word run and fall back to overlap alone.
    """
    title = title_of(msg)
    if not title:
        return False
    tw = words(title)
    if not tw:
        return False
    if len(tw & words(page_text)) / len(tw) < 0.6:
        return False
    seq = [w for w in re.findall(r"[a-z0-9]+", title.lower())]
    if len(seq) < 4:
        return True
    hay = " ".join(re.findall(r"[a-z0-9]+", page_text.lower()))
    return any(" ".join(seq[i:i + 4]) in hay for i in range(len(seq) - 3))


def read_sidecar(path: Path) -> dict | None:
    """Hand-written metadata for a PDF that no registry can identify."""
    side = path.with_suffix(".json")
    if not side.exists():
        return None
    try:
        meta = json.loads(side.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise RuntimeError(f"{side.name} is not valid JSON: {exc}") from None
    if not meta.get("title"):
        raise RuntimeError(f"{side.name} has no title")
    meta.setdefault("type", "report")
    meta.setdefault("authors", [])
    meta.setdefault("doi", "")
    for k in ("source", "volume", "issue", "pages"):
        meta.setdefault(k, "")
    return meta


def identify(path: Path) -> tuple[dict | None, str, str]:
    """Return (crossref message, doi, how) or (None, '', reason)."""
    side = read_sidecar(path)
    if side:
        return side, side.get("doi", "").lower(), f"sidecar {path.stem}.json"
    try:
        # DOIs are extracted from the first two pages only -- looking further
        # starts picking them out of reference lists. But the title is verified
        # against the first eight, because books and reports carry front matter
        # and their title page is rarely the first sheet. A wider verification
        # window can only confirm, never mislead.
        page1 = pdf_text(path, 2)
        front = pdf_text(path, 8)
    except Exception as exc:
        return None, "", f"cannot read the PDF ({exc})"

    tried: list[str] = []
    unresolved: list[str] = []      # looked up and got nothing back, which is not
                                    # the same failure as looked up and did not match
    name_conflict: list[str] = []   # resolved fine; the FILE NAME is what refused it
    title_mismatch: list[str] = []  # resolved fine; page 1 is what refused it
    # A 10.2210/pdb... DOI identifies a STRUCTURE DEPOSITION, not a paper, and its
    # title mirrors the paper's closely enough to pass any similarity test. Two
    # files in one batch resolved to the deposition instead of the article.
    def usable(d: str) -> bool:
        return not d.lower().startswith("10.2210/pdb")

    sources = [
        ("file name", filename_dois(path.name)),
        ("PDF metadata", pdf_metadata_dois(path)),
        ("first pages", [clean_doi(m) for m in DOI_RE.findall(page1)][:6]),
    ]
    for how, cands in sources:
        for doi in cands:
            if doi in tried or not usable(doi):
                continue
            tried.append(doi)
            meta = resolve_doi(doi)
            if not meta:
                unresolved.append(doi)
                continue
            # A DOI written into the file name is a deliberate act, so trust it
            # even when the PDF is a scan with no text to check it against.
            if how != "file name" and stem_conflict(path.stem, doi):
                # The file is named for one DOI and the text identifies another.
                # A scanned journal issue often opens on the PREVIOUS article's last
                # page, so the first text in the file belongs to a different paper --
                # that is how a CD4 paper came back as RNA polymerase II. The name is
                # the deliberate act; refuse rather than trust the text over it.
                name_conflict.append(doi)
                continue
            if how == "file name" and len(front.strip()) < 200 and not has_content(path):
                return None, "", ("this PDF has no text, no images and no marks on its first "
                                  "pages -- it looks like an empty or failed download rather "
                                  "than a scan. Nothing was filed; fetch it again.")
            if looks_right(meta, front) or (how == "file name" and len(front.strip()) < 200):
                return meta, doi.lower(), how + (" (unverified: no text in the PDF)"
                                                 if not looks_right(meta, front) else "")
            title_mismatch.append(doi)

    msg = crossref_search(page1)
    if msg and not usable(msg.get("doi", "")):
        msg = None
    if msg and looks_right(msg, front) and not stem_conflict(path.stem, msg.get("doi", "")):
        return msg, msg["doi"], "crossref title search"

    if tried:
        # These are different failures and used to read identically. A DOI that
        # no registry would answer for is a network or registry problem and the
        # file is probably fine; a DOI that resolved to a paper whose title is
        # not on page 1 means the file is not what its name says. Saying "none
        # matched the text on page 1" for the first sent at least one
        # investigation after the wrong thing.
        if len(unresolved) == len(tried):
            return None, "", ("no registry answered for the DOI(s) found -- a network or "
                              "registry failure, not a mismatch. Try again before "
                              "concluding anything about the file: "
                              + ", ".join(tried[:3]))
        # And one level deeper, for the same reason. "None matched the text on
        # page 1" is this function's summary of the whole loop, so it was the
        # message a file got when the TITLE matched perfectly and the FILE NAME
        # was what refused it. library spent a diagnosis on the title matcher
        # over exactly that on 2026-10-05, and the message is why. A refusal
        # names the test that refused.
        if name_conflict and not title_mismatch:
            return None, "", (
                "the file name and the text name different papers, so the DOI was "
                "not trusted: " + ", ".join(name_conflict[:3])
                + f" -- page 1 was never the problem. If the name is right (a "
                  f"browser often truncates or rewrites it), rename the file to "
                  f"the DOI or pass --key.")
        matched_none = [d for d in tried if d not in unresolved]
        detail = ", ".join(matched_none[:3])
        if unresolved:
            detail += f" (and {len(unresolved)} that no registry answered for)"
        if name_conflict:
            detail += (f" (and {len(name_conflict)} refused by the file name "
                       "rather than by page 1)")
        return None, "", ("found DOIs but none matched the text on page 1: " + detail)
    # What was measured is the LENGTH, so that is what this says. The old
    # wording asserted a cause -- "a scan with no text layer" -- that this
    # test never checked, and `library` found a probe with a text layer
    # `pdftotext` reads perfectly that is simply short. The remedy is the same
    # either way, which is exactly why the wrong cause could sit here: it sent
    # a reader to OCR a file that does not need it, and nothing else went
    # wrong to contradict it.
    n = len(page1.strip())
    if n < 200:
        return None, "", (f"page 1 yielded only {n} characters of text, too "
                          "little to identify the paper by -- usually a scan "
                          "with no text layer, sometimes a short first page. "
                          "Either way: rename it to its DOI, e.g. "
                          "10.2172_219366.pdf, and run again")
    return None, "", "no DOI in the file name, the metadata, or the first two pages"


# --------------------------------------------------------------------------
# asking the mirror what it already holds
# --------------------------------------------------------------------------

def existing_document(out: Path, doi: str, title: str = "") -> str:
    """The citation key of a reference the library already holds, else ''."""
    bib = (out / "library.bib").read_text(encoding="utf-8") if (out / "library.bib").exists() else ""
    key = ""
    if doi:
        for chunk in re.split(r"\n@", bib)[1:]:
            if re.search(rf"doi\s*=\s*\{{{re.escape(doi)}\}}", chunk, re.I):
                m = re.match(r"\w+\{([^,]+),", chunk)
                key = m.group(1).strip() if m else ""
                break
    if not key:
        # Either there was no DOI, or the DOI matched nothing -- fall back to an
        # exact normalized title match.
        #
        # The fallback used to be skipped whenever a DOI was present, which made
        # a whole class of reference invisible: an arXiv paper filed without a
        # doi field cannot be found by DOI, so dropping its
        # PDF in the inbox reported "new to the library" and would have created
        # a second reference for a paper already held. Trying the title after
        # the DOI misses costs one pass and cannot match more loosely than the
        # no-DOI path already does -- it demands exact equality of the
        # normalized title.
        want = " ".join(words(title))
        for chunk in re.split(r"\n@", bib)[1:]:
            m = re.search(r"title\s*=\s*\{+(.*?)\}+,?\s*$", chunk, re.M | re.S)
            if m and " ".join(words(m.group(1))) == want and want:
                k = re.match(r"\w+\{([^,]+),", chunk)
                key = k.group(1).strip() if k else ""
                break
    return key

