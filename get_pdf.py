#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31", "pymupdf>=1.24"]   # pymupdf only to page-count a cached PDF
# ///
"""
get_pdf.py -- get one paper's actual PDF, on demand.

The mirror keeps extracted text, not PDFs. That is the right default: text is
searchable and quotable. But extraction drops figures, structures and table
layout, so sometimes the real document is the thing you need -- to look at a
deposited structure, read a figure, or check a table.

**Three sources, in this order, and the first needs nothing.**

1. The local cache, from an earlier fetch.
2. **`<out>/pdf/`, the archive**, which since the evacuation holds every
   attachment in the library. No network, no credentials, no account. This is
   where almost every answer now comes from, and the file is served **in place**
   -- it belongs to the mirror and this script does not copy, move or quarantine
   it.
3. Zotero, for anything the archive does not have yet. A paper filed today is
   the case: `zotero_inbox.py` uploads to Zotero and the archive catches up on
   the next refresh, so between the two it exists only in Zotero.

The Mendeley path is gone. It was retired with the account on 2026-10-02, and a
fallback that always fails is worse than none -- it turns "this paper is not in
the archive" into an authentication error.

    uv run --script get_pdf.py Muller2020Yield
    uv run --script get_pdf.py --search "packed bed"      # find the key first
    uv run --script get_pdf.py Bird2021Transport --open   # and open it

The PDF lands in a local cache OUTSIDE the mirror folder, so fetching one does
not push it out to every machine the mirror syncs to. The path is printed on
stdout; hand it to whatever wants to read it.
"""

from __future__ import annotations

import argparse
import os
import difflib
import math
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from mendeley_mirror import DEFAULT_OUT, load_json, mirror_state_dir
    from zotero_migrate import Zotero, load_zotero_credentials
except ImportError:
    sys.exit("get_pdf.py must sit in the same folder as mendeley_mirror.py")

import requests


def cache_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    d = base / "mendeley-mirror" / "pdf"
    d.mkdir(parents=True, exist_ok=True)
    return d


def archive_index(pdf_dir: Path) -> dict:
    """`stem -> path` from ONE listing of `<out>/pdf/`.

    A stem is `<citekey>` or `<citekey>-N`, and whatever suffix the attachment
    was archived under -- `.pdf` for 2,739 of them and `.cif`, `.pdb`, `.avi`
    or one mangled `.-_charmm_g` for the rest. Built by listing rather than by
    probing candidate names, because here the question is "what is there", and a
    single pass answers it for every key at once.
    """
    idx: dict = {}
    if not pdf_dir.is_dir():
        return idx
    for path in pdf_dir.iterdir():
        if not path.is_file() or not path.stat().st_size:
            continue
        stem = path.name[:len(path.name) - len(path.suffix)] if path.suffix else path.name
        idx.setdefault(stem, path)
    return idx


def zotero_files(z: Zotero) -> list:
    """Every attachment in the library, shaped like Mendeley's `/files` rows.

    One bulk call, which is what `attachment_inventory` was built around: asking
    per document is the difference between a sweep somebody repeats and a sweep
    nobody runs twice. `itemType=attachment` is the direct analogue of `/files`,
    and `parentItem` of `document_id`.
    """
    rows = []
    for a in z.paged("items", quiet=True, itemType="attachment"):
        d = a.get("data") or {}
        if d.get("linkMode") not in ("imported_file", "imported_url"):
            continue
        rows.append({"id": a.get("key"), "document_id": d.get("parentItem"),
                     "mime_type": d.get("contentType") or "",
                     "file_name": d.get("filename") or ""})
    return rows


def zotero_pdfs_for(z: Zotero, item_key: str) -> list:
    """The storage attachments on one record, as `/files`-shaped rows."""
    rows = []
    for c in z.paged(f"items/{item_key}/children", quiet=True):
        d = c.get("data") or {}
        if d.get("linkMode") in ("imported_file", "imported_url"):
            rows.append({"id": c.get("key"), "mime_type": d.get("contentType") or "",
                         "file_name": d.get("filename") or ""})
    return rows


def search_index(out: Path, needle: str) -> list[tuple[str, str]]:
    """Grep the mirror's index for a title, author, or DOI fragment."""
    index = out / "index.md"
    if not index.exists():
        sys.exit(f"No index at {index} -- run mendeley_mirror.py first.")
    hits = []
    for line in index.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `"):
            continue
        if needle.lower() in line.lower():
            cells = [c.strip() for c in line.strip("|").split("|")]
            hits.append((cells[0].strip("`"), " · ".join(cells[1:4])))
    return hits


def split_attachment(key: str) -> tuple[str, int]:
    """'Abrams2012Fly-2' -> ('Abrams2012Fly', 2); a plain key -> (key, 1).

    The mirror writes the first attachment as text/<key>.md and every later one
    as text/<key>-N.md, so those names are already in front of anyone reading the
    library -- but this script only ever resolved the bare key, and rejected
    `<key>-2` as unknown. That mattered more than a missing convenience:
    Brenner1990Empirical's FIRST attachment is a one-page errata sheet carrying
    someone else's erratum, and the 14-page paper is the second. Following the
    extract's own instruction to check a quotation against the rendered page
    served a different paper's document, and a plausible-looking one.
    """
    m = re.fullmatch(r"(.+)-(\d+)", key)
    if m and int(m.group(2)) >= 2:
        return m.group(1), int(m.group(2))
    return key, 1


def choose_attachment(pdfs: list, out: Path, key: str, nth: int) -> tuple[dict | None, str]:
    """Which of a document's PDFs is the one `key` names. Returns (file, why).

    Order is the fallback, not the rule. The refresh numbers attachments from a
    bulk /files listing while this script asks /files?document_id=, and nothing
    promises the two agree; serving the wrong one is precisely the failure this
    is here to fix. So when the mirror's extract records a page count, prefer the
    attachment that MATCHES it, and fall back to position only when that cannot
    decide.
    """
    if not pdfs:
        return None, "no PDF attached"
    if nth > len(pdfs):
        return None, f"has only {len(pdfs)} PDF attachment(s), so there is no -{nth}"
    # The listing carries no page count, so position is all there is to go on
    # here. It is CHECKED after the download, against the extract's own markers,
    # which is the only point at which the two can actually be compared.
    return pdfs[nth - 1], f"attachment {nth} of {len(pdfs)}"


NEAR = 0.98      # two extracts this alike are one paper, not two

# Placeholder pagination, as a proof or galley carries it before the page numbers
# are assigned: "xxx-xxx", "XXXX, XXX, 000-000". Built from the forms actually
# present in this library rather than guessed -- 14 extracts carry one.
PROOF_PAGES = re.compile(r"(?:[xX]{3,}\s*[-\u2013\u2014]\s*[xX]{3,})"
                         r"|(?:\b0{3}\s*[-\u2013\u2014]\s*0{3}\b)"
                         r"|(?:XXXX,\s*XXX)")


def is_proof(body: str) -> int:
    """How many placeholder page ranges an extract carries; 0 for a published one.

    The difference that decides what may be CITED. Abrams2012Fly's two extracts
    are 98.34% alike and differ, essentially, in three places: xxx -> 547,
    xxx-xxx -> 114-119, xxxx -> 10 August 2012. One is the accepted proof and the
    other the version of record. Telling a reader they are near-duplicates and
    stopping there invites them to cite the copy that has no page numbers.
    """
    return len(PROOF_PAGES.findall(body))


def extract_body(path: Path) -> str:
    """An extract's text from the first page marker on, without its front matter.

    The front matter names the key, so <key>-2.md and <key>.md always differ as
    bytes even when they came from the same PDF. Comparing bodies is what tells a
    genuine second document from the same file attached twice.
    """
    t = path.read_text(encoding="utf-8", errors="replace")
    i = t.find("<!-- p. 1 -->")
    return t[i:] if i >= 0 else t


def local_extracts(out: Path, by_key: dict) -> dict:
    """{citekey: [1, 2, ...]} -- which attachment numbers the mirror has written."""
    found: dict[str, list] = {}
    for path in sorted((out / "text").glob("*.md")):
        stem = path.stem
        if stem in by_key:                 # a key that merely ends in -N is a key
            found.setdefault(stem, []).append(1)
            continue
        base, n = split_attachment(stem)
        if base in by_key:
            found.setdefault(base, []).append(n)
    return {k: sorted(set(v)) for k, v in found.items()}


def attachment_inventory(files: list, out: Path, by_key: dict, keys: list) -> int:
    """Report attachments held per key against extracts written, downloading none.

    One bulk /files call answers this for the whole library. The alternative --
    asking per document, or fetching to find out -- is 72 requests to learn
    something the account will tell you in one, which is the difference between a
    sweep somebody repeats and a sweep nobody runs twice.

    Two things it finds, and they are different problems:

    * ORPHAN: the mirror wrote text/<key>-N.md but the library no longer reports
      that many attachments. The extract may be the ONLY remaining copy of that
      document, so it must not be treated as regenerable.
    * DUPLICATE: two extracts of one record with the same body. The same file
      attached twice; nothing is lost by ignoring it, and a search hits the paper
      twice. NEAR-DUPLICATE is the same thing where the two differ slightly, as
      two scans of one article do; it is a prompt to look, not a verdict.
    * NO BASE: the first attachment produced no extract at all, so text/<key>.md
      does not exist and only <key>-2 onward do. Such a record appears in
      index.md and library.bib but in no extraction report.
    """
    extracts = local_extracts(out, by_key)
    if keys:
        extracts = {k: v for k, v in extracts.items() if k in keys}
    doc_to_key = {v: k for k, v in by_key.items()}

    counts: dict[str, int] = {}
    for f in files:
        key = doc_to_key.get(f.get("document_id"))
        if key and "pdf" in (f.get("mime_type") or "").lower():
            counts[key] = counts.get(key, 0) + 1

    interesting = sorted(k for k, v in extracts.items() if len(v) > 1 or keys)
    orphans = dupes = near = proofs = irreplaceable = 0
    print(f"{'key':<34}{'extracts':>9}{'attachments':>13}  note")
    for key in interesting:
        have, want = counts.get(key, 0), max(extracts[key])
        notes = []
        orphaned = [n for n in extracts[key] if n > have]
        if orphaned:
            notes.append(f"ORPHAN: -{want} has no attachment in the library")
            orphans += 1
        # Compare every PAIR of extracts this record actually has, not each one
        # against the base. Shan2011How has no base -- its first attachment was
        # not a PDF -- so a base-anchored comparison examined nothing and its two
        # extracts of the same paper went unflagged.
        present = [(n, out / "text" / (f"{key}.md" if n == 1 else f"{key}-{n}.md"))
                   for n in extracts[key]]
        present = [(n, f) for n, f in present if f.exists()]
        if 1 not in [n for n, _ in present]:
            notes.append("NO BASE: the first attachment produced no extract")
        bodies = {n: extract_body(f) for n, f in present}
        # Pagination is checked for EVERY pair, not only near-duplicate ones.
        # Keying it off similarity was backwards: a proof and its published
        # version have different page breaks, so the markers move and the pair
        # drifts apart -- Caparco2018Effect's two extracts are 4% alike, and its
        # base is the proof. The closer the pair, the likelier a similarity test
        # catches it, which is exactly the wrong way round.
        for i, a in enumerate(sorted(bodies)):
            for b in sorted(bodies)[i + 1:]:
                pa, pb = is_proof(bodies[a]), is_proof(bodies[b])
                if (pa > 0) != (pb > 0):
                    which = "the base" if a == 1 else f"-{a}"
                    proof = which if pa else f"-{b}"
                    real = f"-{b}" if pa else which
                    notes.append(f"PAGINATION: {proof} is an unpaginated proof, "
                                 f"{real} is not -- cite {real}")
                    proofs += 1
        for i, a in enumerate(sorted(bodies)):
            for b in sorted(bodies)[i + 1:]:
                if bodies[a] == bodies[b]:
                    notes.append(f"DUPLICATE: -{b} body equals -{a}"
                                 if a != 1 else f"DUPLICATE: -{b} body equals the base")
                    dupes += 1
                else:
                    # Exact equality is not enough. Shan2011How's two extracts are
                    # the same paper at 0.9935 -- two scans of one article differ
                    # in a handful of characters, and a strict test calls them
                    # distinct. This is a prompt to look, not a verdict.
                    r = difflib.SequenceMatcher(None, bodies[a], bodies[b])
                    if r.real_quick_ratio() >= NEAR and r.quick_ratio() >= NEAR \
                            and r.ratio() >= NEAR:
                        # Floor rather than round: 0.999919 printed as "100.00%"
                        # beside an exact DUPLICATE row reads as a contradiction,
                        # and two decimals of a ratio that close is false
                        # precision anyway.
                        pct = math.floor(r.ratio() * 10000) / 100
                        which = "the base" if a == 1 else f"-{a}"
                        notes.append(f"NEAR-DUPLICATE: -{b} is {pct:.2f}% the same "
                                     f"as {which} -- check before citing both")
                        near += 1
        # The worst combination, and the reason it gets its own line. Shan2011How's
        # -3 is BOTH the orphan and the only copy with real page numbers: the
        # extract that cannot be re-fetched is the published version, and the one
        # the library still holds is the proof. Deleting the orphan would lose no
        # CONTENT -- the pair is 99.3% alike -- only the pagination, which is the
        # single thing that differs and the only reason anyone wants it.
        #
        # Which extract is orphaned is inferred from the numbering, since the
        # listing does not say. It is the best available reading and it is stated
        # as one.
        if orphaned and bodies:
            gone_citable = [n for n in orphaned if n in bodies and not is_proof(bodies[n])]
            kept_proofs = [n for n in bodies if n not in orphaned and is_proof(bodies[n])]
            if gone_citable and kept_proofs:
                notes.append(
                    f"IRREPLACEABLE: -{gone_citable[0]} appears to be both the "
                    f"orphan and the only copy with real page numbers; what "
                    f"the library still holds is the proof. Do not delete it")
                irreplaceable += 1
        print(f"{key:<34}{len(extracts[key]):>9}{have:>13}  {'; '.join(notes)}")
    print(f"\n{len(interesting)} keys examined, {orphans} orphaned, {dupes} duplicated, "
          f"{near} near-duplicate, {proofs} proof/published pairs.")
    if irreplaceable:
        print(f"{irreplaceable} of those orphans appear to be the ONLY citable copy "
              "of their paper.")

    # Pairing only decides the cases that HAVE a pair. A proof filed on its own
    # reads as an ordinary paper, and its front matter invites a reader to cite
    # page markers that are not the journal's pages. This needs no network, so
    # it costs nothing to say here.
    alone = []
    for path in sorted((out / "text").glob("*.md")):
        n = is_proof(extract_body(path))
        if n:
            alone.append((path.stem, n))
    if alone:
        print(f"\nExtracts carrying placeholder pagination ({len(alone)}). Their page"
              "\nmarkers are the proof's, not the journal's, and are not citable:")
        for stem, n in alone:
            print(f"  {stem:<34}{n:>4} placeholder(s)")
    if orphans:
        print("An ORPHANED extract cannot be re-fetched and may be the only copy "
              "left of that document. Do not delete it to force a re-extraction.")
    return 1 if orphans else 0


def extract_pages(out: Path, key: str) -> int:
    """How many pages the mirror's own extract says this paper has, or 0.

    The extract is written from the SAME attachment get_pdf serves, so its
    `<!-- p. N -->` markers are a local, offline record of the page count. That
    makes it the cheapest possible check on a cache hit, and it needs no network
    and no credentials -- which matters, because serving a cached PDF without
    authenticating is a deliberate property of this script.
    """
    extract = out / "text" / f"{key}.md"
    if not extract.exists():
        return 0
    return len(re.findall(r"<!-- p\. \d+ -->", extract.read_text(encoding="utf-8",
                                                                errors="replace")))


def cache_is_the_right_paper(target: Path, out: Path, key: str) -> bool:
    """Is the cached file plausibly the paper asked for, or something else?

    A cache hit used to be served on the strength of existing and being non-empty.
    When inbox.py cached a second attachment over the first, `get_pdf.py <key>`
    returned the Supporting Information under the article's name: right key, right
    file name, wrong document, no warning. Nobody re-checks a cache hit, so it
    would have stood until a human noticed the page count -- which is exactly the
    check being made here.

    Unknowable cases pass. No extract, no markers, no pymupdf: this cannot tell,
    and refusing to serve a file because it could not be verified would break the
    offline path for every scan in the library.
    """
    want = extract_pages(out, key)
    if not want:
        return True
    try:
        import pymupdf
        with pymupdf.open(target) as doc:
            have = doc.page_count
    except Exception:
        return True
    return have == want


def doi_filed(out: Path, dest: Path, key: str) -> Path | None:
    """A PDF that inbox.py filed before this reference had a citation key.

    inbox.py names what it caches after the citation key, but a paper that was
    new to the library has no key until the next refresh, so it gets cached
    under its DOI instead. Pick that file up and rename it rather than
    downloading a second copy of something already on disk.
    """
    index = out / "index.md"
    if not index.exists():
        return None
    for line in index.read_text(encoding="utf-8").splitlines():
        if not line.startswith(f"| `{key}`"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        doi = cells[5] if len(cells) > 5 else ""
        if not doi:
            return None
        cand = dest / (re.sub(r"[^A-Za-z0-9]", "_", doi.lower()) + ".pdf")
        return cand if cand.exists() and cand.stat().st_size > 0 else None
    return None


def open_locally(path: Path) -> None:
    try:
        if os.name == "nt":
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception as exc:
        print(f"(could not open a viewer: {exc})")


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch one paper's PDF: archive first, then Zotero.")
    ap.add_argument("keys", nargs="*",
                    help="citation keys, e.g. Muller2020Yield. A record with more "
                         "than one attachment: Muller2020Yield-2 is the second, "
                         "matching text/<key>-2.md in the mirror")
    ap.add_argument("--search", metavar="TEXT", help="find citation keys by title/author/DOI")
    ap.add_argument("--attachments", action="store_true",
                    help="report attachments held per key against extracts written, "
                         "downloading nothing. With no keys, sweeps every record "
                         "that has more than one extract")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="mirror directory")
    ap.add_argument("--dest", type=Path, default=None,
                    help=f"where to put the PDFs (default: {cache_dir()})")
    ap.add_argument("--open", action="store_true", dest="open_after",
                    help="open each PDF in the system viewer")
    args = ap.parse_args()

    out = args.out.expanduser()
    if args.search:
        hits = search_index(out, args.search)
        if not hits:
            print(f"nothing matching {args.search!r}")
            return 1
        for key, rest in hits[:40]:
            print(f"{key}\t{rest}")
        if len(hits) > 40:
            print(f"... and {len(hits) - 40} more")
        return 0

    if not args.keys and not args.attachments:
        ap.error("give at least one citation key, or --search TEXT, or --attachments")

    keymap = load_json(mirror_state_dir(out) / "citekeys.json", {})
    if not keymap:
        sys.exit("No citation-key map found -- run mendeley_mirror.py first.")
    # Stored ids are namespaced, and only the live backend's may be sent to the
    # live backend: another service's id would 404 at best, and at worst match a
    # different paper.
    by_key = {key: ident.split(":", 1)[1] for ident, key in keymap.items()
              if ident.startswith("zotero:")}
    if not by_key:
        sys.exit("citekeys.json holds no zotero: entries -- run zotero_migrate.py.")

    if args.attachments:
        z = Zotero(*load_zotero_credentials())
        return attachment_inventory(zotero_files(z), out, by_key, args.keys)

    dest = (args.dest or cache_dir()).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    archived = archive_index(out / "pdf")

    # Resolve keys and serve anything already local BEFORE touching the network,
    # so a typo, a cache hit or an archived paper never needs an account at all.
    status, wanted = 0, []
    for key in args.keys:
        base, nth = split_attachment(key)
        doc_id = by_key.get(base)
        if not doc_id:
            near = [k for k in by_key if k.lower().startswith(base.lower()[:8])][:5]
            print(f"! unknown citation key {key!r}" + (f" -- did you mean {near}?" if near else ""))
            status = 1
            continue
        target = dest / f"{key}.pdf"
        if not target.exists():
            stray = doi_filed(out, dest, key)
            if stray:
                stray.rename(target)
                print(f"(adopted {stray.name}, filed before {key} had a key)", file=sys.stderr)
        if target.exists() and target.stat().st_size > 0:
            if cache_is_the_right_paper(target, out, key):
                print(target)
                if args.open_after:
                    open_locally(target)
                continue
            quarantine = target.with_name(f"{target.stem}-unexpected{target.suffix}")
            target.rename(quarantine)
            print(f"! {key}: the cached PDF has the wrong number of pages for this "
                  f"paper -- moved to {quarantine.name} and re-fetching", file=sys.stderr)

        # The archive, before the network. It holds every attachment in the
        # library and needs no account. Served IN PLACE: it belongs to the
        # mirror, so this never copies, renames or quarantines it -- the
        # cache-hygiene branch above is about files this script put there.
        in_archive = archived.get(key)
        if in_archive:
            print(in_archive)
            if args.open_after:
                open_locally(in_archive)
            continue
        wanted.append((key, doc_id, target, nth))

    if not wanted:
        return status

    z = Zotero(*load_zotero_credentials())

    for key, doc_id, target, nth in wanted:
        pdfs = [f for f in zotero_pdfs_for(z, doc_id)
                if "pdf" in (f.get("mime_type") or "").lower()]
        if not pdfs:
            print(f"! {key}: not in <out>/pdf/ and Zotero has no PDF attachment "
                  "on this record")
            status = 1
            continue
        chosen, why = choose_attachment(pdfs, out, key, nth)
        if chosen is None:
            print(f"! {key}: {why}")
            status = 1
            continue
        if len(pdfs) > 1 and nth == 1:
            # Say it, rather than let a reader assume one attachment exists. The
            # first is not always the paper.
            others = (f"{key}-2" if len(pdfs) == 2
                      else f"{key}-2 .. {key}-{len(pdfs)}")
            print(f"({key}: {len(pdfs)} PDF attachments; serving the first. "
                  f"The others are {others})", file=sys.stderr)

        resp = z.session.get(f"{z.base}/items/{chosen['id']}/file", timeout=180)
        if resp.status_code == 404:
            print(f"! {key}: Zotero has the attachment record but no file behind "
                  "it -- a byte-less stub. zotero_delete.py --stubs lists these.")
            status = 1
            continue
        if not resp.ok:
            print(f"! {key}: download failed ({resp.status_code})")
            status = 1
            continue
        target.write_bytes(resp.content)
        # The same check a cache hit gets. A fresh download deserves it more: if
        # the attachment order here differs from the order the refresh numbered
        # them in, this is the only thing standing between a reader and the wrong
        # document under the right name.
        if not cache_is_the_right_paper(target, out, key):
            want = extract_pages(out, key)
            print(f"! {key}: the downloaded PDF does not have the {want} pages "
                  f"{key} is recorded as having. Served anyway, but do not quote "
                  f"from it until you have checked which document it is.",
                  file=sys.stderr)
            status = 1
        print(target)
        if args.open_after:
            open_locally(target)

    return status


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
