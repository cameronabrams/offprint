#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]
# ///
"""Upload the mirrored PDFs into the Zotero records that already reference them.

The 2026-09-28 migration carried every record into Zotero but no bytes: each
attachment arrived as an `imported_file` item with `md5: None`, a filename and a
parent, and nothing behind it. The bytes are in `<out>/pdf/`, evacuated from
Mendeley by `mendeley_mirror.py --backfill`, and that archive is now the only
copy of them that is not behind a subscription.

This script fills those attachments in, and makes each one describe the file it
is given. It does not create them: where Zotero has no `imported_file`
attachment for a record, that is reported, because inventing an item is a
different kind of write from supplying bytes for one that exists.

Two things arrived wrong through the migration, from Mendeley rather than
because of Zotero, and both are corrected by a PATCH before the upload:

- **The filename is Mendeley's.** The archive names every attachment
  `<citekey>` plus its real extension, which is the name `library.bib`,
  `text/<key>.md` and `findings/` all already use. One of them is the mangled
  `...biolo.-_charmm_g`.
- **The content type can be wrong.** `Vanommeslaeghe2009Charmm` declares
  `application/octet-stream` for a complete PDF, so Zotero would store a PDF it
  does not treat as one. Only the bytes decide that, and a type is never
  invented: bytes that are not a PDF keep whatever type the item declares.

Zotero's PATCH is a real partial merge — "Properties not included in the
uploaded JSON are left untouched on the server" — which is what makes it safe to
point at 2,740 items. Only fields that actually differ are sent, so a re-run
after a partial failure writes nothing. A 412 means the item changed since this
run read it; that is reported and the item is left alone, never re-read and
retried, because a retry would overwrite whatever that edit was. `--no-metadata`
turns all of this off and uploads bytes only.

    uv run --script zotero_attach.py                 # dry run: GETs only
    uv run --script zotero_attach.py --yes           # the real thing
    uv run --script zotero_attach.py --key Abrams2013Enhanced --yes

**A dry run performs no write of any kind** -- it stops before the upload
authorization, which is itself a POST. `--yes` is for a run a person has already
approved; it is not a way past a prompt.

Resumable by construction, twice over: an attachment that already reports an
`md5` is never offered, and Zotero answers an authorization request for a file
it already holds with `{"exists": 1}`, which is honoured as success. An
interrupted run is finished by running it again.

Pairing is the one place this can be wrong in a way nobody notices. A record
with one archived file and one Zotero attachment is unambiguous and is the
overwhelming majority. A record with several is paired on file extension only
when that is unique on both sides and the two sets agree -- otherwise it is
REPORTED, not guessed, which is the rule that took `zotero_migrate.py` from
98.5% to 2,739 of 2,739 with nothing left by hand.

The protocol is Zotero Web API v3 (verified against
https://www.zotero.org/support/dev/web_api/v3/file_upload on 2026-10-01):
authorization POST with `If-None-Match: *`, then `prefix + bytes + suffix` to
the returned URL, then a registration POST carrying the upload key.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mendeley_mirror import (DEFAULT_OUT, load_json, load_removed,  # noqa: E402
                             looks_like_pdf, mirror_state_dir)
from zotero_migrate import Zotero, load_zotero_credentials  # noqa: E402

MAX_TRIES = 5


# --------------------------------------------------------------------------
# pairing -- pure, and the part most worth testing
# --------------------------------------------------------------------------

def imported_files(children: list) -> list:
    """The children that are files Zotero expects to hold bytes for.

    `linked_url` children are bookmarks -- 149 of them came across in the
    migration and they are links, not attachments. Counting them is how the
    import's 2,894 became the mirror's 2,745.
    """
    return [c for c in children
            if (c.get("data") or {}).get("linkMode") == "imported_file"]


def pair_attachments(locals_: list, items: list) -> tuple[list, str | None]:
    """Pair archived files with Zotero attachment items.

    Returns `(pairs, problem)`. A non-None `problem` means report it and upload
    nothing for this record: a wrong pairing files one paper's PDF under
    another's citation key, which is both silent and very hard to find later.

    `locals_` arrives in archive order -- `<key>`, `<key>-2`, `<key>-3` -- which
    is Mendeley's attachment order. Zotero's child order is not promised to be
    the same, so it is never used to decide anything here. It is only measured,
    by the caller, against pairings that were settled some other way.
    """
    if not locals_:
        return [], None
    if not items:
        return [], (f"{len(locals_)} archived file(s) but no imported_file "
                    "attachment in Zotero to put them in")
    if len(locals_) == 1 and len(items) == 1:
        return [(locals_[0], items[0])], None
    if len(locals_) != len(items):
        return [], (f"{len(locals_)} archived file(s) against "
                    f"{len(items)} Zotero attachment(s)")

    local_suffixes = [p.suffix.lower() for p in locals_]
    zotero_suffixes = [Path((it.get("data") or {}).get("filename") or "").suffix.lower()
                       for it in items]
    if (len(set(local_suffixes)) == len(local_suffixes)
            and sorted(local_suffixes) == sorted(zotero_suffixes)):
        by_suffix = dict(zip(zotero_suffixes, items))
        return [(p, by_suffix[p.suffix.lower()]) for p in locals_], None

    return [], (f"{len(locals_)} attachments with no unambiguous pairing "
                f"(archive {local_suffixes}, zotero {zotero_suffixes})")


def plan_metadata(item: dict, path: Path, is_pdf: bool) -> dict:
    """The fields that should change so Zotero describes the file it is given.

    Two things are wrong on the Zotero side and both came through the migration
    from Mendeley rather than being introduced by it:

    - **The filename is Mendeley's**, which for one record is the mangled
      `...biolo.-_charmm_g`. The archive names every attachment `<citekey>` plus
      its real extension, and that is the name every other part of this system
      already uses -- `library.bib`, `text/<key>.md`, `findings/`.
    - **The content type can be wrong.** `Vanommeslaeghe2009Charmm` declares
      `application/octet-stream` for a complete PDF, so Zotero would hold a PDF
      it does not treat as one. Only the bytes get to decide that, which is the
      same rule `looks_like_pdf` exists for.

    Returns only what differs, so a re-run after a partial failure sends
    nothing. A content type is never *invented*: if the bytes are not a PDF the
    declared type is left exactly as it is, because this function knows what a
    PDF looks like and nothing else.
    """
    data = item.get("data") or {}
    want = {}
    if data.get("filename") != path.name:
        want["filename"] = path.name
    if is_pdf and data.get("contentType") != "application/pdf":
        want["contentType"] = "application/pdf"
    return want


def item_version(item: dict) -> int | None:
    """The version PATCH must be told, so a concurrent edit loses rather than us."""
    v = item.get("version")
    if v is None:
        v = (item.get("data") or {}).get("version")
    return v


def needs_upload(item: dict) -> bool:
    """An attachment with an md5 already has its bytes in Zotero's storage."""
    return not (item.get("data") or {}).get("md5")


def upload_body(prefix: str, data: bytes, suffix: str) -> bytes:
    """Exactly what Zotero's authorization response says to send."""
    return prefix.encode("utf-8") + data + suffix.encode("utf-8")


# --------------------------------------------------------------------------
# the write client, kept out of zotero_migrate on purpose
# --------------------------------------------------------------------------

class ZoteroWriter:
    """File upload for Zotero Web API v3.

    Deliberately NOT a method on `zotero_migrate.Zotero`, whose docstring makes
    a promise worth keeping: "It takes no write method at all, so a mistake in
    this script cannot become a mistake in someone's library." Adding one there
    would quietly retire that guarantee for every other caller.
    """

    def __init__(self, z: Zotero):
        self.base, self.session = z.base, z.session

    def _retrying(self, fn, what: str):
        """Honour Backoff and Retry-After rather than hammering a loaded API."""
        for attempt in range(1, MAX_TRIES + 1):
            resp = fn()
            backoff = resp.headers.get("Backoff")
            if backoff:
                time.sleep(min(float(backoff), 60))
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == MAX_TRIES:
                    resp.raise_for_status()
                wait = float(resp.headers.get("Retry-After") or 2 ** attempt)
                print(f"    {what}: HTTP {resp.status_code}, retrying in {wait:g}s "
                      f"({attempt}/{MAX_TRIES})", file=sys.stderr)
                time.sleep(min(wait, 60))
                continue
            return resp
        raise RuntimeError(f"{what}: exhausted retries")

    def authorize(self, item_key: str, filename: str, data: bytes, mtime_ms: int) -> dict:
        md5 = hashlib.md5(data).hexdigest()
        resp = self._retrying(lambda: self.session.post(
            f"{self.base}/items/{item_key}/file",
            data={"md5": md5, "filename": filename,
                  "filesize": len(data), "mtime": mtime_ms},
            headers={"If-None-Match": "*",
                     "Content-Type": "application/x-www-form-urlencoded"},
            timeout=60), f"authorize {item_key}")
        if resp.status_code == 403:
            sys.exit("Zotero refused the key (403). This script needs WRITE access;\n"
                     "the migration only ever needed read. Check the key at\n"
                     "https://www.zotero.org/settings/keys")
        resp.raise_for_status()
        return resp.json()

    def put_bytes(self, auth: dict, data: bytes) -> None:
        body = upload_body(auth["prefix"], data, auth["suffix"])
        resp = self._retrying(lambda: requests.post(
            auth["url"], data=body,
            headers={"Content-Type": auth["contentType"]},
            timeout=600), "upload")
        resp.raise_for_status()

    def patch_item(self, item_key: str, version: int, fields: dict) -> bool:
        """Change only the named fields on an attachment item.

        Zotero's PATCH is a real partial merge -- "Properties not included in
        the uploaded JSON are left untouched on the server" -- which is the
        property that makes this safe to point at 2,740 items. The version
        precondition means a 412 is a concurrent edit, and the right response to
        that is to report it and leave the item alone, never to re-read and
        retry: a retry would quietly overwrite whatever that edit was.
        """
        resp = self._retrying(lambda: self.session.patch(
            f"{self.base}/items/{item_key}",
            json=fields,
            headers={"If-Unmodified-Since-Version": str(version),
                     "Content-Type": "application/json"},
            timeout=60), f"patch {item_key}")
        if resp.status_code == 412:
            return False
        resp.raise_for_status()
        return True

    def register(self, item_key: str, upload_key: str) -> None:
        resp = self._retrying(lambda: self.session.post(
            f"{self.base}/items/{item_key}/file",
            data={"upload": upload_key},
            headers={"If-None-Match": "*",
                     "Content-Type": "application/x-www-form-urlencoded"},
            timeout=60), f"register {item_key}")
        resp.raise_for_status()


# --------------------------------------------------------------------------

def archived_for(pdf_dir: Path, citekey: str) -> list:
    """Every archived file for a citation key, in attachment order.

    `<key>.<ext>` is attachment 1 and `<key>-N.<ext>` is attachment N, so a
    numeric sort on the suffix is the archive's own order and not an
    alphabetical accident.
    """
    found = []
    for path in pdf_dir.iterdir():
        if not path.is_file() or path.stat().st_size == 0:
            continue
        stem = path.name[:len(path.name) - len(path.suffix)] if path.suffix else path.name
        if stem == citekey:
            found.append((1, path))
        elif stem.startswith(f"{citekey}-") and stem[len(citekey) + 1:].isdigit():
            found.append((int(stem[len(citekey) + 1:]), path))
    return [p for _, p in sorted(found)]


def stem_of(path: Path) -> str:
    return path.name[:len(path.name) - len(path.suffix)] if path.suffix else path.name


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Upload mirrored PDFs into the Zotero records that already reference them.")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT,
                    help=f"mirror directory (default: {DEFAULT_OUT})")
    ap.add_argument("--yes", action="store_true",
                    help="actually upload; without it this is a dry run that only reads")
    ap.add_argument("--key", action="append", default=[],
                    help="limit to this citation key (repeatable)")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after this many uploads, for a cautious first run")
    ap.add_argument("--no-metadata", action="store_true",
                    help="upload bytes only; leave each attachment's filename and "
                         "content type as the migration left them")
    args = ap.parse_args()

    out: Path = args.out
    pdf_dir = out / "pdf"
    if not pdf_dir.is_dir():
        sys.exit(f"No archive at {pdf_dir}. Run mendeley_mirror.py --backfill first.")

    keymap = load_json(mirror_state_dir(out) / "citekeys.json", {})
    zotero_keys = {k.split(":", 1)[1]: v for k, v in keymap.items()
                   if k.startswith("zotero:")}
    if not zotero_keys:
        sys.exit("citekeys.json holds no zotero: entries. Run zotero_migrate.py first.")
    wanted = set(args.key)
    if wanted:
        missing = wanted - set(zotero_keys.values())
        if missing:
            sys.exit(f"not a citation key in citekeys.json: {', '.join(sorted(missing))}")
        zotero_keys = {k: v for k, v in zotero_keys.items() if v in wanted}

    removed = load_removed(out)

    api_key, user_id = load_zotero_credentials()
    z = Zotero(api_key, user_id)
    writer = ZoteroWriter(z)

    mode = "UPLOADING" if args.yes else "DRY RUN -- reading only, nothing is written"
    print(f"offprint zotero_attach: {mode}")
    print(f"  mirror  {out}")
    print(f"  records {len(zotero_keys)}\n")

    uploaded = existing = skipped_removed = patched = 0
    would_bytes = 0
    problems: list = []
    order_checked = order_agreed = 0

    for n, (zkey, citekey) in enumerate(sorted(zotero_keys.items(), key=lambda kv: kv[1]), 1):
        locals_ = [p for p in archived_for(pdf_dir, citekey)
                   if stem_of(p) not in removed]
        dropped = [p for p in archived_for(pdf_dir, citekey) if stem_of(p) in removed]
        skipped_removed += len(dropped)
        if not locals_:
            continue

        children = z.paged(f"items/{zkey}/children", quiet=True)
        items = imported_files(children)
        pairs, problem = pair_attachments(locals_, items)
        if problem:
            problems.append(f"{citekey} ({zkey}): {problem}")
            continue

        # Evidence, not a decision: where the pairing was settled by extension,
        # does Zotero's child order happen to agree? Enough of these and index
        # order becomes a defensible fallback for the ones that cannot be paired
        # at all. Too few, and it stays off the table. Either way it decides
        # nothing in this run.
        if len(pairs) > 1:
            order_checked += 1
            order_agreed += int(pairs == list(zip(locals_, items)))

        for path, item in pairs:
            item_key = item.get("key") or (item.get("data") or {}).get("key")
            data = path.read_bytes()

            # Metadata first, so the name the upload is authorized under is the
            # name the item will carry. Done even for an attachment whose bytes
            # are already in Zotero: a wrong filename is wrong either way, and
            # this is the run that is looking at every record.
            fields = {} if args.no_metadata else plan_metadata(
                item, path, looks_like_pdf(data))
            if fields:
                shown = ", ".join(f"{k}={v!r}" for k, v in sorted(fields.items()))
                if not args.yes:
                    print(f"  would patch   {citekey} -> {item_key}  {shown}")
                    patched += 1
                else:
                    version = item_version(item)
                    if version is None:
                        problems.append(f"{citekey} ({item_key}): no item version, "
                                        "cannot PATCH safely")
                    elif writer.patch_item(item_key, version, fields):
                        print(f"  patched  {citekey} -> {item_key}  {shown}", flush=True)
                        patched += 1
                    else:
                        problems.append(
                            f"{citekey} ({item_key}): 412, the item changed since "
                            "this run read it -- left alone, re-run to pick it up")
                        continue

            if not needs_upload(item):
                existing += 1
                continue
            filename = fields.get("filename") or (item.get("data") or {}).get("filename") or path.name
            would_bytes += len(data)
            label = f"{n}/{len(zotero_keys)}  {citekey} -> {item_key}  {path.name}"
            if not args.yes:
                print(f"  would upload  {label}  ({len(data):,} bytes as {filename!r})")
                uploaded += 1
            else:
                print(f"  {label}  ({len(data):,} bytes)", flush=True)
                auth = writer.authorize(item_key, filename, data,
                                        int(path.stat().st_mtime * 1000))
                if auth.get("exists"):
                    existing += 1
                else:
                    writer.put_bytes(auth, data)
                    writer.register(item_key, auth["uploadKey"])
                    uploaded += 1
                time.sleep(0.2)
            if args.limit and uploaded >= args.limit:
                print(f"\n  stopping at --limit {args.limit}")
                break
        if args.limit and uploaded >= args.limit:
            break

    verb = "uploaded" if args.yes else "would upload"
    print(f"\n{verb}: {uploaded}  ({would_bytes:,} bytes)")
    print(f"already in Zotero: {existing}")
    if not args.no_metadata:
        print(f"{'patched' if args.yes else 'would patch'}: {patched} "
              "(filename and content type made to match the archived file)")
    if skipped_removed:
        print(f"skipped as deliberately removed (.mirror/removed.tsv): {skipped_removed}")
    if order_checked:
        print(f"child-order agreement on records paired by extension: "
              f"{order_agreed}/{order_checked}")

    # A pass that examines nothing does not get to report success, and neither
    # does one that silently leaves records out.
    if problems:
        print(f"\nREPORTED, not guessed -- {len(problems)} record(s) uploaded nothing:")
        for line in problems:
            print(f"  ! {line}")
    else:
        print("\nno ambiguous records")

    if not args.yes:
        print("\nThis was a dry run. Nothing was written -- no upload, no PATCH. "
              "Re-run with --yes.")


if __name__ == "__main__":
    main()
