#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31", "pymupdf>=1.24"]
# ///
"""
inbox.py -- drop a PDF in, get a filed Mendeley reference out.

Save a paper into inbox/ straight from the browser, under whatever name the
publisher gave it, then:

    uv run --script inbox.py --dry-run    # what it thinks each PDF is
    uv run --script inbox.py              # file them (asks first)

For each PDF it works out which paper it is, finds the matching reference in
Mendeley or creates one from Crossref, uploads the PDF as an attachment, and
moves the file out of the folder into the local PDF cache -- outside the
mirror, so it is not synced to every machine. The next refresh extracts the
text like any other reference.

Grey literature -- DTIC/NTIS reports, theses, conference proceedings -- often
has no DOI to identify it by. For those, drop a sidecar JSON next to the PDF
with the same stem (report.pdf + report.json) holding the Mendeley fields:

    {"type": "report", "title": "...", "year": 1991,
     "source": "NRL Memorandum Report 6848 (DTIC ADA239276)",
     "authors": [{"first_name": "Arthur W.", "last_name": "Snow"}]}

A sidecar overrides identification entirely; nothing is looked up.

Identification, in order, stopping at the first candidate that checks out:

    the DOI in the file name, if you renamed it that way
    the DOI in the PDF's own metadata
    a DOI printed on the first two pages
    a Crossref search on the text of the first page

Every candidate is verified before it is used: the title Crossref returns has
to actually appear on the PDF's first page. A PDF that cannot be identified
that way is left in the inbox and reported, because attaching a paper to the
wrong reference is worse than not filing it.

This is the one script here besides mendeley_push.py that WRITES to Mendeley.
It asks before sending unless you pass --yes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

import pymupdf
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from mendeley_mirror import (API, DEFAULT_OUT, Mendeley, config_dir,
                                 get_app_config, load_json, local_id,
                                 mirror_state_dir, page_is_garbled, pdftotext_pages)
    from mendeley_push import CSL_TO_MENDELEY, DOC_CT, csl_year, one, split_name
    from get_pdf import cache_dir
except ImportError as exc:
    sys.exit(f"inbox.py must sit beside the other mirror scripts ({exc})")

FILE_CT = "application/vnd.mendeley-file.1+json"
# A DOI may contain brackets, angle brackets and semicolons, and old Elsevier and
# Wiley DOIs routinely do: 10.1016/s0032-3861(01)00634-6, and the SICI form
# 10.1002/(SICI)1097-4628(19990906)73:10<1927::AID-APP12>3.0.CO;2-O. Excluding
# those characters here truncated such a DOI mid-string -- 10.1016/s0032-3861(01)
# 00634-6 became "10.1016/s0032-3861(01", which then matched nothing and reported
# the paper as unidentifiable. So match everything up to whitespace or a quote,
# and let clean_doi decide which trailing characters are punctuation.
DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"']+", re.I)

CLOSERS = {")": "(", "]": "[", "}": "{", ">": "<"}
UA = "offprint inbox.py (mailto:cfa22@drexel.edu)"

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
    where the words should be. mendeley_mirror has re-read those with pdftotext
    since 2026-09-16, and inbox.py did not -- so a paper whose text layer is
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
    """Both metadata shapes, reduced to the fields a Mendeley record needs."""
    return {
        # Share the pusher's type map so a book filed through the inbox is a
        # book, not a journal article.
        "type": CSL_TO_MENDELEY.get((raw.get("type") or "").lower(), "generic"),
        "publisher": one(raw.get("publisher")),
        "doi": (raw.get("DOI") or raw.get("doi") or "").lower(),
        "title": one(raw.get("title")),
        "source": one(raw.get("container-title")),
        # The issue year, not the online one -- see csl_year in mendeley_push.
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


def crossref_search(text: str) -> dict | None:
    """Ask Crossref what paper this first page belongs to."""
    query = " ".join(text.split()[:60])
    r = requests.get("https://api.crossref.org/works",
                     params={"query.bibliographic": query, "rows": 3},
                     headers={"User-Agent": UA}, timeout=60)
    if r.status_code != 200:
        return None
    items = r.json()["message"].get("items") or []
    return normalize(items[0]) if items else None


def title_of(meta: dict) -> str:
    return meta.get("title", "")


def stem_conflict(stem: str, doi: str) -> bool:
    """True when the file name looks like it names a DIFFERENT DOI than `doi`.

    Only fires when the stem is recognisably a DOI suffix -- "387527a0",
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
    if len(page1.strip()) < 200:
        return None, "", ("this PDF is a scan with no text layer, so there is nothing "
                          "to identify it by -- rename it to its DOI, e.g. "
                          "10.2172_219366.pdf, and run again")
    return None, "", "no DOI in the file name, the metadata, or the first two pages"


# --------------------------------------------------------------------------
# talking to Mendeley
# --------------------------------------------------------------------------

def existing_document(out: Path, doi: str, title: str = "") -> tuple[str, str]:
    """(mendeley id, citekey) for a reference already in the library, else ('','')."""
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
        # a whole class of reference invisible: an arXiv paper pushed into
        # Mendeley without a doi field cannot be found by DOI, so dropping its
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
    if not key:
        return "", ""
    keymap = load_json(mirror_state_dir(out) / "citekeys.json", {})
    for ident, citekey in keymap.items():
        if citekey == key:
            # Only a Mendeley id can be attached to; a key from another backend
            # resolves to no id, and the caller reports it as unresolved.
            return local_id(ident) or "", key
    return "", key


def remote_doi_index(client: Mendeley) -> dict[str, str]:
    """Every DOI in Mendeley right now, mapped to its document id.

    library.bib only knows what the last refresh saw. A reference pushed since
    then -- or added on another machine -- is absent from it, and creating a
    second record for it would leave a duplicate in the library that nothing
    here can clean up. So ask Mendeley itself before creating anything.
    """
    index = {}
    for doc in client.paged("/documents", "documents", quiet=True):
        doi = ((doc.get("identifiers") or {}).get("doi") or "").strip().lower()
        if doi:
            index[doi] = doc["id"]
    return index


def has_attachment(client: Mendeley, doc_id: str) -> bool:
    resp = client.get(f"{API}/files", accept=None, params={"document_id": doc_id})
    files = resp.json() if resp.ok else []
    return any("pdf" in (f.get("mime_type") or "").lower() for f in files)


def create_document(client: Mendeley, meta: dict) -> str:
    if meta["doi"].startswith("10.48550/arxiv"):
        # arXiv DOIs resolve with no container title; match the push script's
        # convention so the entry is not left journal-less.
        meta["source"] = meta.get("source") or "arXiv"
    doc = {
        "type": meta.get("type", "journal"),
        "title": meta["title"],
        "authors": meta["authors"],
        "year": meta["year"],
        "source": meta["source"],
        "identifiers": {k: v for k, v in (("doi", meta["doi"]),) if v},
        "publisher": meta.get("publisher", ""),
        "volume": meta["volume"],
        "issue": meta["issue"],
        "pages": meta["pages"],
    }
    doc = {k: v for k, v in doc.items() if v not in (None, "", [], {})}
    resp = client.session.post(
        f"{API}/documents",
        headers={**client._auth_header(), "Content-Type": DOC_CT, "Accept": DOC_CT},
        data=json.dumps(doc), timeout=60)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Mendeley refused the new reference "
                           f"({resp.status_code}): {resp.text[:200]}")
    return resp.json()["id"]


def upload(client: Mendeley, doc_id: str, path: Path) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", path.name)
    headers = {
        **client._auth_header(),
        "Content-Type": "application/pdf",
        "Accept": FILE_CT,
        "Content-Disposition": f'attachment; filename="{safe}"',
        "Link": f'<{API}/documents/{doc_id}>; rel="document"',
    }
    resp = client.session.post(f"{API}/files", headers=headers,
                               data=path.read_bytes(), timeout=300)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"upload refused ({resp.status_code}): {resp.text[:200]}")
    return resp.json().get("id", "")


# --------------------------------------------------------------------------

REFRESH_UNIT = "mendeley-mirror.service"


def refresh_command(which=shutil.which, run=subprocess.run) -> str:
    """How to refresh on *this* machine, named so it cannot cause a double run.

    This string is printed at the one moment the reader is most likely to obey
    it immediately, so it must not name a command that can overlap a scheduled
    refresh. Starting the systemd service cannot run twice at once; calling
    run_mirror.sh directly can, and two refreshes at once make Syncthing
    conflict files out of .mirror/. So prefer the unit wherever it is installed,
    and fall back to the launcher only where it is not.

    `which` and `run` are injectable for the tests: probing the real systemd on
    the machine running the suite would make the result depend on the host.
    """
    if os.name == "nt":
        return "run_mirror.bat"
    systemctl = which("systemctl")
    if systemctl:
        try:
            probe = run([systemctl, "--user", "list-unit-files", REFRESH_UNIT],
                        capture_output=True, text=True, timeout=5)
            if probe.returncode == 0 and REFRESH_UNIT in (probe.stdout or ""):
                return f"systemctl --user start {REFRESH_UNIT}"
        except (OSError, subprocess.SubprocessError):
            pass  # no usable systemd: fall through to the launcher
    return "./run_mirror.sh"


def cache_target(dest: Path, stem: str, src: Path) -> Path:
    """Where to park this PDF without destroying one already there.

    The cache was named for the citation key alone, so a SECOND attachment on the
    same record -- Supporting Information, a corrigendum, a scanned appendix --
    overwrote the first. The article was replaced by its SI under the article's
    own name, and get_pdf.py then served that to anyone who asked for the paper.
    Nothing crashed, which is what made it dangerous.

    So mirror what the extractor already does with text/<key>-2.md: the first file
    keeps the bare stem and the rest are suffixed. A byte-identical file is not a
    second attachment -- it is the same one being filed again, so reuse its name
    rather than accumulating copies.
    """
    first = dest / f"{stem}.pdf"
    if not first.exists():
        return first
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    n = 1
    while True:
        cand = first if n == 1 else dest / f"{stem}-{n}.pdf"
        if not cand.exists():
            return cand
        if hashlib.sha256(cand.read_bytes()).hexdigest() == digest:
            return cand          # already cached, same bytes: keep one copy
        n += 1


def closing_report(attached: list[str], skipped: list[tuple[str, str]],
                   stuck: list[tuple[str, str]], dry_run: bool = False,
                   refresh_cmd: str | None = None) -> str:
    """The last thing the run prints, and it must match what actually happened.

    Two constraints, both learned the hard way. The instruction to refresh is
    printed only when something was actually attached -- a failed run used to
    end by telling you to go pull down text that was never uploaded. And
    anything still sitting in inbox/ is printed LAST, because the common way to
    read this output is `| tail`, which shows the end and throws the exit status
    away. A reader who sees only the final lines must not come away thinking a
    failed run succeeded.
    """
    out: list[str] = []
    if attached and not dry_run:
        cmd = refresh_cmd if refresh_cmd is not None else refresh_command()
        out.append(f"Run `{cmd}` to pull the extracted text down.")
    if skipped:
        out.append(f"{len(skipped)} left in inbox/ (already had a PDF; --replace to override):")
        out += [f"      {name} -- {why}" for name, why in skipped]
    if stuck:
        out.append(f"! {len(stuck)} NOT filed, still in inbox/:")
        out += [f"      {name} -- {why}" for name, why in stuck]
        out.append("  Nothing was sent for these. Give each a DOI file name or a"
                   " sidecar JSON, then run again.")
    return "\n" + "\n".join(out) if out else ""


def main() -> int:
    ap = argparse.ArgumentParser(description="File PDFs dropped in inbox/ into Mendeley.")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="mirror directory")
    ap.add_argument("--dry-run", action="store_true", help="identify only, send nothing")
    ap.add_argument("--yes", action="store_true", help="do not ask before sending")
    ap.add_argument("--keep", action="store_true",
                    help="leave the PDFs in inbox/ instead of moving them to the cache")
    ap.add_argument("--replace", action="store_true",
                    help="attach even if the reference already has a PDF")
    args = ap.parse_args()

    out = args.out.expanduser()
    box = out / "inbox"
    box.mkdir(exist_ok=True)
    pdfs = sorted(p for p in box.glob("*.pdf") if p.is_file())
    if not pdfs:
        print(f"nothing to file -- drop PDFs in {box}")
        return 0

    print(f"{len(pdfs)} PDF(s) in {box}\n")
    plan = []
    stuck: list[tuple[str, str]] = []
    for path in pdfs:
        try:
            msg, doi, how = identify(path)
        except RuntimeError as exc:
            print(f"  ! {path.name}\n      {exc}")
            stuck.append((path.name, str(exc)))
            continue
        if not msg:
            print(f"  ? {path.name}\n      cannot identify: {how}")
            stuck.append((path.name, f"cannot identify: {how}"))
            continue
        doc_id, key = existing_document(out, doi, msg.get('title', ''))
        where = f"already in the library as {key}" if key else "new to the library"
        print(f"  - {path.name}")
        print(f"      {title_of(msg)[:90]}")
        print(f"      {doi}  ({how}; {where})")
        if how.startswith("crossref title search"):
            # This path asks Crossref live and does NOT reproduce: two files this week
            # identified cleanly in every dry run and then failed on the real one,
            # which breaks the promise that a dry run predicts what --yes will do.
            print("      ! identified by live title search -- this may fail on the real run."
                  " Rename it to its DOI first.")
        plan.append((path, msg, doi, doc_id, key))

    if not plan:
        print(closing_report([], [], stuck))
        return 1
    if args.dry_run:
        print("\n--dry-run: nothing sent.")
        print(closing_report([], [], stuck, dry_run=True))
        return 1 if stuck else 0

    tokens = load_json(config_dir() / "tokens.json", {})
    if not tokens.get("access_token"):
        sys.exit("no Mendeley tokens on this machine -- run ./run_mirror.sh once first.")

    if not args.yes:
        reply = input(f"\nFile {len(plan)} PDF(s) into Mendeley? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("aborted.")
            return 0

    client = Mendeley(get_app_config(), tokens)
    dest = cache_dir()
    status = 1 if stuck else 0
    attached: list[str] = []
    skipped: list[tuple[str, str]] = []
    remote: dict[str, str] | None = None
    for path, msg, doi, doc_id, key in plan:
        try:
            if not doc_id and doi:
                if remote is None:
                    print("  (checking Mendeley for references the mirror has not seen yet)")
                    remote = remote_doi_index(client)
                doc_id = remote.get(doi, "")
                if doc_id:
                    print(f"  = {doi} is already in Mendeley, just not mirrored yet")
            if not doc_id:
                doc_id = create_document(client, msg)
                print(f"  + created reference for {doi}")
            elif has_attachment(client, doc_id) and not args.replace:
                print(f"  = {key} already has a PDF attached; skipping (--replace to override)")
                skipped.append((path.name, f"{key} already has a PDF"))
                continue
            upload(client, doc_id, path)
            attached.append(path.name)
            print(f"  ✓ attached {path.name} -> {key or doi}")
            if not args.keep:
                # Prefer the citation key; fall back to the DOI, then to the
                # file's own name -- a sidecar-identified report has neither of
                # the first two, and an empty stem would give a nameless file.
                stem = key or re.sub(r"[^A-Za-z0-9]", "_", doi) or path.stem
                target = cache_target(dest, stem, path)
                shutil.move(str(path), target)
                print(f"    moved out of the inbox to {target}")
                side = path.with_suffix(".json")
                if side.exists():
                    shutil.move(str(side), target.with_suffix(".json"))
        except Exception as exc:
            print(f"  ! {path.name}: {exc}")
            stuck.append((path.name, str(exc)))
            status = 1

    print(closing_report(attached, skipped, stuck))
    return status


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
