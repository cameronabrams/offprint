#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pymupdf>=1.24", "requests>=2.31"]
# ///
"""
zotero_inbox.py -- file a PDF you downloaded into the Zotero record it belongs to.

`inbox.py` does this for Mendeley and is retired with it. The identification
half of that script is backend-agnostic and is reused here unchanged: reading a
DOI out of the filename, the PDF metadata and the first page, resolving it
through Crossref, checking the result actually matches the page in front of you,
and finding the record already in `library.bib`. What this adds is the Zotero
half -- **creating an attachment** and putting bytes in it.

    uv run --script zotero_inbox.py                       # dry run over <out>/inbox/
    uv run --script zotero_inbox.py --yes
    uv run --script zotero_inbox.py --key Abrams2013Enhanced paper.pdf --yes
    uv run --script zotero_inbox.py --item FABXX33S paper.pdf --yes

Then ONE short refresh rather than two full ones; the script prints the exact
command, naming the records it filed:

    uv run --script zotero_source.py --refresh --only Abrams2013Enhanced

**Since 0.29.0 it creates the record too, when no record holds the paper.**
That reverses a line this repo drew deliberately, so the reason is worth
stating. The line said "acquiring a reference and acquiring its PDF are two
operations with different risks"; the risk it was guarding was a SECOND record
for a paper the library already had. But the check that guards it read
`library.bib`, which only knows what the last refresh saw -- so the line cost
Cameron two full refreshes per paper (one to mint the citation key, one to
extract the text, ~13 minutes each) and did not actually close the hole: a
record pushed an hour ago was equally invisible.

`zotero_doi_index` closes it properly by asking **Zotero** before creating, the
way `inbox.py` always did for Mendeley. With that in place the separation was
buying nothing, and `--no-create` keeps the old refusal for anyone who wants it.

Four refusals, each because the alternative is a library that is wrong rather
than a library that is missing something:

- **It never creates a record it cannot identify by DOI.** A title-only match
  is enough to ATTACH to a record a human already made; it is not enough to
  mint one, because the metadata would be guesswork and a wrong record is
  worse than a missing one. Metadata comes back through `from_doi`, so
  `csl_year()` still decides the year.
- **It refuses a file whose bytes are already attached.** The md5s of the
  record's existing attachments are checked first, so re-running after a partial
  failure, or filing the same download twice, attaches nothing.
- **It refuses a PDF with no readable text**, through `inbox.py`'s own
  `has_content`. A scan filed silently becomes a paper that cannot be quoted and
  looks complete.
- **It refuses to guess which record.** Identification has to produce a DOI or
  title that matches something in `library.bib`; otherwise `--key` names the
  record explicitly. A wrong guess files one paper's PDF under another's
  citation key, which is silent and very hard to find later.

A note on the archive: this uploads to Zotero and does **not** write to
`<out>/pdf/`, because the stem an attachment will be archived under depends on
its position in the record's attachment list and is not known until the next
refresh reads it back. The refresh fetches the bytes from Zotero on its first
pass. `<out>/pdf/` therefore lags by one refresh for a newly filed paper, which
is stated here because the archive being complete is the whole reason it exists.
"""

from __future__ import annotations

import argparse
import hashlib
import secrets
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mendeley_mirror import (DEFAULT_OUT, __version__, load_json,  # noqa: E402
                             looks_like_pdf, mirror_state_dir)
from inbox import existing_document, has_content, identify, title_of  # noqa: E402
from mendeley_push import from_doi  # noqa: E402
from zotero_attach import ZoteroWriter, storage_attachments  # noqa: E402
from zotero_migrate import Zotero, load_zotero_credentials, norm_doi  # noqa: E402
from zotero_push import (TO_ZOTERO_TYPE, create_item, doc_to_zotero,  # noqa: E402
                         item_template)


def die(msg: str):
    sys.exit(f"error: {msg}")


def zotero_key_for(out: Path, citekey: str) -> str:
    """The Zotero item key a citation key resolves to, or ''.

    Through `citekeys.json`, like every other write in this repo, so a key the
    mirror has never seen is an error rather than a silent no-op.
    """
    keymap = load_json(mirror_state_dir(out) / "citekeys.json", {})
    for ident, key in keymap.items():
        if key == citekey and ident.startswith("zotero:"):
            return ident.split(":", 1)[1]
    return ""


def already_attached(children: list, data: bytes) -> str:
    """The key of an existing attachment holding exactly these bytes, or ''.

    Checked before anything is created. Filing the same download twice, or
    re-running after a partial failure, otherwise leaves a record carrying the
    same paper under two attachments -- which nothing downstream would flag,
    because two attachments on one record is ordinary.
    """
    md5 = hashlib.md5(data).hexdigest()
    for c in storage_attachments(children):
        if ((c.get("data") or {}).get("md5") or "").lower() == md5:
            return c.get("key") or ""
    return ""


def zotero_doi_index(z: Zotero) -> dict:
    """Every DOI in Zotero right now, normalized, mapped to its item key.

    `library.bib` only knows what the last refresh saw, and the whole point of
    this change is to file a paper without waiting for a refresh -- so the
    record this script creates today is invisible to the check that would stop
    it creating a second one tomorrow. `inbox.py` had exactly this function for
    Mendeley and exactly this reasoning; it was never the Mendeley-specific
    half.

    It is also what makes a record pushed by `zotero_push.py` since the last
    refresh findable at all: it has no citation key yet, so `library.bib`
    cannot answer for it.
    """
    index = {}
    for item in z.items_top():
        data = item.get("data") or {}
        doi = norm_doi(data.get("DOI") or "")
        if doi:
            index.setdefault(doi, item.get("key") or "")
    return index


def create_record(z: Zotero, doi: str) -> tuple[str, list]:
    """Create the top-level record for `doi`. Returns `(item key, dropped)`.

    **The metadata comes back through `from_doi`, not off the Crossref message
    this script already holds.** That is deliberate and not a wasted request:
    `from_doi` is where `csl_year()` lives, and `csl_year` is the difference
    between an Advance Access paper's issue year and its online year. CHARMM36m
    went into the library as 2016 and was cited that way in a manuscript
    because one path read a date-part itself. A second creator of records that
    did its own conversion would be a second chance at the same bug.
    """
    doc = from_doi(doi)
    item_type = TO_ZOTERO_TYPE.get(doc.get("type", "generic"), "document")
    item, dropped = doc_to_zotero(doc, item_template(item_type))
    return create_item(z, item), dropped


def create_attachment(z: Zotero, parent_key: str, filename: str,
                      content_type: str) -> str:
    """POST a child attachment and return its key.

    The only item-creating call in the repo. `Zotero-Write-Token` makes it
    idempotent against a retry that the network lost rather than the server
    refused -- without it, a dropped response means a duplicate attachment on
    the next attempt and no way to tell.
    """
    body = [{"itemType": "attachment", "parentItem": parent_key,
             "linkMode": "imported_file", "title": "Full Text PDF",
             "filename": filename, "contentType": content_type}]
    resp = z.session.post(
        f"{z.base}/items", json=body,
        headers={"Zotero-Write-Token": secrets.token_hex(16),
                 "Content-Type": "application/json"},
        timeout=60)
    if resp.status_code == 403:
        die("Zotero refused the key (403). Creating an attachment needs WRITE "
            "access; check it at https://www.zotero.org/settings/keys")
    resp.raise_for_status()
    payload = resp.json()
    failed = payload.get("failed") or {}
    if failed:
        die(f"Zotero refused the attachment: {failed}")
    saved = (payload.get("successful") or {}).get("0") or {}
    key = saved.get("key") or (saved.get("data") or {}).get("key")
    if not key:
        die(f"Zotero accepted the attachment but returned no key: {payload}")
    return key


def plan_one(out: Path, path: Path, forced_key: str, forced_item: str,
             doi_index: dict | None, may_create: bool) -> tuple[dict, str]:
    """What to do with one file. An empty plan means do not file it.

    The plan is one of `{"citekey": ...}` (a record the mirror knows),
    `{"item": ...}` (a Zotero record the mirror has not seen yet) or
    `{"create": doi}`. Three outcomes rather than two because the middle one is
    the case this script used to get wrong: a record pushed since the last
    refresh has no citation key, is absent from `library.bib`, and would have
    been reported as "not in the library" -- inviting a second record for a
    paper Zotero already held.
    """
    data = path.read_bytes()
    if not looks_like_pdf(data):
        return {}, "not a PDF by its bytes"
    if not has_content(path):
        return {}, ("no readable text -- a scan filed silently becomes a paper "
                    "that cannot be quoted and looks complete")
    if forced_item:
        return {"item": forced_item}, "named with --item"
    if forced_key:
        return {"citekey": forced_key}, "named with --key"
    msg, doi, how = identify(path)
    if not msg:
        return {}, f"could not identify it ({how})"
    # The first-page title check inside `identify` has already run; nothing
    # below relaxes it, and nothing below runs if it refused.
    label = f"{how}: {doi or title_of(msg)[:60]}"
    _mid, citekey = existing_document(out, doi, title_of(msg))
    if citekey:
        return {"citekey": citekey}, label
    hit = (doi_index or {}).get(norm_doi(doi)) if doi else ""
    if hit:
        return {"item": hit}, label + " -- in Zotero but not yet in library.bib"
    if not doi:
        return {}, (f"identified as {title_of(msg)[:60]!r} by title alone and no "
                    "record holds it; a record cannot be created without a DOI")
    if not may_create:
        return {}, (f"identified as {doi} but no record holds it, and --no-create "
                    "was given")
    return {"create": doi}, label + " -- NEW record"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="File a downloaded PDF into the Zotero record it belongs to.")
    ap.add_argument("files", nargs="*", type=Path,
                    help="PDFs to file; default is everything in <out>/inbox/")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="mirror directory")
    ap.add_argument("--key", help="file ONE named PDF under this citation key, "
                                  "skipping identification")
    ap.add_argument("--item", help="file ONE named PDF onto this Zotero item key, "
                                   "skipping identification -- for a record with "
                                   "no citation key yet")
    ap.add_argument("--no-create", action="store_true",
                    help="refuse a paper no record holds, instead of creating "
                         "the record for it")
    ap.add_argument("--yes", action="store_true",
                    help="actually create and upload; without it this is a dry run")
    ap.add_argument("--version", action="version", version=f"offprint {__version__}")
    args = ap.parse_args()

    if args.key and args.item:
        ap.error("--key and --item both name the record; give one")
    if (args.key or args.item) and len(args.files) != 1:
        ap.error("--key/--item names one record, so give exactly one file with it")
    files = args.files or sorted(p for p in (args.out / "inbox").glob("*")
                                 if p.is_file() and not p.name.startswith("."))
    if not files:
        print(f"Nothing in {args.out / 'inbox'}.")
        return 0

    api_key, user_id = load_zotero_credentials()
    z = Zotero(api_key, user_id)
    writer = ZoteroWriter(z)

    print(f"offprint {__version__} zotero_inbox: "
          f"{'filing' if args.yes else 'DRY RUN -- nothing is created or uploaded'}")
    print(f"  {len(files)} file(s)\n")

    # Asked once, before the loop, and only when identification might need it.
    doi_index = {}
    if not (args.key or args.item):
        doi_index = zotero_doi_index(z)

    filed = skipped = created = 0
    targets: list = []
    for path in files:
        plan, why = plan_one(args.out, path, args.key or "", args.item or "",
                             doi_index, not args.no_create)
        if not plan:
            print(f"  ! {path.name}: {why}")
            skipped += 1
            continue

        citekey = plan.get("citekey", "")
        item_key = plan.get("item", "")
        if citekey:
            item_key = zotero_key_for(args.out, citekey)
            if not item_key:
                print(f"  ! {path.name}: {citekey} has no zotero: entry in "
                      "citekeys.json")
                skipped += 1
                continue

        if plan.get("create"):
            # Nothing exists to check the bytes against, so the duplicate test
            # below is skipped for this branch alone -- a record created in this
            # same loop iteration cannot already hold the file.
            print(f"  {'+' if args.yes else 'would add'} {path.name} -> NEW record "
                  f"for {plan['create']}  [{why}]")
            if not args.yes:
                filed += 1
                created += 1
                targets.append("<the new item key>")
                continue
            item_key, dropped = create_record(z, plan["create"])
            created += 1
            print(f"    created record {item_key}")
            for line in dropped:
                print(f"    ! nowhere to put {line}")
        else:
            data = path.read_bytes()
            children = z.paged(f"items/{item_key}/children", quiet=True)
            dup = already_attached(children, data)
            if dup:
                label = citekey or item_key
                print(f"  = {path.name}: already attached to {label} as {dup}")
                skipped += 1
                continue
            n_existing = len(storage_attachments(children))
            print(f"  {'+' if args.yes else 'would add'} {path.name} -> "
                  f"{citekey or item_key} ({item_key}), attachment "
                  f"{n_existing + 1}  [{why}]")
            if not args.yes:
                filed += 1
                targets.append(citekey or item_key)
                continue

        data = path.read_bytes()
        # A record with no citation key yet has nothing to name the file after,
        # so it keeps the name the human downloaded it under. The next refresh
        # archives it under the stem, and zotero_attach.py can rename it later;
        # inventing a key here would be a second authority for the one thing
        # that must have exactly one.
        fname = f"{citekey}.pdf" if citekey else path.name
        att = create_attachment(z, item_key, fname, "application/pdf")
        auth = writer.authorize(att, fname, data, int(path.stat().st_mtime * 1000))
        if not auth.get("exists"):
            writer.put_bytes(auth, data)
            writer.register(att, auth["uploadKey"])
        filed += 1
        targets.append(citekey or item_key)
        print(f"    uploaded {len(data):,} bytes as {att}")

    print(f"\n{'filed' if args.yes else 'would file'}: {filed}, "
          f"records created: {created}, skipped: {skipped}")
    if not args.yes:
        print("\nDry run. Nothing was created and nothing was uploaded.")
        if targets:
            print("Then: uv run --script zotero_source.py --refresh --only "
                  + " ".join(sorted(set(targets))))
    elif filed:
        print("\n<out>/pdf/ does not have these yet: the archive catches up on "
              "the next refresh, which fetches them back from Zotero.")
        if created:
            print("A record created here has NO citation key until a refresh "
                  "assigns one.")
        print("\nTargeted refresh for just these records:\n"
              "  uv run --script zotero_source.py --refresh --only "
              + " ".join(sorted(set(targets))))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except requests.HTTPError as exc:
        die(str(exc))
