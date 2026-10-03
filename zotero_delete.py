#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]
# ///
"""
zotero_delete.py -- remove a record or a byte-less attachment from Zotero.

**The one operation in this repo with no undo**, so it is the most guarded. The
shape comes from 16 deletions the library session performed by hand on
2026-10-02 -- 11 duplicate records and 5 attachment stubs -- and from its own
account of what it had to remember rather than be given.

    uv run --script zotero_delete.py --key Frankel1998Hiva --key Frankel1998Hivb
    uv run --script zotero_delete.py --key Frankel1998Hiva --yes
    uv run --script zotero_delete.py --attachment R83UG3SX     # one attachment
    uv run --script zotero_delete.py --stubs            # byte-less attachments

**A dry run names what SURVIVES as well as what goes**, which is the library
session's first ask and the right one: *the dangerous deletion is not the one
you listed, it is the sibling you did not.* Deleting `Frankel1998Hiva/b/c` is
only correct if `Frankel1998Hiv` is still there and still holds its attachment,
and that is a fact about the records you are **not** naming.

So for every record named, the survivors sharing its title are printed with
their attachment counts, and **a deletion that would leave no survivor is
refused** unless `--allow-last-copy` says otherwise. That case is not a
duplicate being tidied; it is a paper leaving the library, which is a different
decision and should have to be spelled.

`--attachment` removes a single attachment **and its bytes**, which `--stubs`
deliberately cannot: a stub is a record of a file that no longer exists, and
this is a file that does. The same guards apply, plus one that only matters
here — **annotations hang off the attachment, not the record**, so they go with
it, and they are the one part that re-uploading the file cannot bring back. The
dry run counts them.

**Every item and its children are backed up before anything is sent**, to
`.mirror/deleted-<timestamp>.json`, whose path is printed. The library session
did this by hand and said it would not have under time pressure. It goes in
`.mirror/` rather than a cache so it travels with the library.

Two things it deliberately does **not** do, both at the library session's
request and both right:

- **It never touches `citekeys.json`.** The map is append-only by design, a
  deleted key stays reserved, and a key cited in a manuscript keeps resolving.
- **It never deletes from `pdf/` or `text/`.** `Frankel1998Hiva/b/c` held a
  *different* scan from the record that survived them, so deleting the records
  was right and deleting their archived files would have destroyed a unique
  copy. Tidying orphans needs the uniqueness check in
  `get_pdf.py --attachments` and is a separate, deliberate step.

**On `--stubs`, one correction to the spec it was built from.** A byte-less
attachment cannot be identified by `md5: None` in the item metadata. That field
means the item records no file, not that no file is there -- on 2026-10-02,
1,060 attachments reported `md5: None` and Zotero held bytes for every one of
them. So a stub is an attachment whose **file endpoint answers 404**, which is
a read and is authoritative, and that is what this checks.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mendeley_mirror import DEFAULT_OUT, __version__, load_json, mirror_state_dir  # noqa: E402
from zotero_attach import storage_attachments  # noqa: E402
from zotero_migrate import Zotero, load_zotero_credentials, parse_bib  # noqa: E402


def die(msg: str):
    sys.exit(f"error: {msg}")


def norm_title(s: str) -> str:
    return "".join(c for c in (s or "").lower() if c.isalnum())


def siblings(bib_entries: dict, citekey: str) -> list:
    """Other citation keys in the bib whose title matches this one's.

    Title rather than citation key, because the keys of a duplicate set are not
    reliably related: `Frankel1998Hiv` and `Frankel1998Hiva` share a prefix, but
    `assign_citekeys` disambiguates by appending a letter only when it has to,
    and two records can collide without either being suffixed.
    """
    want = norm_title((bib_entries.get(citekey) or {}).get("title", ""))
    if not want:
        return []
    return sorted(k for k, f in bib_entries.items()
                  if k != citekey and norm_title(f.get("title", "")) == want)


def has_bytes(z: Zotero, item_key: str) -> bool:
    """Does Zotero actually hold a file for this attachment?

    **Not `md5 is None`.** That field says the item records no file, which is a
    different claim: 1,060 attachments in this library reported `md5: None` and
    Zotero served bytes for all of them. The file endpoint is the authority, and
    asking it is a read.
    """
    resp = z.session.get(f"{z.base}/items/{item_key}/file", timeout=60,
                         allow_redirects=False)
    if resp.status_code == 404:
        return False
    if resp.status_code in (200, 301, 302, 303, 307):
        return True
    resp.raise_for_status()
    return True


def delete_item(z: Zotero, item_key: str, version: int) -> bool:
    """DELETE one item. False means 412: it changed since this run read it."""
    resp = z.session.delete(
        f"{z.base}/items/{item_key}",
        headers={"If-Unmodified-Since-Version": str(version)}, timeout=60)
    if resp.status_code == 412:
        return False
    if resp.status_code == 403:
        die("Zotero refused the key (403). Deleting needs WRITE access.")
    resp.raise_for_status()
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Delete a Zotero record or attachment stub.")
    ap.add_argument("--key", action="append", default=[],
                    help="citation key of a record to delete (repeatable)")
    ap.add_argument("--attachment", action="append", default=[], metavar="KEY",
                    help="Zotero key of ONE attachment to delete, bytes and all "
                         "(repeatable)")
    ap.add_argument("--stubs", action="store_true",
                    help="find attachments whose file endpoint 404s and remove them")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="mirror directory")
    ap.add_argument("--yes", action="store_true",
                    help="actually delete; without it this is a dry run")
    ap.add_argument("--allow-last-copy", action="store_true",
                    help="permit deleting a record with no surviving sibling -- "
                         "that is a paper leaving the library, not a duplicate "
                         "being tidied")
    ap.add_argument("--version", action="version", version=f"offprint {__version__}")
    args = ap.parse_args()
    if not args.key and not args.stubs and not args.attachment:
        ap.error("nothing to do: give --key, --attachment, or --stubs")

    bib_path = args.out / "library.bib"
    if not bib_path.is_file():
        die(f"no library.bib at {bib_path}")
    bib_entries = {k: f for k, _t, f in parse_bib(bib_path.read_text(encoding="utf-8"))}
    keymap = load_json(mirror_state_dir(args.out) / "citekeys.json", {})
    by_citekey = {v: k.split(":", 1)[1] for k, v in keymap.items()
                  if k.startswith("zotero:")}

    api_key, user_id = load_zotero_credentials()
    z = Zotero(api_key, user_id)

    print(f"offprint {__version__} zotero_delete: "
          f"{'DELETING' if args.yes else 'DRY RUN -- nothing is sent'}\n")

    doomed: list = []       # (label, item_key, version, backup payload)
    refused: list = []

    for citekey in args.key:
        item_key = by_citekey.get(citekey)
        if not item_key:
            refused.append(f"{citekey}: no zotero: entry in citekeys.json")
            continue
        resp = z.session.get(f"{z.base}/items/{item_key}", timeout=60)
        if resp.status_code == 404:
            refused.append(f"{citekey}: Zotero has no item {item_key}")
            continue
        resp.raise_for_status()
        item = resp.json()
        children = z.paged(f"items/{item_key}/children", quiet=True)

        # What SURVIVES. The whole point: the risk is the sibling nobody listed.
        sibs = siblings(bib_entries, citekey)
        surviving = [s for s in sibs if s not in args.key]
        print(f"  DELETE  {citekey}  ({item_key}), "
              f"{len(storage_attachments(children))} attachment(s)")
        if surviving:
            for s in surviving:
                s_key = by_citekey.get(s, "")
                n = ""
                if s_key:
                    s_kids = z.paged(f"items/{s_key}/children", quiet=True)
                    n = f", {len(storage_attachments(s_kids))} attachment(s)"
                print(f"     survives: {s} ({s_key or 'no zotero entry'}{n})")
        else:
            print("     survives: NOTHING with this title")
            if not args.allow_last_copy:
                refused.append(
                    f"{citekey}: no surviving record shares its title. That is a "
                    "paper leaving the library, not a duplicate being tidied -- "
                    "pass --allow-last-copy if you mean it.")
                print("     ! refused")
                continue
        doomed.append((citekey, item_key, item.get("version"),
                       {"item": item, "children": children}))

    for att_key in args.attachment:
        resp = z.session.get(f"{z.base}/items/{att_key}", timeout=60)
        if resp.status_code == 404:
            refused.append(f"attachment {att_key}: Zotero has no such item")
            continue
        resp.raise_for_status()
        att = resp.json()
        data = att.get("data") or {}
        if data.get("itemType") != "attachment":
            refused.append(f"{att_key}: that is a {data.get('itemType')}, not an "
                           "attachment. Use --key for a record.")
            continue
        parent = data.get("parentItem")
        siblings_here = []
        if parent:
            kids = z.paged(f"items/{parent}/children", quiet=True)
            siblings_here = [c for c in storage_attachments(kids)
                             if c.get("key") != att_key]

        # Annotations hang off the ATTACHMENT, not the record, so they go with
        # it. Said before the deletion rather than discovered after: they are a
        # reader's own highlights and comments, and the one part of an
        # attachment that cannot be recovered by re-uploading the file.
        notes = [c for c in z.paged(f"items/{att_key}/children", quiet=True)
                 if (c.get("data") or {}).get("itemType") in ("annotation", "note")]

        print(f"  DELETE  attachment {att_key}  {data.get('filename')!r}"
              + (f"  on {parent}" if parent else "  (no parent)"))
        if notes:
            print(f"     and {len(notes)} annotation(s)/note(s) on it, which go "
                  "with it and cannot be recovered by re-uploading the file")
        if siblings_here:
            for c in siblings_here:
                d = c.get("data") or {}
                print(f"     survives: {c.get('key')}  {d.get('filename')!r}")
        else:
            print("     survives: NOTHING -- this is the record's only attachment")
            if not args.allow_last_copy:
                refused.append(
                    f"attachment {att_key}: it is the only one on {parent}, so "
                    "deleting it leaves that record with no file at all. Pass "
                    "--allow-last-copy if you mean it.")
                print("     ! refused")
                continue
        doomed.append((f"attachment {att_key}", att_key, att.get("version"),
                       {"item": att, "children": notes}))

    if args.stubs:
        print("  scanning for attachments with no bytes "
              "(the file endpoint, not md5)...")
        for a in z.paged("items", quiet=True, itemType="attachment"):
            data = a.get("data") or {}
            if data.get("linkMode") not in ("imported_file", "imported_url"):
                continue
            key = a.get("key")
            if has_bytes(z, key):
                continue
            print(f"  DELETE  stub {key}  {data.get('filename')!r} "
                  f"on {data.get('parentItem')}")
            doomed.append((f"stub {key}", key, a.get("version"),
                           {"item": a, "children": []}))

    for line in refused:
        print(f"  ! {line}")
    print(f"\n{len(doomed)} to delete, {len(refused)} refused")
    if not doomed:
        return 1 if refused else 0

    if not args.yes:
        print("\nDry run. Nothing was sent. Read the 'survives' lines before "
              "re-running with --yes.")
        return 0

    # The backup goes out BEFORE anything is deleted, and its path is printed.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = mirror_state_dir(args.out) / f"deleted-{stamp}.json"
    backup.write_text(json.dumps([d[3] for d in doomed], indent=2,
                                 ensure_ascii=False), encoding="utf-8")
    print(f"\nbacked up {len(doomed)} item(s) and their children to {backup}")

    gone = conflicts = 0
    for label, item_key, version, _payload in doomed:
        if version is None:
            print(f"  ! {label}: no item version, refusing to delete without one")
            continue
        if delete_item(z, item_key, version):
            gone += 1
            print(f"  deleted {label}")
        else:
            conflicts += 1
            print(f"  ! {label}: 412, changed since this run read it -- left "
                  "alone. Re-run to pick it up.")
    print(f"\ndeleted {gone}, conflicts {conflicts}")
    print(f"Nothing in pdf/, text/ or citekeys.json was touched. The backup is "
          f"{backup}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except requests.HTTPError as exc:
        die(str(exc))
