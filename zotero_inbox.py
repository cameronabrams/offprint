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

**This is the only script in the repo that creates a Zotero item.**
`zotero_attach.py` fills attachment slots that already exist; it has no way to
make one, which is why a paper fetched by hand had no route into the library at
all. Ten of the eleven on the current fetch list are published versions of
records that already hold a preprint, so filing one means creating a *second*
attachment on an existing record -- not a new record.

Four refusals, each because the alternative is a library that is wrong rather
than a library that is missing something:

- **It never creates a RECORD.** If the paper is not already in the library it
  is reported, not added. Adding a reference is a different operation with
  different metadata risks, and `mendeley_push.py` has no Zotero counterpart yet.
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
from zotero_attach import ZoteroWriter, storage_attachments  # noqa: E402
from zotero_migrate import Zotero, load_zotero_credentials  # noqa: E402


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


def plan_one(out: Path, path: Path, forced_key: str) -> tuple[str, str]:
    """`(citekey, why)` for one file. An empty citekey means do not file it."""
    data = path.read_bytes()
    if not looks_like_pdf(data):
        return "", "not a PDF by its bytes"
    if not has_content(path):
        return "", ("no readable text -- a scan filed silently becomes a paper "
                    "that cannot be quoted and looks complete")
    if forced_key:
        return forced_key, "named with --key"
    msg, doi, how = identify(path)
    if not msg:
        return "", f"could not identify it ({how})"
    _mid, citekey = existing_document(out, doi, title_of(msg))
    if not citekey:
        return "", (f"identified as {doi or title_of(msg)[:60]!r} but no record "
                    "in library.bib holds it -- this script does not create "
                    "records")
    return citekey, f"{how}: {doi or title_of(msg)[:60]}"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="File a downloaded PDF into the Zotero record it belongs to.")
    ap.add_argument("files", nargs="*", type=Path,
                    help="PDFs to file; default is everything in <out>/inbox/")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="mirror directory")
    ap.add_argument("--key", help="file ONE named PDF under this citation key, "
                                  "skipping identification")
    ap.add_argument("--yes", action="store_true",
                    help="actually create and upload; without it this is a dry run")
    ap.add_argument("--version", action="version", version=f"offprint {__version__}")
    args = ap.parse_args()

    if args.key and len(args.files) != 1:
        ap.error("--key names one record, so give exactly one file with it")
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

    filed = skipped = 0
    for path in files:
        citekey, why = plan_one(args.out, path, args.key or "")
        if not citekey:
            print(f"  ! {path.name}: {why}")
            skipped += 1
            continue
        item_key = zotero_key_for(args.out, citekey)
        if not item_key:
            print(f"  ! {path.name}: {citekey} has no zotero: entry in "
                  "citekeys.json")
            skipped += 1
            continue
        data = path.read_bytes()
        children = z.paged(f"items/{item_key}/children", quiet=True)
        dup = already_attached(children, data)
        if dup:
            print(f"  = {path.name}: already attached to {citekey} as {dup}")
            skipped += 1
            continue
        n_existing = len(storage_attachments(children))
        print(f"  {'+' if args.yes else 'would add'} {path.name} -> {citekey} "
              f"({item_key}), attachment {n_existing + 1}  [{why}]")
        if not args.yes:
            filed += 1
            continue
        att = create_attachment(z, item_key, f"{citekey}.pdf", "application/pdf")
        auth = writer.authorize(att, f"{citekey}.pdf", data,
                                int(path.stat().st_mtime * 1000))
        if not auth.get("exists"):
            writer.put_bytes(auth, data)
            writer.register(att, auth["uploadKey"])
        filed += 1
        print(f"    uploaded {len(data):,} bytes as {att}")

    print(f"\n{'filed' if args.yes else 'would file'}: {filed}, skipped: {skipped}")
    if not args.yes:
        print("\nDry run. Nothing was created and nothing was uploaded.")
    elif filed:
        print("\n<out>/pdf/ does not have these yet: the archive catches up on "
              "the next refresh, which fetches them back from Zotero.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except requests.HTTPError as exc:
        die(str(exc))
