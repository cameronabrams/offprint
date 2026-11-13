#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31", "pymupdf>=1.24"]
# ///
"""
mirror.py -- the mirror itself: what a library turns into on disk.

Not a CLI. This is the module every other script here imports, and it holds
everything that is true of the mirror whatever backend fills it: citation-key
assignment, the id namespacing, BibTeX and index generation, text extraction
with its page markers, annotation rendering, and the status files.

`zotero_source.py --refresh` is the entry point that drives it.

It produces, in the output directory:

    library.bib          BibTeX for the whole library, with stable citation keys
    index.md             one-line-per-reference index (fast to grep / skim)
    folders.json         collections -> citation keys
    text/<citekey>.md    full text extracted from the PDF, with page markers
    annotations/<citekey>.md   highlights and sticky notes, by page
    extraction-report.md what came out empty, and why that is probably so

By default each attachment is downloaded, its text extracted, and the PDF then
discarded: **the extract is the durable artifact and all that survives**, which
is why extraction is deterministic.

It holds no credentials and opens no connection of its own. `harvest_attachments`
takes a `fetch(file_id) -> bytes` and that is the entire backend interface --
the one place in this file that knows which service is being read.
"""

from __future__ import annotations

__version__ = "0.52.0"
"""The tool's version, and the only place it is written down.

It exists so a mirror can say what produced it. Extraction behavior has changed
under people's feet twice -- OCR for scanned attachments, then a pdftotext
fallback for garbled text layers -- and both times the only record of when was a
git log that the library itself does not carry. `mirror-status.md` and
`.mirror/state.json` now both record it, so "this extract predates the garbled-
text fix" is a question the library can answer about itself.

Bump it in the same commit as the behavior it describes; nothing generates it.
"""


import json
from collections import Counter
import os
import re
import socket
import sys
import time
import html
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit(
        "This script needs 'requests'. Either run it through uv, which installs\n"
        "it for you --   uv run --script zotero_source.py   -- or install it\n"
        "into the interpreter you are using:   pip install requests"
    )


def _default_out() -> Path:
    """Where the library lives.

    The directory was renamed to `~/Sync/library` on 2026-10-02, and the old
    name was accepted alongside it for as long as any machine might still have
    it. It lives inside the synced folder, so every machine agreed within one
    sync; the fallback was removed on 2026-11-13 once none was left. That is
    the end the dual-name period was for -- a deletion, not a permanent
    accommodation.
    """
    new = Path.home() / "Sync" / "library"
    if new.is_dir():
        return new
    return new          # a fresh install gets the new name


DEFAULT_OUT = _default_out()

# Set by --quiet, for scheduled runs: no progress spam, no prompts that would
# block forever with nobody at the keyboard.
QUIET = False
LOG: list = []


def note(msg: str = "") -> None:
    """A milestone worth keeping in the log."""
    LOG.append(msg)
    if not QUIET:
        print(msg)


def progress(msg: str) -> None:
    """Transient in-place progress; never logged."""
    if not QUIET:
        print(msg, end="\r", flush=True)




# --------------------------------------------------------------------------
# credential storage
# --------------------------------------------------------------------------



def mirror_state_dir(out: Path) -> Path:
    """Bookkeeping that belongs to the mirror, not to the machine.

    Citation keys and per-file extraction state live beside the mirrored files
    so that a synced folder carries them between machines: the Linux box then
    agrees with the Windows box about what `Muller2020Yield` means, and does not
    re-download 2500 PDFs to rebuild text it already has.

    Until 2026-11-13 this also copied both files out of the old per-machine
    credential directory, where they lived before 2026-09-24. That migration is
    done on every machine that syncs, because `.mirror/` travels inside the
    synced folder itself -- so a machine that has the library at all has them.
    """
    d = out / ".mirror"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    if os.name != "nt":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass















# --------------------------------------------------------------------------
# citation keys
# --------------------------------------------------------------------------

STOPWORDS = {
    "a", "an", "the", "on", "of", "in", "for", "and", "to", "with", "at", "by",
    "from", "into", "via", "is", "are", "as", "using", "toward", "towards",
}


def ascii_fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c))


def first_author_surname(doc: dict) -> str:
    for field in ("authors", "editors"):
        people = doc.get(field) or []
        if people:
            p = people[0]
            name = p.get("last_name") or p.get("name") or p.get("first_name") or ""
            name = ascii_fold(name)
            name = re.sub(r"[^A-Za-z]", "", name)
            if name:
                # keep acronym casing for corporate authors (NIST, NASA, IUPAC)
                return name if name.isupper() else name.capitalize()
    return "Anon"


def title_word(doc: dict) -> str:
    words = re.findall(r"[A-Za-z]+", ascii_fold(doc.get("title") or ""))
    for w in words:
        if w.lower() not in STOPWORDS and len(w) > 2:
            return w.capitalize()
    return words[0].capitalize() if words else "Untitled"


def make_citekey(doc: dict, taken: set) -> str:
    year = doc.get("year") or "n.d."
    base = f"{first_author_surname(doc)}{year}{title_word(doc)}"
    base = re.sub(r"[^A-Za-z0-9]", "", base)
    key = base
    suffix = ord("a")
    while key in taken:
        key = f"{base}{chr(suffix)}"
        suffix += 1
    taken.add(key)
    return key


# --------------------------------------------------------------------------
# identifier namespaces
#
# Every id this tool stores is qualified with the backend it came from, so that
# `zotero:abc` and a second service's `abc` cannot collide and nothing has to
# guess which
# service a stored id belongs to. It costs nothing while there is one backend
# and is impossible to retrofit safely once there are two: a citation key is
# assigned once per id and never changes, so an unqualified map that starts
# taking ids from a second source silently reassigns keys that are already
# cited in manuscripts and are file names under findings/.
# --------------------------------------------------------------------------

# The namespaces that appear in stored maps. "mendeley" is here because 2,739
# citation keys and 2,745 state entries on disk carry it, not because anything
# can still talk to that service: it is a fact about files, and removing it
# would make every one of those entries unreadable.
KNOWN_BACKENDS = ("mendeley", "zotero")

# What a BARE id means. Ids were unqualified until 2026-09-24, and every one
# written before that date came from the one backend that existed then. This is
# the only place that assumption lives.
LEGACY_BACKEND = "mendeley"


def is_qualified(ident: str) -> bool:
    """True for 'zotero:abc'. False for a bare id, and for 'http://x' -- a
    colon alone does not make a namespace, only a known backend before one does."""
    head, sep, _rest = ident.partition(":")
    return bool(sep) and head in KNOWN_BACKENDS


def qualify(raw_id: str, backend: str) -> str:
    """'abc' -> 'zotero:abc'. Idempotent, so it is safe to apply twice.

    **`backend` is required and has no default.** It used to default to the
    module constant, and the whole hazard of this file was a run that let it:
    namespacing one service's ids under another's name misses every existing
    entry and mints a fresh citation key over the top of one already cited in a
    manuscript, silently. A parameter that must be named cannot be defaulted
    wrongly.
    """
    return raw_id if is_qualified(raw_id) else f"{backend}:{raw_id}"


def split_id(ident: str) -> tuple[str, str]:
    """('zotero', 'abc'). A bare id is read as `LEGACY_BACKEND`'s, which is what
    makes every map written before namespacing still readable."""
    head, sep, rest = ident.partition(":")
    return (head, rest) if sep and head in KNOWN_BACKENDS else (LEGACY_BACKEND, ident)


def local_id(ident: str, backend: str) -> str | None:
    """The raw id if it belongs to `backend`, else None.

    The None is the point. A script that talks to one service must refuse a key
    belonging to another rather than send someone else's id to an API that will
    cheerfully report it as not found -- or, worse, match it.
    """
    source, raw = split_id(ident)
    return raw if source == backend else None


def qualify_map(d: dict) -> tuple[dict, int]:
    """Namespace the keys of a stored map, returning (map, how many changed).

    **It takes no backend, and that is deliberate**: it migrates *bare* ids, and
    a bare id means `LEGACY_BACKEND`'s by definition, whatever run is reading
    the file. Passing a backend here would let a later one claim ids it never
    wrote. A test covers this, because it looks exactly like an omission.

    Order is preserved so a migrated file diffs against its predecessor line for
    line, and the migration is idempotent: running it on an already-qualified
    map changes nothing and reports zero.
    """
    out, moved = {}, 0
    for k, v in d.items():
        q = qualify(k, LEGACY_BACKEND)
        if q != k:
            moved += 1
        out[q] = v
    return out, moved


def assign_citekeys(docs: list, keymap_path: Path, backend: str) -> dict:
    """Keys, once assigned to a document id, never change between runs.

    `backend` is named by the caller and cannot be defaulted. A run that
    namespaced its ids under the wrong name would miss every existing entry and
    assign thousands of brand-new keys over the top of keys already cited in
    manuscripts and used as file names under `findings/` -- silently, and
    indistinguishably from a first run.

    `qualify_map` below is NOT given the backend. See its docstring.
    """
    keymap, migrated = qualify_map(load_json(keymap_path, {}))
    if migrated:
        # Key text is untouched -- only the id it hangs on changes -- so every
        # citation already in a manuscript still resolves to the same paper.
        note(f"  namespaced {migrated} citation-key ids as {LEGACY_BACKEND}:<id>")
    taken = set(keymap.values())
    # Deterministic order for first assignment, so a fresh run is reproducible.
    for doc in sorted(docs, key=lambda d: (str(d.get("created", "")), d["id"])):
        if qualify(doc["id"], backend) not in keymap:
            keymap[qualify(doc["id"], backend)] = make_citekey(doc, taken)
    save_json(keymap_path, keymap)
    return keymap


# --------------------------------------------------------------------------
# BibTeX
# --------------------------------------------------------------------------

TYPE_MAP = {
    "journal": "article",
    "magazine_article": "article",
    "newspaper_article": "article",
    "book": "book",
    "book_section": "incollection",
    "encyclopedia_article": "incollection",
    "conference_proceedings": "inproceedings",
    "thesis": "phdthesis",
    "report": "techreport",
    "working_paper": "techreport",
    "web_page": "misc",
    "computer_program": "misc",
    "patent": "misc",
    "generic": "misc",
}

TEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "$": r"\$",
    "&": r"\&",
    "%": r"\%",
    "#": r"\#",
    "_": r"\_",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


# Unicode that publisher metadata carries but a non-UTF-8 LaTeX build cannot read.
# The dashes, quotes and spaces are the dangerous ones: they masquerade as ASCII, so
# a broken .bib looks correct in an editor. Crossref hands out U+2010 for hyphenated
# names ("Jean-Pierre") and U+2013 for page ranges. Accented letters are deliberately
# NOT in here -- they are legitimate UTF-8 and every modern engine handles them.
UNICODE_TEX = {
    # dashes and hyphens
    "\u2010": "-", "\u2011": "-", "\u2012": "--", "\u2013": "--",
    "\u2014": "---", "\u2015": "---", "\u2212": "$-$",
    # quotation marks and primes
    "\u2018": "`", "\u2019": "'", "\u201a": ",", "\u201b": "`",
    "\u201c": "``", "\u201d": "''", "\u201e": ",,",
    "\u2032": "$'$", "\u2033": "$''$",
    "\u00ab": "``", "\u00bb": "''", "\u00a1": r"!`", "\u00bf": r"?`",
    "\u02da": r"\textdegree{}",  # RING ABOVE, routinely misused for a degree sign
    # spaces of every width collapse to an ordinary one
    "\u00a0": " ", "\u2002": " ", "\u2003": " ", "\u2007": " ", "\u2008": " ",
    "\u2009": " ", "\u200a": " ", "\u202f": " ", "\u3000": " ",
    # symbols with standard LaTeX spellings
    "\u00b0": r"\textdegree{}", "\u2103": r"\textdegree{}C",
    "\u00b1": "$\\pm$", "\u00d7": "$\\times$", "\u00f7": "$\\div$",
    "\u2248": "$\\approx$", "\u223c": "$\\sim$", "\u2260": "$\\neq$",
    "\u2264": "$\\leq$", "\u2265": "$\\geq$", "\u221e": "$\\infty$",
    "\u2190": "$\\leftarrow$", "\u2192": "$\\rightarrow$",
    "\u2194": "$\\leftrightarrow$", "\u21d4": "$\\Leftrightarrow$",
    "\u2211": "$\\sum$", "\u220f": "$\\prod$", "\u2208": "$\\in$",
    "\u2206": "$\\Delta$", "\u2202": "$\\partial$",
    "\u00b7": "$\\cdot$", "\u2217": "$*$", "\u2215": "/",
    "\u2026": r"\ldots{}", "\u2022": r"\textbullet{}",
    "\u00a9": r"\textcopyright{}", "\u00ae": r"\textregistered{}",
    "\u2122": r"\texttrademark{}", "\u00a7": r"\S{}",
    "\u2020": r"\dag{}", "\u2021": r"\ddag{}",
    # mojibake from a bad extraction -- drop rather than emit a tofu box
    "\ufffd": "",
}

# any run of dash-like characters in a page range is one BibTeX en-dash
PAGE_DASHES = re.compile(r"[-\u2010-\u2015\u2212]+")


def format_pages(s: str) -> str:
    """1234-1245, 1234\u20131245 and an already-correct 1234--1245 all give 1234--1245."""
    return PAGE_DASHES.sub("--", tex_escape_plain(s))


def html_decode(s: str) -> str:
    """Undo HTML entities before anything else looks at the string.

    Crossref and the backend hand back journal names, titles and URLs with HTML
    entities in them -- "Molecular Systems Design &amp; Engineering" is a real
    example. Escaping that for LaTeX without decoding it first turns the "&"
    into "\\&" and leaves the "amp;" sitting there, so the bibliography renders
    "Design &amp; Engineering". Decode first, then escape, and the reader sees
    an ampersand.

    Only one level is undone, which is what we want: a genuinely double-encoded
    "&amp;amp;" should come back as the literal "&amp;" it encodes.
    """
    return html.unescape(str(s))


def tex_escape_plain(s: str) -> str:
    """Normalize unicode without applying the LaTeX dash rules -- for page ranges."""
    out = []
    for ch in html_decode(s):
        if unicodedata.category(ch) in ("Cf", "Co"):
            continue  # zero-width joiners, BOMs, private-use junk
        out.append(ch)
    return "".join(out).replace("\n", " ").strip()


def tex_escape(s: str) -> str:
    out = []
    for ch in html_decode(s):
        if ch in UNICODE_TEX:
            out.append(UNICODE_TEX[ch])  # already LaTeX; must not be re-escaped
        elif unicodedata.category(ch) in ("Cf", "Co"):
            continue  # zero-width joiners, BOMs, private-use junk
        else:
            out.append(TEX_ESCAPES.get(ch, ch))
    return "".join(out).replace("\n", " ").strip()


def format_authors(people: list) -> str:
    parts = []
    for p in people or []:
        last = (p.get("last_name") or "").strip()
        first = (p.get("first_name") or "").strip()
        if last and first:
            parts.append(f"{tex_escape(last)}, {tex_escape(first)}")
        elif last or first:
            parts.append(tex_escape(last or first))
        elif p.get("name"):
            name = p["name"].strip()
            if name.lower() == "others":
                # `and others` IS BibTeX's et-al. marker. Brace-wrapping it
                # makes it a literal author surnamed "others", which is how
                # Saibil1993Atp, Demeyts1973Insulin and Ullrich1985Human
                # acquired one. The braces are right for a corporate name and
                # wrong for this one word.
                parts.append("others")
            else:
                parts.append("{" + tex_escape(name) + "}")
    return " and ".join(parts)


def bib_entry(doc: dict, key: str, include_abstract: bool = True) -> str:
    entry_type = TYPE_MAP.get(doc.get("type", "generic"), "misc")
    ids = doc.get("identifiers") or {}
    fields: list[tuple[str, str]] = []

    def add(name, value):
        if value:
            fields.append((name, str(value)))

    if doc.get("authors"):
        add("author", format_authors(doc["authors"]))
    if doc.get("editors"):
        add("editor", format_authors(doc["editors"]))
    if doc.get("title"):
        # inner braces protect the capitalization of chemical formulas, acronyms,
        # and proper nouns against styles that lowercase titles
        fields.append(("title", "{" + tex_escape(doc["title"]) + "}"))
    if not (doc.get("authors") or doc.get("editors")):
        # BibTeX needs author or key to sort an entry
        add("key", key)

    # `source` is the venue, and for four of the seven entry types there was no
    # field to put it in and no else-branch, so it was dropped: 66 records here,
    # 28 of which carry volume/pages/DOI and no venue of any kind. That is not an
    # entry that looks incomplete -- Berman2000Protein printed "28(1)" with
    # nothing to put it in. Worse, <out>/CLAUDE.md tells the filer to put a
    # report's accession number in `source` precisely because there is no
    # identifier field for it, and reports become @techreport. The documented
    # procedure wrote into the field the documented output discarded.
    # Anything not claimed by a real field now goes to `note`, which is valid in
    # every standard entry type (`howpublished` is not, and @article is 97% of
    # the library).
    source = doc.get("source")
    source_placed = False
    if entry_type == "article":
        add("journal", tex_escape(source) if source else None)
        source_placed = bool(source)
    elif entry_type in ("inproceedings", "incollection"):
        add("booktitle", tex_escape(source) if source else None)
        source_placed = bool(source)

    add("year", doc.get("year"))
    add("volume", doc.get("volume"))
    add("number", doc.get("issue"))
    add("pages", format_pages(doc.get("pages") or "") or None)
    add("publisher", tex_escape(doc.get("publisher")) if doc.get("publisher") else None)
    if entry_type == "phdthesis":
        # There is ONE thesis type and it does not say which degree. TYPE_MAP
        # renders it @phdthesis, and a style then prints "PhD thesis" -- a claim
        # the source never made. Deserno1999Efficient is chapter 3 of someone
        # else's dissertation and carried it. `type` overrides that label in the
        # standard styles, which is the cheap honest fix: say "Thesis" and let
        # `note` carry whatever the record actually said.
        add("type", "Thesis")
    if doc.get("institution"):
        # a thesis wants school; a report wants institution
        add("school" if entry_type == "phdthesis" else "institution",
            tex_escape(doc["institution"]))
    add("address", tex_escape(doc.get("city")) if doc.get("city") else None)
    add("doi", ids.get("doi"))
    add("issn", ids.get("issn"))
    add("isbn", ids.get("isbn"))
    if ids.get("pmid"):
        add("pmid", ids["pmid"])
    if ids.get("arxiv"):
        add("eprint", ids["arxiv"])
    websites = doc.get("websites") or []
    if websites:
        # URLs are emitted raw -- no TeX escaping -- so they need the decode of
        # their own, or a query string arrives as "...&amp;lr=..." and does not
        # resolve when clicked.
        add("url", html_decode(websites[0]))
    if doc.get("keywords"):
        add("keywords", tex_escape(", ".join(doc["keywords"])))
    if include_abstract and doc.get("abstract"):
        add("abstract", tex_escape(doc["abstract"]))

    notes = []
    if source and not source_placed:
        notes.append(tex_escape(source))
    if not doc.get("title"):
        notes.append(f"Untitled record (document id {doc.get('id', '?')})")
    if notes:
        add("note", ". ".join(notes))

    lines = [f"@{entry_type}{{{key},"]
    width = max((len(n) for n, _ in fields), default=0)
    for name, value in fields:
        lines.append(f"  {name.ljust(width)} = {{{value}}},")
    lines.append("}")
    return "\n".join(lines)


def write_bibtex(docs: list, keymap: dict, out: Path, include_abstract: bool,
                 backend: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    chunks = [
        f"% Library mirror -- generated {stamp}",
        f"% {len(docs)} references. Do not edit by hand; edit in the library and re-run.",
        "",
    ]
    for doc in sorted(docs, key=lambda d: keymap[qualify(d["id"], backend)].lower()):
        chunks.append(bib_entry(doc, keymap[qualify(doc["id"], backend)],
                                include_abstract))
        chunks.append("")
    (out / "library.bib").write_text("\n".join(chunks), encoding="utf-8")


# --------------------------------------------------------------------------
# index, folders, annotations
# --------------------------------------------------------------------------

def write_index(docs: list, keymap: dict, files_by_doc: dict, ann_by_doc: dict,
                out: Path, backend: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Library index",
        "",
        f"{len(docs)} references, mirrored {stamp}.",
        "",
        "| key | year | first author | title | journal | doi | text | notes |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for doc in sorted(docs, key=lambda d: keymap[qualify(d["id"], backend)].lower()):
        key = keymap[qualify(doc["id"], backend)]
        ids = doc.get("identifiers") or {}
        title = (doc.get("title") or "").replace("|", r"\|")
        source = (doc.get("source") or "").replace("|", r"\|")
        if (out / "text" / f"{key}.md").exists():
            has_text = "yes"
        elif files_by_doc.get(doc["id"]):
            has_text = "no"  # there is a file, but no text came out of it
        else:
            has_text = ""
        lines.append(
            f"| `{key}` | {doc.get('year','')} | {first_author_surname(doc)} | {title} | "
            f"{source} | {ids.get('doi','')} | {has_text} | "
            f"{len(ann_by_doc.get(doc['id'], []))} |"
        )
    (out / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_folders(folders: list, folder_docs: dict, keymap: dict, out: Path,
                  backend: str) -> None:
    by_id = {f["id"]: f for f in folders}

    def full_name(f):
        parts, cur, guard = [], f, 0
        while cur and guard < 20:
            parts.append(cur.get("name", "?"))
            cur = by_id.get(cur.get("parent_id"))
            guard += 1
        return "/".join(reversed(parts))

    payload = {
        full_name(f): sorted(
            keymap[qualify(d, backend)] for d in folder_docs.get(f["id"], [])
            if qualify(d, backend) in keymap
        )
        for f in folders
    }
    save_json(out / "folders.json", payload)


def annotation_markdown(doc: dict, key: str, annotations: list) -> str:
    ids = doc.get("identifiers") or {}
    author_names = []
    for p in doc.get("authors") or []:
        first = (p.get("first_name") or "").strip()
        last = (p.get("last_name") or p.get("name") or "").strip()
        joined = f"{first} {last}".strip()
        if joined:
            author_names.append(joined)
    head = [
        f"# {doc.get('title') or '(untitled)'}",
        "",
        f"- **key**: `{key}`",
        f"- **authors**: {', '.join(author_names)}",
        f"- **year**: {doc.get('year', '')}",
        f"- **source**: {doc.get('source', '')}",
    ]
    if ids.get("doi"):
        head.append(f"- **doi**: {ids['doi']}")
    head += ["", "---", ""]

    def page_of(a):
        pos = a.get("positions") or []
        return pos[0].get("page", 0) if pos else 0

    def top_of(a):
        pos = a.get("positions") or []
        if pos and isinstance(pos[0].get("top_left"), dict):
            return pos[0]["top_left"].get("y", 0)
        return 0

    body = []
    for a in sorted(annotations, key=lambda a: (page_of(a), top_of(a))):
        page = page_of(a)
        text = (a.get("text") or "").strip()
        kind = a.get("type", "annotation")
        if kind == "highlight" and not text:
            # The backend returns coordinates for highlights but not always the
            # highlighted text; the location is still worth recording.
            body.append(f"- *(highlight, p. {page})*")
        elif kind == "highlight":
            body.append(f"- > {text}\n  \n  *(p. {page})*")
        elif text:
            body.append(f"- **note (p. {page})**: {text}")
    if not body:
        body = ["*(no annotations)*"]
    return "\n".join(head + body) + "\n"


# --------------------------------------------------------------------------
# PDF text extraction
# --------------------------------------------------------------------------

# A page of a text-bearing paper yields hundreds of characters. A scanned page
# yields a handful of stray marks, if that.
def load_removed(out: Path) -> dict:
    """Attachment stems a person deliberately deleted from `<out>/pdf/`.

    `--backfill` tests only whether a file is on disk, so it cannot tell
    "never evacuated" from "evacuated, then deleted on purpose". Re-running the
    evacuation -- which is documented as the way to finish an interrupted one,
    and is resumable by construction -- therefore undid the deletion silently.
    Five files removed on 2026-10-01 are the case this was written for.

    Keyed by STEM (`<citekey>` or `<citekey>-N`), not by attachment id: the
    citation key is the one handle that survives a change of backend, and no
    two backends share an id.

    Format is `stem<TAB>date<TAB>reason`, `#` comments and blank lines ignored.
    Only the first field is required, and any non-blank line yields a stem, so
    a hand-edit cannot quietly fail to protect a file by being malformed.

    **This file never causes a deletion.** It withholds a fetch, nothing else.
    An entry whose file is present on disk is left exactly where it is -- the
    tool does not read it as an instruction to remove anything, because an
    archive-eroding path is the defect this repo has now fixed twice.
    """
    path = mirror_state_dir(out) / "removed.tsv"
    if not path.exists():
        return {}
    out_map = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        stem = parts[0].strip()
        if stem:
            out_map[stem] = " ".join(p_.strip() for p_ in parts[1:] if p_.strip())
    return out_map


# The statuses that mean an attachment was examined and a verdict reached.
# Anything else -- `failed`, an unrecognized value, or nothing at all -- is
# unfinished work and gets re-examined. Deliberately a list of what IS done
# rather than of what is not: a new failure status added later should default to
# being retried, not to being skipped.
DONE_STATUSES = ("ok", "ocr", "garbled", "no-text", "not-pdf")

# Bumped when the DECISION extraction makes changes -- not when the code moves,
# and not with `__version__`. A stored verdict was reached under one set of
# rules, and a later run with different rules is asking a different question of
# the same bytes.
#
#   1  the original volume test
#   2  2026-10-03: a page-fraction test as well, so a scan whose cover page
#      carries a text layer reaches OCR
#   3  2026-10-04: that fraction measured on CONTENT rather than raw text, so a
#      stamp repeated on every image page stops counting as text
#
# `Kirkpatrick1983Optimization` is why this exists. It was extracted under
# rules 1, stored `ok`, and the run that shipped rules 2 skipped it as already
# done -- so the fix never reached the one paper it was written for. Every
# improvement to extraction has this shape: the papers that would benefit are
# exactly the ones already marked finished.
EXTRACT_RULES = 3


def content_of_extract(text: str) -> int:
    """How much actual body an existing `text/<key>.md` carries.

    The front matter and the page markers are not content, so a file holding
    nothing but a header does not count as better than a fresh read of zero.
    """
    if not text:
        return 0
    body = text.split("---", 2)[-1] if text.startswith("---") else text
    body = re.sub(r"<!-- p\. \d+ -->", "", body)
    return len(body.strip())

PDF_MAGIC = b"%PDF-"


def looks_like_pdf(data: bytes) -> bool:
    """Is this a PDF by its bytes? The backend's MIME type does not decide.

    `mime_type` is the backend's opinion and it is sometimes wrong. One backend
    reported a non-PDF type for `Vanommeslaeghe2009Charmm` -- a complete
    20-page CGenFF paper with an intact text layer -- and the attachment was
    skipped, cached as skipped, and never mentioned in extraction-report.md.
    Zotero's attachment metadata is a second set of opinions nobody has
    audited, and the first Zotero refresh re-examines every attachment at once.

    The header is allowed a short preamble because some writers emit bytes
    before it and every PDF reader tolerates that.
    """
    return PDF_MAGIC in data[:1024]


def archive_suffix(f: dict, is_pdf: bool) -> str:
    """The extension `keep` and `--backfill` archive this attachment under."""
    return ".pdf" if is_pdf else (Path(f.get("file_name") or "").suffix or ".bin")


def find_archived(pdf_dir: Path, stem: str, f: dict | None = None) -> Path | None:
    """The archived copy under whichever suffix it was actually written with.

    The inverse of `archive_suffix`, and it has to exist: `keep` and
    `--backfill` write the attachment's real extension, so this library holds
    `.cif`, `.pdb`, `.png`, `.avi` and one mangled `.-_charmm_g` beside 2,739
    `.pdf`. A lookup hardcoded to `.pdf` cannot see any of them, so a refresh
    went to the network for bytes already sitting on disk -- against an account
    that is being retired and has already been downgraded.

    Candidates rather than a glob: a citation key is alphanumeric today, but a
    glob would still be the kind of thing that quietly matches a neighbour.
    """
    candidates = [f"{stem}.pdf"]
    if f is not None:
        suffix = Path(f.get("file_name") or "").suffix
        if suffix and suffix != ".pdf":
            candidates.append(f"{stem}{suffix}")
        candidates.append(f"{stem}.bin")
    for name in candidates:
        path = pdf_dir / name
        if path.exists() and path.stat().st_size > 0:
            return path
    return None


MIN_CHARS_PER_PAGE = 80

# A scan can clear the whole-document threshold on one page. Kirkpatrick1983's
# JSTOR PDF is 11 pages: page 1 is JSTOR's cover, with a text layer, and pages
# 2-11 are images. 2,986 characters over 11 pages passes `80 * 11 = 880`
# comfortably, so OCR never ran and the article body is invisible to search.
#
# The test is therefore how many PAGES carry text, not how many characters the
# document has. Half is the line, and the negative case sets it: a born-digital
# paper with one or two figure-only pages must NOT be OCR'd wholesale, and that
# is 9 pages in 11 with text. A cover-plus-images scan is 1 in 11.
MIN_PAGE_TEXT_FRACTION = 0.5


def page_text_fraction(page_texts: list[str]) -> float:
    """What share of the pages carry text of their own.

    **Of their own** is the whole of it. The first version of this counted
    `<!-- p. N -->` markers, which is a count of pages that produced *any*
    output — and a JSTOR scan stamps its 152-character footer onto every image
    page, so all eleven produced output and none was OCR'd. Measured on content,
    after page-repeated lines are dropped, the same document is 1 page in 11.

    A page below `MIN_CHARS_PER_PAGE` of its own text is not carrying any, which
    is the same floor the whole-document test uses.
    """
    if not page_texts:
        return 1.0
    per = content_per_page(page_texts)
    return sum(1 for n in per if n >= MIN_CHARS_PER_PAGE) / len(per)

# How much of a document's pages a line must appear on to be boilerplate rather
# than content. A running head or a library stamp is on all of them; a sentence
# is on one.
BOILERPLATE_SHARE = 0.6


def content_chars(page_texts: list[str]) -> int:
    """Characters left once lines repeated across most pages are discarded.

    Counting raw characters cannot tell a paper from a scan of a paper. A
    ProQuest copy of Lin & Rye 2006 carried *exactly* 103 characters on every
    one of its 29 pages -- the same "Reproduced with permission of the copyright
    owner" stamp, one image, and nothing else. That is 1.3x MIN_CHARS_PER_PAGE,
    so a per-page character count calls it a text layer and the paper stops
    being reported as unreadable. A silent gap is worse than a known one,
    because a search over text/ then returns a confident zero for it.

    So measure what is left after removing the lines that repeat: for the stamped
    scan that is nothing, while a real paper loses only its running head.

    Takes the *raw* page text, before clean_page_text. That ordering is
    load-bearing: cleaning joins a line to the next one when the break is not
    after sentence punctuation, so a running head ending in ")" is spliced onto
    the first sentence of the body and stops being a repeated line at all.
    Boilerplate has to be found while the lines are still lines.

    Short documents are exempt -- across one or two pages "repeated on most
    pages" means nothing, and a two-page paper whose header matches its footer
    should not be judged on it.
    """
    return sum(content_per_page(page_texts))


def content_per_page(page_texts: list[str]) -> list[int]:
    """Characters left on EACH page once page-repeated lines are discarded.

    The per-page form of the same judgement, factored out because the share of
    pages carrying real text answers a question the total cannot:
    `Kirkpatrick1983Optimization` is a JSTOR scan whose every image page carries
    the identical 152-character footer, so every page "has text", the
    page-fraction test introduced in 0.26.0 saw 11 of 11, and OCR never ran.
    Judged on content instead, it is 1 of 11.

    Exactly the ProQuest stamp again, which this function was written for — and
    the per-page test was built on raw text while the total beside it was
    already built on content.
    """
    pages = [t.strip() for t in page_texts]
    if len(pages) < 3:
        return [len(t) for t in pages]

    seen: dict[str, int] = {}
    for text in pages:
        for line in {" ".join(x.split()).casefold()
                     for x in text.splitlines() if x.strip()}:
            seen[line] = seen.get(line, 0) + 1
    # max(3, ...) so a 3-page document needs unanimity, not a 2-of-3 accident
    threshold = max(3, int(len(pages) * BOILERPLATE_SHARE))
    boilerplate = {line for line, n in seen.items() if n >= threshold}
    if not boilerplate:
        return [len(t) for t in pages]

    out = []
    for text in pages:
        kept = [x for x in text.splitlines()
                if " ".join(x.split()).casefold() not in boilerplate]
        out.append(len("\n".join(kept).strip()))
    return out


# A page is judged garbled when too little of it is letters or digits. PyMuPDF
# reads some fonts with broken encodings as a stream of punctuation and control
# characters; Allington et al. 2001 came out at 2-14% alphanumeric on every page,
# where poppler's pdftotext reads the same pages at 87-97%. Across the library's
# 32,000 text pages the split is clean: garbled pages sit below 0.15, real text
# above 0.7, and the handful between are tables and reference lists.
GARBLED_ALNUM_SHARE = 0.25
# Pages with less than this many non-space characters are too short to judge.
GARBLE_MIN_CHARS = 200


def alnum_share(text: str) -> float:
    """Fraction of non-whitespace characters that are letters or digits."""
    ns = [c for c in text if not c.isspace()]
    return sum(c.isalnum() for c in ns) / len(ns) if ns else 1.0


# A page whose letters are uniformly rotated reads as nonsense and passes
# every test built on character classes: it is all letters, properly spaced,
# the right length. `text/Morel2001Proliferation.md` carries
# "HOLXP-RXUQDORI7KHRUHWLFDO%LRORJ\" for "...Journal of Theoretical Biology"
# -- a broken embedded font encoding, extracted faithfully as gibberish, with
# `ocr: false` so nothing flagged it. The PDF RENDERS correctly, so no fresh
# download fixes it and only rasterising and reading the pixels does.
#
# The rotation requirement is what makes this safe for a corpus that is not
# all English: a German or French page scores low on these words too, but does
# not turn into English under any shift.
# Space-delimited words were the wrong test. The first version counted " the "
# with its spaces, which works on a CLEANED page and not on the raw pdftotext
# output this has to judge -- and the 2,809-extract sweep that "confirmed" it
# read `text/*.md`, which is the already-repaired text. A page-level detector
# validated against post-repair output cannot fail, which is the shape of
# nearly every silent success in this repo.
#
# Substrings instead, and the verdict is a RATIO against the unrotated text
# rather than an absolute rate, so it does not care about spacing at all.
COMMON_BITS = ("the", "and", "tion", "ing", "of", "that", "with", "for",
               "are", "was", "this", "ment", "ally")
SHIFT_MIN_CHARS = 600
SHIFT_RATIO = 4.0       # best rotation must beat the text as it stands
SHIFT_FLOOR = 12        # ... and clear this many hits outright
# Measured on the live library: a DNA page runs 0.696, and every prose page
# tried runs 0.37 -- including Morel's rotated one, since a rotation moves
# which letters are common without changing how concentrated they are.
SEQUENCE_TOP4 = 0.55


def _bits(s: str) -> int:
    low = s.lower()
    return sum(low.count(w) for w in COMMON_BITS)


def _rotate(s: str, k: int) -> str:
    return "".join(
        chr((ord(c) - 97 - k) % 26 + 97) if "a" <= c <= "z" else
        chr((ord(c) - 65 - k) % 26 + 65) if "A" <= c <= "Z" else c for c in s)


def shift_encoded(text: str) -> bool:
    """True when the page is ordinary prose with every letter rotated.

    `text/Morel2001Proliferation.md` carries "HOLXP-RXUQDORI7KHRUHWLFDO" for
    "...Journal of Theoretical" -- a broken embedded font encoding. The page is
    all letters, correctly spaced and the right length, so every test built on
    character classes passes it.

    The rotation requirement is what keeps a non-English corpus safe: a German
    or French page scores low on these fragments too, and does not improve
    under any shift.
    """
    if len(text) < SHIFT_MIN_CHARS:
        return False
    sample = text[:8000]
    # Sequence data is not prose and must never be rotated into "prose".
    # `Tournier2020Engineered-3` is a page of DNA -- GCCAACCCCTACGAGCGCGGC --
    # and a four-letter alphabet rotates into English fragments by chance.
    # English's six commonest letters carry about half its text; a nucleotide
    # or peptide run is carried almost entirely by a handful.
    letters = [c.lower() for c in sample if c.isalpha()]
    if letters:
        counts = sorted(Counter(letters).values(), reverse=True)
        # Top FOUR, not six: `Tournier2020Engineered-3` is a plasmid page whose
        # DNA carries 85% of its letters while prose carries the rest, so a
        # six-letter test at 90% let it through and a rotation of ACGT scored
        # 239 English fragments against the text's own 42. The test is
        # rotation-invariant by construction -- English's four commonest
        # letters carry about a third of it, shifted or not.
        if sum(counts[:4]) >= SEQUENCE_TOP4 * len(letters):
            return False
    base = _bits(sample)
    best = max(_bits(_rotate(sample, k)) for k in range(1, 26))
    return best >= SHIFT_FLOOR and best >= SHIFT_RATIO * max(base, 1)


def page_is_garbled(text: str) -> bool:
    """True for a substantial page whose characters are mostly not text.

    Counting characters cannot tell a text layer from a broken font encoding:
    ",   , , ?@ , :D;" is as long as a sentence. So a page long enough to judge
    is called garbled when its letters and digits fall under
    GARBLED_ALNUM_SHARE. isalnum() counts CJK and accented letters, so a
    Japanese or French paper is not mistaken for noise.
    """
    ns = sum(1 for c in text if not c.isspace())
    if ns >= GARBLE_MIN_CHARS and alnum_share(text) < GARBLED_ALNUM_SHARE:
        return True
    return shift_encoded(text)


def pdftotext_pages(data: bytes) -> list[str] | None:
    """Per-page text from poppler's pdftotext, or None when it is unavailable.

    Optional by design: the mirror runs on machines without poppler (Windows),
    and there a garbled page is simply dropped rather than repaired.
    """
    import shutil
    import subprocess
    exe = shutil.which("pdftotext")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "-enc", "UTF-8", "-", "-"], input=data,
                             capture_output=True, timeout=120, check=True).stdout
    except (subprocess.SubprocessError, OSError):
        return None
    pages = out.decode("utf-8", "replace").split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]  # pdftotext ends with a form feed
    return pages


def extract_pdf_text(data: bytes, fallback=pdftotext_pages
                     ) -> tuple[str, int, int, int, str]:
    """Return (markdown body, page count, character count, content characters, note).

    The body keeps every character of every readable page; only the *decision*
    about whether this is a text layer uses the boilerplate-stripped count.

    A page PyMuPDF garbles is re-read with ``fallback`` (pdftotext); if that
    cannot read it either, the page is left out of the body and out of the
    content count, exactly as an image-only page would be. A garbled page must
    never be written as text: it passes every length check and then answers
    every search with a confident zero. ``note`` says what happened, or is "".
    """
    import pymupdf  # imported lazily so --attachments none needs no PDF stack

    with pymupdf.open(stream=data, filetype="pdf") as doc:
        pages = doc.page_count
        raw_pages = [page.get_text("text") or "" for page in doc]

    garbled = [i for i, raw in enumerate(raw_pages) if page_is_garbled(raw)]
    repaired = dropped = 0
    if garbled:
        alt = fallback(data) if fallback else None
        for i in garbled:
            if alt is not None and i < len(alt) and alt[i].strip() \
                    and not page_is_garbled(alt[i]):
                raw_pages[i] = alt[i]
                repaired += 1
            else:
                raw_pages[i] = ""
                dropped += 1

    chunks, chars = [], 0
    for n, raw in enumerate(raw_pages, 1):
        text = clean_page_text(raw)
        chars += len(text.strip())
        if text.strip():
            chunks.append(f"<!-- p. {n} -->\n\n{text.strip()}")

    notes = []
    if repaired:
        notes.append(f"{repaired} garbled page(s) re-read with pdftotext")
    if dropped:
        notes.append(f"{dropped} garbled page(s) dropped")
    # The page fraction travels with the rest because it needs the RAW pages,
    # which only exist in here: boilerplate has to be found while the lines are
    # still lines, and the caller only ever sees the joined body.
    return ("\n\n".join(chunks), pages, chars, content_chars(raw_pages),
            page_text_fraction(raw_pages), "; ".join(notes))


def require_ocr_stack() -> None:
    """Fail once, at the start, if `--ocr` cannot possibly work.

    rapidocr is deliberately NOT in any script's PEP 723 header: it is a large
    optional stack and `--ocr` is an occasional pass, so it is passed in with
    `uv run --with rapidocr-onnxruntime`. The cost of that choice is that
    forgetting it is easy, and on 2026-10-03 the result was **116 separate
    per-attachment failures** inside the handler.

    A missing dependency is a property of the RUN and not of an attachment,
    which is the lesson already written beside the pymupdf check in
    `zotero_source.refresh` -- and not applied here, in the same file, two days
    later.
    """
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except ImportError:
        sys.exit("--ocr needs rapidocr, which is not installed. It is kept out "
                 "of the dependency header on purpose, so pass it in:\n"
                 "  uv run --with rapidocr-onnxruntime --script "
                 "zotero_source.py --refresh --ocr")


def ocr_pdf_text(data: bytes, dpi: int = 300) -> tuple[str, int]:
    """Read a scanned PDF by rasterising each page and running OCR on it.

    For the ~100 attachments in this library that are page images, this is the
    difference between a paper that can be found and one that cannot. It is not
    a substitute for the publisher's text: OCR guesses characters, and it guesses
    worst at exactly what gets asked of this library -- numerals, subscripts,
    superscripts, Greek. Measured once against a known passage (Lin & Rye 2006,
    abstract, checked word for word against Europe PMC) it was perfect on prose,
    which says nothing about a table of rate constants.

    So everything written from this path is marked, in the front matter and in
    the body, and the marking is the point. An unmarked OCR extract is
    indistinguishable later from the words off the page, and the whole value of
    text/ is that it is not a paraphrase.
    """
    import pymupdf
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:  # a clear message beats a traceback in a refresh
        raise RuntimeError(
            "OCR needs rapidocr-onnxruntime; run with "
            "'uv run --with rapidocr-onnxruntime --script mirror.py --ocr'"
        ) from exc

    reader = RapidOCR()
    chunks, chars = [], 0
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        for n, page in enumerate(doc, 1):
            png = page.get_pixmap(dpi=dpi).tobytes("png")
            result, _ = reader(png)
            text = clean_page_text("\n".join(line[1] for line in (result or [])))
            chars += len(text.strip())
            if text.strip():
                chunks.append(f"<!-- p. {n} -->\n\n{text.strip()}")
    return "\n\n".join(chunks), chars


# Control characters that survive extraction from some PDFs and carry no
# information in a text extract. A single NUL is enough to make GNU grep treat
# the whole file as binary: it then reports NO MATCH, silently, with exit 1 --
# not "Binary file matches", and no warning. `grep -rl text/` is the command the
# library's own CLAUDE.md prescribes for subject search, so 33 papers were
# answering a confident zero while sitting in library.bib and index.md, marked as
# having extracted text. Tab and newline are kept; everything else in C0 goes.
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]")


def strip_control(text: str) -> str:
    """Normalize line endings, then remove control characters.

    Carriage returns are NORMALIZED rather than deleted, because they are line
    endings and deleting one joins two lines into a word-collision. The first
    version of this stripped C0 "keeping tab and newline" and quietly kept CR
    too, which is neither: 820k of them survived, embedded in the text rather
    than at the ends of lines, in files whose other lines end in a bare \n. An
    anchored search then fails on them -- `grep 'reactor$'` does not match a line
    ending "reactor\r" -- which is the same silent-zero family as the NUL, just
    narrower.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return CONTROL_CHARS.sub("", text)


def clean_page_text(text: str) -> str:
    text = strip_control(text)
    # join words broken across a line by hyphenation: "cataly-\nsis" -> "catalysis"
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    # a single newline inside a paragraph is a column-wrap artifact, not a break
    text = re.sub(r"(?<![.:;!?])\n(?![\n•\-\d])", " ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def text_document(doc: dict, key: str, body: str, pages: int, chars: int,
                  ocr: bool = False, attachment: str = "") -> str:
    ids = doc.get("identifiers") or {}
    authors = "; ".join(
        f"{(p.get('last_name') or '').strip()}, {(p.get('first_name') or '').strip()}".strip(", ")
        for p in (doc.get("authors") or [])
    )

    def esc(v):
        return json.dumps(str(v), ensure_ascii=False) if v not in (None, "") else '""'

    front = [
        "---",
        f"citekey: {key}",
        f"title: {esc(doc.get('title'))}",
        f"authors: {esc(authors)}",
        f"year: {doc.get('year', '')}",
        f"source: {esc(doc.get('source'))}",
        f"doi: {esc(ids.get('doi'))}",
        f"pages: {pages}",
        f"characters: {chars}",
        f"ocr: {'true' if ocr else 'false'}",
        # Which ATTACHMENT produced this, not just which record. Without it an
        # extract cannot say whether it belongs under the stem it sits at, and
        # on 2026-10-03 two of them sat under the wrong one with nothing in the
        # file to show it.
        f"attachment: {attachment or ''}",
        f"record_id: {doc.get('id', '')}",
        "---",
        "",
        f"# {doc.get('title') or key}",
        "",
    ]
    if ocr:
        front += [
            "> **Read by OCR, not extracted.** This attachment is page images, so "
            "every character here was guessed by a machine. Prose comes out well; "
            "numerals, subscripts, superscripts and Greek do not, and those are "
            f"usually what is being asked for. **Verify any quotation, and every "
            f"number, against the rendered page — `get_pdf.py {key}` — before it "
            "goes anywhere.**",
            "",
        ]
    front += [
        f"*Cite as `{key}`; page markers below are the PDF's own "
        "pagination. Equations and table structure do not survive extraction — "
        "check the PDF itself before quoting either.*",
        "",
    ]
    return "\n".join(front) + body + "\n"


def write_extraction_report(rows: list, out: Path, extracted: int) -> None:
    empty = [r for r in rows if r["status"] == "no-text"]
    failed = [r for r in rows if r["status"] == "failed"]
    ocred = [r for r in rows if r["status"] == "ocr"]
    garbled = [r for r in rows if r["status"] == "garbled"]
    ctrl = [r for r in rows if r["status"] == "control-chars"]
    notpdf = [r for r in rows if r["status"] == "not-pdf"]
    lines = [
        "# Extraction report",
        "",
        f"- extracted this run: {extracted}",
        f"- no text layer (probably scans): {len(empty)}",
        f"- read by OCR: {len(ocred)}",
        f"- garbled text layer, pages repaired or dropped: {len(garbled)}",
        f"- control characters stripped: {len(ctrl)}",
        f"- not a PDF, nothing to extract: {len(notpdf)}",
        f"- failed outright: {len(failed)}",
        "",
        "Anything under 'No extractable text' is invisible to any text search of",
        "this folder. The attachment exists; its text does not.",
        "",
        "Anything under 'Not a PDF' was examined and discarded: the bytes carry no",
        "PDF header, so there was nothing to extract. Some of these are correct --",
        "a figure, a structure file, a video attached to a paper. Any of them that",
        "is the record's ONLY attachment is a paper with no extract, and the line",
        "says what the bytes actually looked like so the two can be told apart.",
        "",
        "Anything under 'Read by OCR' is searchable but was never typeset as text:",
        "a machine guessed every character. Those extracts carry ocr: true and a",
        "banner. Verify quotations and all numbers against the rendered page.",
        "",
        "Anything under 'Control characters stripped' held bytes a PDF had no",
        "business putting in text. They were removed; the entry is here because",
        "the PDF produced them and another one probably will.",
        "",
        "That heading used to say these files would answer every search with a",
        "silent zero. **Measured 2026-10-02, that is not true of this library.**",
        "GNU grep treats a file as binary on a NUL or an encoding error, and of",
        "2,740 extracts 1,087 carry C0 control characters and NOT ONE carries a",
        "NUL -- `grep -rl` finds them. The mechanism is real and the test for it",
        "uses a NUL; the claim that it applied to these files was never checked",
        "against them. Stripping them is a text-quality fix, not a findability",
        "one.",
        "",
        "Anything under 'Garbled text layer' had pages the PDF library read as",
        "punctuation soup. Those pages were re-read with pdftotext, or left out of",
        "the extract where that failed too; a missing page marker means the latter.",
        "",
    ]
    for label, rows_ in (("No extractable text", empty), ("Read by OCR", ocred),
                         ("Garbled text layer", garbled),
                         ("Control characters stripped", ctrl),
                         ("Not a PDF", notpdf), ("Failed", failed)):
        if not rows_:
            continue
        lines += [f"## {label}", ""]
        for r in sorted(rows_, key=lambda r: r["key"]):
            detail = f" — {r['detail']}" if r.get("detail") else ""
            lines.append(f"- `{r['key']}` · {r.get('title', '')[:80]}{detail}")
        lines.append("")
    (out / "extraction-report.md").write_text("\n".join(lines), encoding="utf-8")




def harvest_attachments(fetch, files_by_doc: dict, keymap: dict,
                        docs_by_id: dict, out: Path, state: dict, mode: str,
                        backend: str, state_path: Path | None = None,
                        ocr: bool = False, backfill: bool = False,
                        reassess: bool = False) -> tuple:
    """Download each attachment, extract its text, and (by default) discard it.

    `fetch(file_id) -> bytes` is the whole backend interface: it is the only
    part of this function that knows which service it is talking to, and it is
    required rather than defaulted, so a caller cannot reach a backend it did
    not name.

    `backfill` is for getting the PDFs themselves onto disk, which a plain
    `--attachments keep` does NOT do on a library that is already mirrored. The
    skip below fires for every attachment whose extract exists and whose
    filehash is unchanged, and it `continue`s before any download -- so a keep
    run over a finished mirror downloads nothing and archives nothing. Verified
    against the stub: 0 downloads, 0 files written.

    That is correct for a refresh and wrong for an evacuation. With `backfill`,
    a skipped attachment still has its file fetched if it is not already on
    disk, and nothing else about it changes: no extraction, no state write, no
    report row. Resumable by construction, since a file already present is not
    fetched again.
    """
    text_dir = out / "text"
    text_dir.mkdir(parents=True, exist_ok=True)
    pdf_dir = out / "pdf"
    if mode == "keep":
        pdf_dir.mkdir(parents=True, exist_ok=True)
    known, migrated = qualify_map(state.get("files", {}))
    state["files"] = known
    if migrated:
        # Not cosmetic: an unqualified file id from a second backend would collide
        # here and skip an extraction as "already done".
        note(f"  namespaced {migrated} attachment ids as {LEGACY_BACKEND}:<id>")
    kept = stale_rules = 0
    qual = lambda raw: qualify(raw, backend)
    fetched = skipped = failed = reused = unresolved = archived = 0
    withheld = 0
    removed = load_removed(out)
    unarchived: list = []
    report: list = []
    state_path = state_path or mirror_state_dir(out) / "state.json"

    total = sum(len(v) for v in files_by_doc.values())
    seen = 0
    try:
        for doc_id, files in files_by_doc.items():
            # keymap is keyed by NAMESPACED id and persists between runs; the maps
            # built from this run's API response are keyed by bare id and do not.
            # This is the one place the two meet, and getting it wrong here skips
            # every attachment without raising anything.
            key = keymap.get(qual(doc_id))
            doc = docs_by_id.get(doc_id)
            if not key or not doc:
                unresolved += 1
                continue
            for i, f in enumerate(files):
                seen += 1
                stem = key if i == 0 else f"{key}-{i+1}"
                is_pdf = "pdf" in (f.get("mime_type") or "").lower()
                text_target = text_dir / f"{stem}.md"
                prior = known.get(qual(f["id"]), {})
                # What counts as DONE is the stored status, not the presence of
                # a text file. Those came apart on 2026-10-02: 2,732 attachments
                # were recorded `failed` (pymupdf missing), and because
                # `text/<key>.md` still existed from the previous backend and the
                # filehash matched, the next run read every one as "unchanged"
                # and skipped it. A failed extraction became indistinguishable
                # from a finished one, and the re-extraction silently did
                # nothing -- reported, by luck, only because two other
                # attachments failed outright and tripped the status.
                #
                # A `failed` entry is unfinished work, not a result. An unknown
                # or missing status is not a result either, so both fall through
                # and are re-examined; the cost of re-extracting is one local
                # file read, and the cost of skipping wrongly is a paper with no
                # extract that nothing will ever look at again.
                prior_status = prior.get("status")
                done_before = prior_status in DONE_STATUSES
                if ocr and prior_status == "no-text":
                    # **A status is the verdict of the reader that produced
                    # it.** `no-text` means the plain reader found nothing, and
                    # `--ocr` is a different and stronger reader whose entire
                    # purpose is to re-read exactly those attachments. Treating
                    # the weaker reader's verdict as final made the flag a
                    # no-op: on 2026-10-02 an --ocr recovery run reported
                    # "0 extracted, 2730 unchanged" and never reached one of
                    # the 110 scans it existed to recover.
                    #
                    # `not-pdf` is deliberately NOT here. OCR does not help a
                    # video file, and retrying those every run would be motion.
                    done_before = False
                has_artifact = text_target.exists() or prior_status in ("no-text", "not-pdf")
                # **The verdict also belongs to the STEM it was written under.**
                # An attachment can keep its bytes and change its name: on
                # 2026-10-03 the refresh ordered two records' attachments
                # opposite to `.mirror/pairings.tsv`, so `Hirota2000Effect` and
                # `Hirota2000Effect-2` swapped papers. Re-running with the
                # ordering fixed would have repaired nothing, because the
                # filehash still matched and a file still existed at the new
                # target -- holding the other paper's text. Recording the stem
                # is what lets a rename read as unfinished work.
                # **No recorded stem means the stem cannot be vouched for**, and
                # that is unfinished work, not a pass. 0.25.0 let a missing
                # `stem` skip, on the reasoning that nothing should re-extract
                # for want of a field that did not exist -- and that branch is
                # exactly what stopped the repair it shipped for. The entries
                # for `Hirota2000Effect` were written by 0.24.0, which recorded
                # no stem, so the fix could not see that the wrong attachment
                # had written the file.
                #
                # The cheap alternatives do not work. A record's *current*
                # attachment count says nothing about which attachment wrote an
                # existing file: after the parked duplicate was deleted,
                # `Hirota2000Effect` had exactly one attachment and the extract
                # on disk was still the other one's.
                #
                # So this costs one full re-extraction, once, and every entry
                # written afterwards carries its stem.
                if prior.get("stem", "") != stem:
                    done_before = False
                if reassess and prior and prior.get("rules", 1) < EXTRACT_RULES:
                    done_before = False
                if (done_before and has_artifact
                        and prior.get("filehash") == f.get("filehash")):
                    skipped += 1
                    # Counted HERE, where the attachment is actually left alone,
                    # and not at the decision above. There it counted two things
                    # it did not mean: an attachment with no prior entry at all,
                    # because `prior.get("rules", 1)` returns the default for a
                    # record that was never produced under any rules; and one
                    # about to be re-extracted for some other reason. On
                    # 2026-10-04 that printed "22 extracts were produced under
                    # older extraction rules" the morning after a full
                    # `--reassess`, and the 22 were exactly the 22 attachments
                    # filed that day -- extracted fresh, under current rules, in
                    # the same run that called them old.
                    #
                    # The number now means what its sentence says: extracts left
                    # on disk from older rules that this run did not re-read.
                    if prior.get("rules", 1) < EXTRACT_RULES:
                        stale_rules += 1
                    if prior.get("status") in ("no-text", "failed", "garbled",
                                                "not-pdf"):
                        # `not-pdf` joined this list on 2026-10-01. Without it a
                        # skipped attachment is reported on the run that skipped
                        # it and never again -- which is exactly how 108 OCR rows
                        # emptied out of the report on the next refresh.
                        report.append({"key": stem, "status": prior["status"],
                                       "title": doc.get("title", ""),
                                       "detail": prior.get("detail", "")})
                    elif prior.get("status") == "ocr":
                        # Skipped because its extract exists, but it is still
                        # machine-read text, and the report is where that is
                        # said. Leaving it out emptied "Read by OCR" for all 108
                        # papers on the first refresh after the OCR run.
                        report.append({"key": stem, "status": "ocr",
                                       "title": doc.get("title", ""),
                                       "detail": f"{prior.get('chars', 0)} characters "
                                                 f"across {prior.get('pages', 0)} pages"})
                    if backfill and mode == "keep":
                        if stem in removed:
                            # Absent from pdf/ because somebody took it out, not
                            # because the evacuation has not reached it yet.
                            withheld += 1
                        elif find_archived(pdf_dir, stem, f) is None:
                            try:
                                content = fetch(f["id"])
                                archive = pdf_dir / (
                                    stem + archive_suffix(
                                        f, looks_like_pdf(content)))
                                archive.write_bytes(content)
                                archived += 1
                                progress(f"  {seen}/{total}  archiving {stem[:36]}")
                                time.sleep(0.2)
                            except Exception as exc:
                                # An attachment that cannot be archived is worth
                                # naming: this run is the evacuation, and a file
                                # missed here is a file left behind.
                                note(f"  ! could not archive {stem}: "
                                     f"{type(exc).__name__}: {exc}")
                                unarchived.append(stem)
                    continue
                progress(f"  {seen}/{total}  {stem[:44]}")
                try:
                    # An earlier run may already have pulled this PDF down. Re-use
                    # it rather than paying for the download twice.
                    local = find_archived(pdf_dir, stem, f)
                    from_disk = local is not None
                    if from_disk:
                        data = local.read_bytes()
                        reused += 1
                    else:
                        data = fetch(f["id"])

                    sniffed = looks_like_pdf(data)
                    if mode == "keep" and not from_disk:
                        if stem in removed:
                            withheld += 1
                        else:
                            (pdf_dir / (stem + archive_suffix(f, sniffed))).write_bytes(data)

                    if not sniffed:
                        # The MIME type is a hint; the bytes are the verdict.
                        # And a skip that says nothing is the defect, not the
                        # skip: six attachments were judged here and the
                        # judgement was written nowhere, so the one file whose
                        # job is to explain a missing extract did not mention
                        # them. A pass that does no work does not get to report
                        # success, and discarding an attachment is work.
                        detail = (f"{f.get('mime_type') or 'unknown type'}, "
                                  f"{len(data)} bytes beginning {data[:8]!r}; "
                                  "no PDF header, so there is nothing to extract")
                        report.append({"key": stem, "status": "not-pdf",
                                       "title": doc.get("title", ""),
                                       "detail": detail})
                        known[qual(f["id"])] = {"filehash": f.get("filehash"),
                                                   "status": "not-pdf",
                                                   "stem": stem, "rules": EXTRACT_RULES,
                                                   "detail": detail}
                        continue

                    body, pages, chars, content, page_frac, garble = extract_pdf_text(data)
                    from_ocr = False
                    # Thin by volume, OR text on too few of its own pages. The
                    # second catches a scan whose cover page carries the whole
                    # document's text, and -- since the fraction is measured on
                    # content -- one whose every image page carries the same
                    # stamp.
                    thin = (content < MIN_CHARS_PER_PAGE * max(pages, 1)
                            or page_frac <= MIN_PAGE_TEXT_FRACTION)
                    if thin and ocr:
                        progress(f"  {seen}/{total}  {stem[:36]} (ocr)")
                        ocr_body, ocr_chars = ocr_pdf_text(data)
                        if ocr_chars >= MIN_CHARS_PER_PAGE * max(pages, 1):
                            body, chars, content, from_ocr = (
                                ocr_body, ocr_chars, ocr_chars, True)
                    # Only VOLUME decides no-text. A document whose few pages of
                    # text are real text is not a scan, and calling it one would
                    # delete a usable extract.
                    if content < MIN_CHARS_PER_PAGE * max(pages, 1):
                        status = "no-text"
                        detail = f"{chars} characters across {pages} pages"
                        if content < chars:
                            detail += f", {content} once page-repeated lines are dropped"
                        if ocr:
                            detail += "; OCR could not read it either"
                        # NEVER delete an extract that has content because THIS
                        # run produced none. On 2026-10-02 a re-extract without
                        # --ocr read 0 characters from 108 image-only scans and
                        # unlinked all 108 OCR'd extracts -- work from a
                        # deliberate pass, gone, with nothing warning. Same
                        # family as the 0.11.0 archive erosion: a run destroying
                        # an artifact it did not create, on the assumption that
                        # its own result is the authoritative one.
                        #
                        # The state cannot help here, which is the part worth
                        # knowing: under a new backend the prior entry is keyed
                        # by a different id and knows nothing of the OCR pass.
                        # The extract itself is the only durable evidence, which
                        # is exactly why it carries `ocr: true`.
                        existing = ""
                        if text_target.exists():
                            existing = text_target.read_text(encoding="utf-8",
                                                             errors="replace")
                        if content_of_extract(existing) > content:
                            kept += 1
                            was_ocr = "ocr: true" in existing[:400]
                            status = "ocr" if was_ocr else "ok"
                            detail = (f"kept the existing extract, which has "
                                      f"{len(existing)} characters; this run read "
                                      f"{chars}" + ("" if ocr else " and was not given --ocr"))
                            if was_ocr:
                                report.append({"key": stem, "status": "ocr",
                                               "title": doc.get("title", ""),
                                               "detail": detail})
                        else:
                            report.append({"key": stem, "status": status,
                                           "title": doc.get("title", ""), "detail": detail})
                            text_target.unlink(missing_ok=True)
                    else:
                        status, detail = ("ocr", "read by OCR") if from_ocr else ("ok", "")
                        if garble and not from_ocr:
                            status, detail = "garbled", garble
                            report.append({"key": stem, "status": "garbled",
                                           "title": doc.get("title", ""), "detail": garble})
                        if from_ocr:
                            report.append({"key": stem, "status": "ocr",
                                           "title": doc.get("title", ""),
                                           "detail": f"{chars} characters across {pages} pages"})
                        document = text_document(doc, stem, body, pages, chars,
                                                 ocr=from_ocr,
                                                 attachment=local_id(qual(f["id"]),
                                                                     backend)
                                                 or f.get("id", ""))
                        # Belt and braces. clean_page_text has already stripped
                        # these per page; if any reach here, something built text
                        # by another route and the extract would be invisible to
                        # grep. Strip them and SAY so, rather than writing a file
                        # that answers every search with a confident zero.
                        safe = strip_control(document)
                        if safe != document:
                            removed = len(document) - len(safe)
                            note(f"  ! {stem}: removed {removed} control "
                                 "characters that would have hidden it from grep")
                            report.append({"key": stem, "status": "control-chars",
                                           "title": doc.get("title", ""),
                                           "detail": f"{removed} control characters "
                                                     "stripped after extraction"})
                        text_target.write_text(safe, encoding="utf-8")
                        fetched += 1
                    known[qual(f["id"])] = {"filehash": f.get("filehash"), "status": status,
                                      "stem": stem, "rules": EXTRACT_RULES,
                                      "detail": detail,
                                      "pages": pages, "chars": chars}
                    stub = pdf_dir / f"{stem}.pdf"
                    if mode == "text" and not from_disk and stub.exists():
                        # `not from_disk` is the whole fix. `text` mode writes
                        # nothing to pdf_dir -- extraction runs on bytes in
                        # memory -- so anything of substance found here was put
                        # there by a `keep` or `--backfill` run and is somebody's
                        # archive, not this run's scratch copy. What remains
                        # reachable is a zero-byte stub from a failed earlier
                        # download, which is worth clearing.
                        #
                        # The comment that used to sit here read "the text is the
                        # artifact; the backend keeps the PDF". True when written.
                        # The account it says that about expires, and `<out>/pdf/`
                        # is the answer to that -- so the line quietly ate one
                        # archived PDF for every attachment that changed.
                        stub.unlink()
                    if seen % 25 == 0:
                        save_json(state_path, state)  # so Ctrl-C keeps the progress
                    if not from_disk:
                        time.sleep(0.2)
                except Exception as exc:  # one bad file shouldn't stop the run
                    failed += 1
                    # SAY WHY. This handler discarded `exc` until 2026-10-02,
                    # and when a missing dependency made every extraction raise,
                    # the run reported "2734 failed" with no reason for any of
                    # them. A swallowed exception turns a one-line diagnosis
                    # into an investigation, and the first few are enough to
                    # recognize a systematic cause.
                    if failed <= 5:
                        note(f"  ! {stem}: {type(exc).__name__}: {exc}")
                    elif failed == 6:
                        note("  ! further failures not individually logged; "
                             "the first five above are the pattern")
                    known[qual(f["id"])] = {"filehash": f.get("filehash"), "status": "failed",
                                            "stem": stem,
                                      "detail": str(exc)[:200]}
                    report.append({"key": stem, "status": "failed",
                                   "title": doc.get("title", ""), "detail": str(exc)[:120]})
    finally:
        save_json(state_path, state)
    if mode == "text" and pdf_dir.exists() and not any(pdf_dir.iterdir()):
        pdf_dir.rmdir()
    if reused:
        note(f"  re-used {reused} PDFs already on disk (no re-download)")
    # These two are about EXTRACTION and belong to every run. They were nested
    # under the archive branch below, so `kept` -- the notice added in 0.19.0 to
    # say that an OCR'd extract had been preserved -- could only ever print
    # during a `--backfill`. It has never appeared on an ordinary refresh, which
    # is the same silence it was written to end.
    if stale_rules:
        note(f"  {stale_rules} extracts were produced under older extraction "
             f"rules (now {EXTRACT_RULES}). They are NOT re-examined by "
             "default; pass --reassess to re-read them under the current rules.")
    if kept:
        note(f"  {kept} existing extracts were KEPT: this run read nothing "
             "from those attachments and an earlier pass had read something. "
             "Re-run with --ocr to refresh them.")
    if archived or unarchived:
        note(f"  archived {archived} attachment files that were already extracted")
        if withheld:
            word = "attachment" if withheld == 1 else "attachments"
            note(f"  {withheld} {word} not archived: .mirror/removed.tsv lists "
                 "them as deliberately deleted")
        if unarchived:
            note(f"  ! {len(unarchived)} could NOT be archived: "
                 + ", ".join(unarchived[:10])
                 + (" ..." if len(unarchived) > 10 else ""))
    if unresolved:
        note(f"  ! {unresolved} documents had attachments but no citation key, "
             "and were skipped")
    # The invariant, and the reason it is here. On 2026-09-24 a namespacing change
    # made every lookup on the line above miss, so every document was skipped by a
    # guard written for the rare case of one unkeyed record. The run reported
    # "0 extracted, 0 unchanged, 0 failed" against "up to 2733 attachments" and
    # still wrote **ok** to the status file. Counting to zero is not an outcome
    # this pass is allowed to report quietly: a new attachment would have gone
    # unextracted with nothing to say so.
    if total and not seen:
        raise RuntimeError(
            f"{total} attachments were listed but not one was examined: every "
            f"document id failed to resolve to a citation key ({unresolved} "
            f"unresolved). The mirror is not extracting anything -- this is a "
            f"bug in the tool, not a problem with the account."
        )
    return fetched, skipped, failed, report


# --------------------------------------------------------------------------
# run bookkeeping: one run at a time, a log, and a visible staleness signal
# --------------------------------------------------------------------------

LOCK_STALE_HOURS = 12


def host_name() -> str:
    """Stable per-machine name, safe to put in a filename."""
    raw = (os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME")
           or socket.gethostname() or "unknown")
    return re.sub(r"[^A-Za-z0-9._-]", "_", raw.split(".")[0]) or "unknown"


def _pid_alive(pid: object) -> bool:
    """True if we can tell the process is running, and when we cannot tell.

    POSIX only. On Windows os.kill() with any signal other than CTRL_C_EVENT /
    CTRL_BREAK_EVENT calls TerminateProcess -- signal 0 would kill the very run
    we are asking about -- so there we say "cannot tell" and fall back to age.
    """
    if os.name != "posix" or not isinstance(pid, int):
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not ours to signal
    except OSError:
        return True
    return True


def acquire_lock(mirror_dir: Path) -> Path | None:
    """Refuse to start if another run on THIS machine is live.

    The lock is per-host -- ``run.lock.<host>`` -- because the mirror directory
    is a synced folder and a single shared ``run.lock`` is not a mutex there.
    Two ways it failed: one writer's delete loses to another peer's surviving
    copy, so a dead run's lock gets resurrected indefinitely; and two machines
    starting inside the propagation window never see each other's lock anyway.
    One writer per filename makes the delete stick.

    Our own host's lock is authoritative and checkable: if the process is gone
    the lock is stale whatever its age, and if it is alive we back off however
    old it is -- the first full run legitimately takes hours. Another host's
    lock is advisory: we say so and continue, since we can neither verify its
    process nor trust the sync to have converged.
    """
    lock = mirror_dir / f"run.lock.{host_name()}"
    if lock.exists():
        held = load_json(lock, {})
        age_h = (time.time() - lock.stat().st_mtime) / 3600
        if _pid_alive(held.get("pid")):
            if os.name == "posix" or age_h < LOCK_STALE_HOURS:
                note(f"Another run started {age_h:.1f} h ago on this machine "
                     f"(pid {held.get('pid', '?')}); stopping.")
                return None
            note(f"Ignoring a stale lock left {age_h:.0f} h ago.")
        else:
            note(f"Clearing a lock from pid {held.get('pid', '?')}, "
                 f"left {age_h:.1f} h ago; that process is gone.")

    for other in sorted(mirror_dir.glob("run.lock.*")):
        if other == lock:
            continue
        held = load_json(other, {})
        age_h = (time.time() - other.stat().st_mtime) / 3600
        note(f"Note: {held.get('host', other.suffix.lstrip('.'))} has a lock "
             f"{age_h:.1f} h old (pid {held.get('pid', '?')}). Advisory only -- "
             f"continuing. If that run is live, expect sync conflicts.")

    legacy = mirror_dir / "run.lock"
    if legacy.exists():
        age_h = (time.time() - legacy.stat().st_mtime) / 3600
        note(f"Removing a shared run.lock from an older version "
             f"({age_h:.0f} h old); locks are per-host now.")
        legacy.unlink(missing_ok=True)

    save_json(lock, {"pid": os.getpid(), "host": host_name(),
                     "started": datetime.now(timezone.utc).isoformat()})
    return lock


def write_log(mirror_dir: Path) -> None:
    log = mirror_dir / "mirror.log"
    try:
        if log.exists() and log.stat().st_size > 1_000_000:
            log.replace(mirror_dir / "mirror.log.1")
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"\n===== {stamp} =====\n")
            fh.write("\n".join(LOG) + "\n")
    except OSError:
        pass


# A rejected login and a dropped connection both end the run, and until now both
# printed the same thing. They mean opposite things: one clears by itself, the
# other never does and is the signal to migrate.
# Phrases that mean the credentials, not the connection. Several of these were
# an OAuth vocabulary until 2026-11-13 -- "refresh token rejected", "state
# mismatch", "timed out waiting for the browser redirect" -- and they went with
# the flow that produced them. A marker for a message nothing can emit is not
# harmless: it is a classifier arm that can only ever be wrong about something
# else, and it makes the list look better tested than it is.
AUTH_MARKERS = (
    "refused the key",
    "no zotero credentials",
    "needs both api_key and user_id",
    "invalid key",
    "403",
    "401",
    # An empty library from an account that is not empty means the credentials
    # are no longer seeing the account. The key still works; what it reaches is
    # gone. That is the shape a lapsed or downgraded account takes, so it routes
    # to the same advice as a rejected key rather than to "unknown".
    "no documents returned",
)
NETWORK_MARKERS = (
    "could not reach",
    "connectionerror",
    "connecttimeout",
    "readtimeout",
    "max retries",
    "name or service not known",
    "temporary failure in name resolution",
    "connection reset",
    "connection refused",
)


def classify_failure(detail: str) -> str:
    """'auth', 'network', 'interrupted' or 'other', from the message a run records.

    Order matters. "Could not reach https://api.zotero.org/keys/..." is a
    NETWORK failure that happens to name the token endpoint, and reading it as an
    authentication problem would send a person to re-authorize over a connection
    that is not there. So the network markers are tested first, and they are
    phrases about reaching a host rather than words like "token".
    """
    low = detail.lower()
    if "keyboardinterrupt" in low:
        return "interrupted"
    if any(m in low for m in NETWORK_MARKERS):
        return "network"
    if any(m in low for m in AUTH_MARKERS):
        return "auth"
    return "other"


KIND_NAMES = {
    "auth": "authentication",
    "network": "network",
    "interrupted": "interrupted",
    "other": "unknown",
}


def record_health(mirror_dir: Path, ok: bool, kind: str = "", now: str = "") -> dict:
    """Update and return the run-health record in .mirror/health.json.

    Kept out of state.json deliberately: that file's shape is a contract other
    people's tooling reads, and a counter that changes on every failed run has no
    business in it. This one is additive and nothing else depends on it.
    """
    now = now or datetime.now(timezone.utc).isoformat()
    path = mirror_dir / "health.json"
    h = load_json(path, {})
    if ok:
        h = {"consecutive_failures": 0, "last_success": now, "last_attempt": now}
    else:
        n = int(h.get("consecutive_failures", 0)) + 1
        h = {
            "consecutive_failures": n,
            # the first failure of THIS streak, so a long outage reads as one
            "failing_since": h.get("failing_since") or now,
            "last_kind": kind,
            "last_attempt": now,
            "last_success": h.get("last_success", ""),
        }
    save_json(path, h)
    return h


def retired_marker(out: Path) -> Path:
    return mirror_state_dir(out) / "retired.json"


def load_retired(out: Path) -> dict:
    """Has this mirror been declared frozen? `{}` means no."""
    return load_json(retired_marker(out), {}) or {}


def write_retired_status(out: Path, info: dict) -> None:
    """Replace mirror-status.md with a banner a frozen mirror keeps saying.

    This exists because the obvious fix does not fire. `write_status` runs only
    during a refresh, so when refreshes stop, the status file freezes too --
    still reading `**ok**` and "Everything in this folder is current as of the
    run above", for as long as anyone cares to read it. Both sentences were true
    on 2026-09-30 and neither is true now, and the library's own CLAUDE.md
    tells every session to check this file before concluding a paper is absent.

    That is the same failure as the silent `not-pdf` skip and the counter that
    said 123: a status line that cannot report the one state that matters. The
    difference is that here the line is not missing, it is confidently wrong,
    and nothing will ever rewrite it unless something writes it once on purpose.

    What stays true is said as plainly as what does not: the extracts, the
    bibliography and the annotations are a valid snapshot of the library AS OF
    the freeze. It is only currency that is gone.
    """
    when = info.get("retired_at", "")[:16].replace("T", " ")
    reason = info.get("reason") or "the backend was retired"
    lines = [
        "# Mirror status — FROZEN",
        "",
        f"- **this mirror stopped being updated on {when} UTC**",
        f"- reason: {reason}",
        f"- last successful refresh: {info.get('last_ok', 'unknown')}",
        f"- written by offprint {__version__}",
        "",
        "**Do not read this folder as current.** Nothing refreshes it. A paper",
        "added to the library after the date above does **not** appear here, and",
        "no amount of re-reading this file will say otherwise -- it no longer",
        "changes.",
        "",
        "What is still true: `library.bib`, `index.md`, `text/`, `annotations/`",
        "and `pdf/` are a valid snapshot of the library as it stood at that",
        "moment. Quoting an extract, citing a key, and reading a page marker are",
        "all as sound as they were. It is only *currency* that is gone.",
        "",
        "So: **absence here is no longer evidence of absence.** Before concluding",
        "the library does not have a paper, check the live library rather than",
        "this folder.",
        "",
    ]
    if info.get("successor"):
        lines += [f"The live library is now {info['successor']}.", ""]
    (out / "mirror-status.md").write_text("\n".join(lines), encoding="utf-8")


def failure_advice(kind: str, streak: int) -> list[str]:
    """What the reader should do about it, which is different for each kind."""
    if kind == "auth":
        return [
            "**This is an authentication failure, not a network problem.** The stored",
            "API key was not accepted. Nothing here will update until a person puts a",
            "working key in `~/.config/offprint/zotero.json` and runs",
            "`zotero_source.py --refresh` by hand.",
            "",
            "If a freshly issued key also fails, API access itself is gone. That is the",
            "signal to move the library, not something to wait out.",
        ]
    if kind == "network":
        out = ["This looks like a network failure rather than a rejected login, so it",
               "may well clear by itself."]
        if streak >= 3:
            out += ["", f"It has not cleared in {streak} consecutive attempts, though, which is",
                    "long enough to stop assuming it will."]
        return out
    if kind == "interrupted":
        return ["The run was interrupted rather than failing on its own. Nothing is",
                "wrong with the account or the connection; the refresh simply did not",
                "finish."]
    return ["The cause is not one this tool recognizes. The message above is the",
            "whole of what it knows; `.mirror/mirror.log` has the run around it."]


def write_status(out: Path, ok: bool, started: datetime, error: str = "",
                 kind: str = "", health: dict | None = None) -> None:
    """Rewritten on every attempt, success or failure.

    A failed run leaves index.md untouched, which would otherwise make a mirror
    that stopped updating three weeks ago look perfectly current.
    """
    path = out / "mirror-status.md"
    prev = {}
    if path.exists():
        m = re.search(r"last successful run: (\S+ \S+ \S+)", path.read_text(encoding="utf-8"))
        if m:
            prev["last_ok"] = m.group(1)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    last_ok = now if ok else prev.get("last_ok", "never")
    took = (datetime.now(timezone.utc) - started).total_seconds()
    label = "ok" if ok else f"FAILED: {KIND_NAMES.get(kind, 'unknown')}"
    lines = [
        "# Mirror status",
        "",
        f"- last attempt: {now} — **{label}** ({took/60:.1f} min)",
        f"- last successful run: {last_ok}",
    ]
    streak = int((health or {}).get("consecutive_failures", 0))
    if not ok and streak:
        since = (health or {}).get("failing_since", "")
        # "failed once" and "has not worked in nine days" must not read alike
        plural = "" if streak == 1 else "s"
        line = f"- **{streak} consecutive failure{plural}**"
        if since:
            line += f", the first at {since[:16].replace('T', ' ')} UTC"
        lines.append(line)
    lines += [f"- written by offprint {__version__}", ""]
    if not ok:
        lines += [
            "The last refresh did not finish, so everything else in this folder is",
            "as of the last successful run above — treat it as possibly stale, and",
            "say so if it matters to the answer.",
            "",
        ] + failure_advice(kind, streak) + [
            "",
            "```",
            error.strip()[:800],
            "```",
            "",
            "Full history in `.mirror/mirror.log`.",
        ]
    else:
        lines += ["Everything in this folder is current as of the run above.",
                  "", "Recent run:", "", "```"] + LOG[-14:] + ["```"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------





