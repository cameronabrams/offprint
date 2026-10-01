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

# Zotero has four attachment link modes and TWO of them are storage-backed.
# This set was `{"imported_file"}` until 2026-10-01, and the function was called
# `imported_files`, which is how the mistake stayed invisible: the name made the
# filter look obviously right, and the docstring reasoned carefully about the
# mode it did exclude while never mentioning the one it excluded by accident.
#
#   imported_file  bytes in Zotero storage                          -- IN
#   imported_url   bytes in Zotero storage, plus the source url     -- IN
#   linked_file    a path on this machine; the bytes are not Zotero's -- out
#   linked_url     a bookmark: no filename, no content type at all   -- out
STORAGE_MODES = {"imported_file", "imported_url"}


def storage_attachments(children: list) -> list:
    """The children Zotero expects to hold bytes for.

    `linked_url` children are bookmarks -- 149 came across in the migration and
    they are links, not attachments. Counting them is how the import's 2,894
    became the mirror's 2,745. A bookmark is recognisable without consulting the
    mode at all: it has no `filename` and an empty `contentType`.

    `imported_url` is what Mendeley's import produced for attachments it had
    recorded as fetched from a URL. The provenance differs from `imported_file`;
    the storage behaviour does not. 24 records in this library have one as their
    only child, each carrying `application/pdf`, an `m-api-<uuid>.pdf` filename
    and `md5: None` -- storage slots awaiting bytes, and until this was widened
    they were skipped as though they were bookmarks, then reported as records
    with nowhere to put a PDF.

    `linked_file` stays out for the opposite reason: its bytes are at a path on
    someone's disk and are not Zotero's to hold, so there is nothing to upload
    into.

    **UNVERIFIED, 2026-10-01: whether Zotero accepts an upload against an
    `imported_url` item.** Nobody has tested it, because testing it is a write.
    The first live run should do one of these alone, with `--key`, before the
    other 2,581.
    """
    return [c for c in children
            if (c.get("data") or {}).get("linkMode") in STORAGE_MODES]


def zotero_suffix(item: dict) -> str:
    return Path((item.get("data") or {}).get("filename") or "").suffix.lower()


def pair_attachments(locals_: list, items: list,
                     by_order: bool = False) -> tuple[list, str | None]:
    """Pair archived files with Zotero attachment items.

    Returns `(pairs, problem)`. A non-None `problem` means report it and upload
    nothing for this record: a wrong pairing files one paper's PDF under
    another's citation key, which is both silent and very hard to find later.

    Three rules, in order.

    **One against one is unambiguous**, whatever the extensions say. That case
    has to come first, because it is exactly where the extensions disagree for a
    good reason: `Vanommeslaeghe2009Charmm.pdf` in the archive against a Zotero
    filename ending `.-_charmm_g`.

    **Otherwise, a local file pairs with the Zotero attachment whose extension
    it uniquely matches.** Counts are allowed to differ, and they do: Cameron
    deleted five non-paper attachments from the archive on 2026-10-01 and Zotero
    still holds a stub for each, so `Bailey1967Crystal` is one archived `.pdf`
    against a `.pdf` and a `.cif`. One candidate is a match; two is not.

    **Anything else is reported.** Two PDFs on one record cannot be told apart
    by extension and nothing else here can tell them apart either, which is the
    honest answer rather than a coin flip.

    `by_order` is the deliberate exception: pair same-count records by archive
    order, which is Mendeley's attachment order. Zotero's child order is not
    promised to match it, so this is a GUESS and is off by default. It exists
    because the alternative for ~61 records is doing them by hand, and the dry
    run prints the pairing it would use so a person can check a sample first.
    """
    if not locals_:
        return [], None
    if not items:
        return [], (f"{len(locals_)} archived file(s) but no imported_file "
                    "attachment in Zotero to put them in")
    if len(locals_) == 1 and len(items) == 1:
        return [(locals_[0], items[0])], None

    pairs, claimed = [], set()
    for path in locals_:
        cands = [i for i, it in enumerate(items)
                 if zotero_suffix(it) == path.suffix.lower() and i not in claimed]
        if len(cands) != 1:
            pairs = None
            break
        claimed.add(cands[0])
        pairs.append((path, items[cands[0]]))
    if pairs is not None:
        return pairs, None

    local_suffixes = [p.suffix.lower() for p in locals_]
    zotero_suffixes = [zotero_suffix(it) for it in items]
    modes = [(it.get("data") or {}).get("linkMode") for it in items]
    if by_order and len(locals_) == len(items):
        return list(zip(locals_, items)), None
    return [], (f"{len(locals_)} archived against {len(items)} in Zotero, no "
                f"unambiguous pairing (archive {local_suffixes}, "
                f"zotero {zotero_suffixes}, modes {modes})")


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


def summary_lines(uploaded: int, existing: int, stranded_files: int,
                  withheld: int, total_archived: int,
                  no_archive: list, unpaired: list) -> list:
    """The closing report, as text, so the arithmetic in it can be tested.

    It is here rather than inline in `main` because the counting is exactly what
    went wrong: `len()` was taken of a list that held one entry per record AND
    one per hint line, and printed as a number of records. 62 records with 61
    hints announced 123. The real figure was 131 -- so it under-reported, which
    hides records rather than inventing them, and it landed close enough to the
    truth to read as right.

    Two rules come out of that. A count of records is taken from a list that
    holds only records. And the file arithmetic has to **close**: every archived
    file is uploaded, already present, stranded in a record that could not be
    paired, or withheld by `removed.tsv`. Saying whether it closes is the tool's
    job, not the reader's.
    """
    accounted = uploaded + existing + stranded_files + withheld
    lines = [f"archived files: {uploaded} to upload + {existing} already present "
             f"+ {stranded_files} stranded + {withheld} withheld = {accounted}"]
    if accounted != total_archived:
        lines.append(f"  ! does NOT reconcile: {total_archived} archived file(s) "
                     f"for these records, {abs(total_archived - accounted)} unaccounted")
    else:
        lines.append(f"  reconciles against {total_archived} archived file(s)")
    lines.append(f"records that uploaded nothing: {len(no_archive) + len(unpaired)} "
                 f"= {len(no_archive)} with no archived file "
                 f"+ {len(unpaired)} that could not be paired")
    return lines


def item_version(item: dict) -> int | None:
    """The version PATCH must be told, so a concurrent edit loses rather than us."""
    v = item.get("version")
    if v is None:
        v = (item.get("data") or {}).get("version")
    return v


def needs_upload(item: dict) -> bool:
    """Worth offering: this item's metadata records no file.

    **It is not a test for whether Zotero holds the bytes, and reading it as
    one was wrong.** On 2026-10-01, 1,060 attachments — 39% of the library —
    passed this check and were then answered `{"exists": 1}` by the
    authorization request, which is the only call that consults storage.
    `md5: None` in item metadata means the item does not record a file, not
    that no file is there.

    So this is a cheap pre-filter that avoids reading a file off disk, and
    `{"exists": 1}` is the authority. A dry run, which never authorizes,
    therefore cannot report how much would really be sent — and must say so
    rather than print a number that looks like it.
    """
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
    ap.add_argument("--pair-by-order", action="store_true",
                    help="for records that cannot be paired by extension, pair "
                         "them by archive order -- a GUESS, since Zotero does "
                         "not promise to return children in Mendeley's order")
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
    # Two totals, because one of them was labelled as the other. `would_bytes`
    # accumulates for every file this run READ and offered; `sent_bytes` only
    # for bytes that actually went over the wire. On the 2026-10-01 live run
    # 1,060 files were read, offered, and answered `{"exists": 1}` -- so a
    # single counter printed beside "uploaded: 1548" described 2,608 files.
    would_bytes = sent_bytes = 0
    # Three separate things, deliberately not one list. They were one list
    # until 2026-10-01, and `len()` of it was printed as a count of RECORDS --
    # but it also held the indented `--pair-by-order` hint lines, so a run with
    # 62 unpaired records and 61 hints announced "123 record(s)". The true
    # figure was 131. It read as plausible because it landed near the right
    # answer, and it under-reported, which is the direction that hides records
    # rather than inventing them.
    unpaired: list = []       # (headline, [hint lines]) -- one entry per RECORD
    no_archive: list = []     # records with nothing in pdf/ at all
    write_errors: list = []   # per-attachment, not per-record
    stranded_files = total_archived = 0

    for n, (zkey, citekey) in enumerate(sorted(zotero_keys.items(), key=lambda kv: kv[1]), 1):
        locals_ = [p for p in archived_for(pdf_dir, citekey)
                   if stem_of(p) not in removed]
        dropped = [p for p in archived_for(pdf_dir, citekey) if stem_of(p) in removed]
        skipped_removed += len(dropped)
        total_archived += len(locals_) + len(dropped)
        if not locals_:
            # A record with nothing archived uploads nothing, and until
            # 2026-10-01 said nothing either: the problem list was keyed on
            # records that HAVE files, so `Hoover1979Exact` -- whose only
            # attachment was a PNG that has since been deleted -- appeared
            # nowhere in a 5,263-line dry run. That is the same silence
            # `offprint` 0.12.0 fixed in the extraction report, arriving by a
            # different road.
            no_archive.append(f"{citekey} ({zkey})"
                              + (f", {len(dropped)} removed by hand" if dropped else ""))
            continue

        children = z.paged(f"items/{zkey}/children", quiet=True)
        items = storage_attachments(children)
        pairs, problem = pair_attachments(locals_, items, by_order=args.pair_by_order)
        if problem:
            hints = []
            # Show the pairing --pair-by-order WOULD use, so the dry run is the
            # place that decision gets checked rather than taken on trust. The
            # filenames carry author names, which is what makes a person able to
            # see that a Won paper is being offered a Shan filename -- and what
            # no summary statistic could have shown.
            if not args.pair_by_order and len(locals_) == len(items):
                guess = ", ".join(f"{p_.name} -> {(it.get('data') or {}).get('filename')!r}"
                                  for p_, it in zip(locals_, items))
                hints.append(f"--pair-by-order would use: {guess}")
            unpaired.append((f"{citekey} ({zkey}): {problem}", hints))
            stranded_files += len(locals_)
            continue

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
                        write_errors.append(f"{citekey} ({item_key}): no item "
                                            "version, cannot PATCH safely")
                    elif writer.patch_item(item_key, version, fields):
                        print(f"  patched  {citekey} -> {item_key}  {shown}", flush=True)
                        patched += 1
                    else:
                        write_errors.append(
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
                    sent_bytes += len(data)
                    writer.register(item_key, auth["uploadKey"])
                    uploaded += 1
                time.sleep(0.2)
            if args.limit and uploaded >= args.limit:
                print(f"\n  stopping at --limit {args.limit}")
                break
        if args.limit and uploaded >= args.limit:
            break

    verb = "uploaded" if args.yes else "would upload"
    if args.yes:
        print(f"\nuploaded: {uploaded}  ({sent_bytes:,} bytes sent)")
        print(f"read and offered: {would_bytes:,} bytes across "
              f"{uploaded + existing} file(s) -- Zotero already held the rest")
    else:
        print(f"\nwould upload: {uploaded}  ({would_bytes:,} bytes to read)")
        print("  a dry run cannot know how many of these Zotero already holds: "
              "`md5: None` in item metadata does not mean no bytes in storage, "
              "and only the authorization request consults storage. On "
              "2026-10-01 that was 1,060 of 2,608.")
    print(f"already in Zotero: {existing}")
    if not args.no_metadata:
        print(f"{'patched' if args.yes else 'would patch'}: {patched} "
              "(filename and content type made to match the archived file)")
    if skipped_removed:
        print(f"skipped as deliberately removed (.mirror/removed.tsv): {skipped_removed}")
    # The numbers have to close, and saying so is the tool's job rather than
    # the reader's. Every archived file is uploaded, already present, stranded
    # in a record that could not be paired, or withheld by removed.tsv.
    print()
    for line in summary_lines(uploaded, existing, stranded_files, skipped_removed,
                              total_archived, no_archive, unpaired):
        print(line)

    if no_archive:
        print(f"\nno archived file at all -- nothing to upload, and this is the "
              f"only place they are named ({len(no_archive)}):")
        for line in no_archive:
            print(f"  - {line}")

    # A pass that examines nothing does not get to report success, and neither
    # does one that silently leaves records out.
    if unpaired:
        print(f"\nREPORTED, not guessed -- {len(unpaired)} record(s), "
              f"{stranded_files} file(s):")
        for headline, hints in unpaired:
            print(f"  ! {headline}")
            for hint in hints:
                print(f"      {hint}")
    else:
        print("\nno ambiguous records")

    if write_errors:
        print(f"\nwrite errors ({len(write_errors)} attachment(s), not records):")
        for line in write_errors:
            print(f"  ! {line}")

    if not args.yes:
        print("\nThis was a dry run. Nothing was written -- no upload, no PATCH. "
              "Re-run with --yes.")


if __name__ == "__main__":
    main()
