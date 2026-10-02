#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]
# ///
"""
zotero_edit.py -- correct fields on records already IN Zotero.

The direct counterpart of `mendeley_edit.py`, for the backend that is now live.
Same cautions, same keying by CITATION KEY -- but **the field names are Zotero's,
not Mendeley's, and an edits file written for the old script will not run here.**
This docstring said "ports unchanged" on 2026-10-02 and that was wrong: the
library session's file needed `authors` -> `creators` and
`first_name`/`last_name` -> `firstName`/`lastName`. The unknown-field check
stopped it on the first record, which is what that check is for, but a claim
someone acts on with `--yes` has no business being wrong. The translations are
listed in `MENDELEY_FIELDS` and named in the error.

    uv run --script zotero_edit.py --edits fixes.json --dry-run   # always first
    uv run --script zotero_edit.py --edits fixes.json             # asks before sending
    uv run --script zotero_edit.py --edits fixes.json --yes       # already approved

    {
      "Won2001Influence": {
        "creators": [{"lastName": "Shan", "firstName": "J."},
                     {"lastName": "Robertson", "firstName": "C.G."}]
      },
      "Something2020Else": {"volume": null}
    }

Keys resolve through `.mirror/citekeys.json`'s `zotero:` half, so a key the
mirror has never seen is an error rather than a silent no-op.

**The dangerous field here is `creators`, not `identifiers`.** Mendeley kept its
DOI, ISSN and PMID in one nested object that its PATCH replaced wholesale, so
`mendeley_edit.py` had to merge it by hand or lose an ISSN every time someone
fixed a DOI. Zotero's PATCH *is* a partial merge at the top level -- "Properties
not included in the uploaded JSON are left untouched on the server" -- so DOI
and ISSN are separate fields and that hazard is simply gone.

It reappears one level down. `creators` is a LIST, and sending one replaces the
whole list, editors and translators included. So the same protection is applied
where the same risk now lives: **an edit replaces only the creator types it
mentions and preserves the ones it does not.** A list of authors replaces the
authors and keeps the editor. An entry with no `creatorType` is an author.

Order is the payload, not an accident: 15 records in this library have their
authors in the wrong order, so the given order is used exactly. Replaced types
come first in the order written, then the untouched types in their original
order.

Three further rules, all of them `mendeley_edit.py`'s:

- **A value already equal to Zotero's is skipped**, so a re-run after a partial
  failure sends nothing for the records that already succeeded.
- **An explicit `null` clears a field** -- an empty string for a scalar, an
  empty list for `creators`. A merge that protects good data also makes bad data
  undeletable, and a DOI resolving to an unrelated paper is worse than no DOI.
- **An unknown field name is an error.** Zotero returns every field valid for an
  item's type, including the empty ones, so a name that is not already a key of
  `data` is a typo -- and a typo that were sent would be ignored server-side,
  which is a silent no-op wearing the clothes of a successful edit.

A 412 means the item changed since this run read it. It is reported and the item
left alone, never re-read and retried, because the retry would overwrite exactly
the edit that caused the conflict.

Credentials are the Zotero key in `~/.config/offprint/zotero.json`, and it needs
write access.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mendeley_mirror import DEFAULT_OUT, __version__, load_json, mirror_state_dir  # noqa: E402
from zotero_attach import ZoteroWriter  # noqa: E402
from zotero_migrate import Zotero, load_zotero_credentials  # noqa: E402

AUTHOR = "author"

# Named in the error rather than silently translated. Accepting a Mendeley field
# name would be guessing at what someone meant about their own library; saying
# which Zotero field they want costs one line and is the difference between a
# refusal and a dead end.
MENDELEY_FIELDS = {
    "authors": "creators",
    "editors": 'creators, with "creatorType": "editor"',
    "source": "publicationTitle, bookTitle or proceedingsTitle, by item type",
    "identifiers": "DOI, ISSN and ISBN, which are separate top-level fields here",
    "year": "date",
    "city": "place",
    "abstract": "abstractNote",
    "keywords": "tags",
    "institution": "university on a thesis, institution on a report",
    "websites": "url",
    "type": "itemType, which this script will not change",
}


def die(msg: str):
    sys.exit(f"error: {msg}")


def show(label: str, value) -> str:
    if isinstance(value, (dict, list)):
        return f"{label}: {json.dumps(value, ensure_ascii=False)}"
    return f"{label}: {value!r}"


def check_creator_shape(creators: list) -> None:
    """Refuse Mendeley's person shape, and say what Zotero's is.

    `{"first_name": ..., "last_name": ...}` is what the Mendeley scripts take.
    Passed here it would be accepted as a creator with empty names, and Zotero
    would store a record with blank authors -- a successful-looking edit that
    destroys the field it was meant to repair.
    """
    for c in creators or []:
        wrong = sorted(k for k in c if k in ("first_name", "last_name"))
        if wrong:
            die(f"creator entries use firstName/lastName, not "
                f"{'/'.join(wrong)} -- that is Mendeley's shape. Zotero also "
                f'accepts a single "name" for a corporate author.')


def norm_creator(c: dict) -> dict:
    """One creator in Zotero's own shape, with `author` assumed.

    Zotero accepts either a two-field name (`firstName`/`lastName`) or a single
    `name` for a corporate author, and both are kept as given -- rewriting one
    into the other would change records nobody asked to change.
    """
    out = {"creatorType": c.get("creatorType") or AUTHOR}
    if c.get("name"):
        out["name"] = c["name"]
    else:
        out["firstName"] = c.get("firstName", "")
        out["lastName"] = c.get("lastName", "")
    return out


def merge_creators(current: list, new: list) -> list:
    """Replace the creator types the edit mentions; keep the ones it does not.

    `creators` is a list and Zotero's PATCH replaces a list wholesale, so
    sending an author list would drop an editor. This is the same protection
    `mendeley_edit.py` gives `identifiers`, moved to where the risk is under
    this backend.

    The replaced types come first, in exactly the order written -- order is the
    correction in 15 of this library's records -- and the untouched types follow
    in their original order.
    """
    new_norm = [norm_creator(c) for c in new]
    touched = {c["creatorType"] for c in new_norm}
    kept = [c for c in (current or []) if c.get("creatorType") not in touched]
    return new_norm + kept


def plan_patch(data: dict, edits: dict) -> tuple[dict, list[str]]:
    """What to PATCH, and the before/after lines to show. Pure, so it is testable."""
    patch: dict = {}
    lines: list[str] = []
    for field, new_value in edits.items():
        if field not in data:
            hint = MENDELEY_FIELDS.get(field)
            if hint:
                die(f"{field!r} is Mendeley's field name. Zotero wants {hint}.")
            die(f"{field!r} is not a field of this item type. Zotero returns "
                f"every valid field including the empty ones, so this is a typo "
                f"-- and Zotero would ignore it silently rather than refuse it.")
        old_value = data.get(field)
        if field == "creators":
            if new_value is not None:
                check_creator_shape(new_value)
            merged = [] if new_value is None else merge_creators(old_value, new_value)
            if merged == (old_value or []):
                continue
            patch[field] = merged
            lines.append(f"    {show('creators  from', old_value)}")
            lines.append(f"    {show('creators    to', merged)}")
            continue
        cleared = "" if new_value is None else new_value
        if old_value == cleared:
            continue
        patch[field] = cleared
        lines.append(f"    {show(f'{field:14} from', old_value)}")
        lines.append(f"    {show(f'{field:14}   to', cleared)}")
    return patch, lines


def main() -> int:
    ap = argparse.ArgumentParser(description="Correct fields on existing Zotero records.")
    ap.add_argument("--edits", type=Path, required=True,
                    help="JSON file: {citekey: {field: value}}")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="mirror directory")
    ap.add_argument("--dry-run", action="store_true", help="show the diff, send nothing")
    ap.add_argument("--yes", action="store_true", help="do not ask before sending")
    ap.add_argument("--version", action="version", version=f"offprint {__version__}")
    args = ap.parse_args()

    try:
        edits = json.loads(args.edits.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        die(f"cannot read {args.edits}: {exc}")
    if not isinstance(edits, dict) or not edits:
        die("the edits file must be a non-empty object keyed by citation key")

    keymap = load_json(mirror_state_dir(args.out) / "citekeys.json", {})
    by_citekey = {v: k.split(":", 1)[1] for k, v in keymap.items()
                  if k.startswith("zotero:")}
    if not by_citekey:
        die("citekeys.json holds no zotero: entries; run zotero_migrate.py first")
    unknown = [k for k in edits if k not in by_citekey]
    if unknown:
        die("not a citation key this mirror knows: " + ", ".join(sorted(unknown)))

    api_key, user_id = load_zotero_credentials()
    z = Zotero(api_key, user_id)
    writer = ZoteroWriter(z)

    mode = "DRY RUN -- nothing is sent" if args.dry_run else "live"
    print(f"offprint {__version__} zotero_edit: {mode}")
    print(f"  {len(edits)} record(s)\n")

    planned, skipped = [], 0
    for citekey in sorted(edits):
        item_key = by_citekey[citekey]
        resp = z.session.get(f"{z.base}/items/{item_key}", timeout=60)
        if resp.status_code == 404:
            die(f"{citekey}: Zotero has no item {item_key}. citekeys.json and the "
                "library disagree, which is worth understanding before editing anything.")
        resp.raise_for_status()
        item = resp.json()
        patch, lines = plan_patch(item.get("data") or {}, edits[citekey])
        if not patch:
            skipped += 1
            continue
        print(f"  {citekey}  ({item_key})")
        for line in lines:
            print(line)
        planned.append((citekey, item_key, item.get("version"), patch))

    print(f"\n{len(planned)} record(s) to change, {skipped} already correct")
    if not planned:
        print("Nothing to do.")
        return 0
    if args.dry_run:
        print("\nDry run. Nothing was sent.")
        return 0
    if not args.yes:
        if input("\nSend these to Zotero? type yes: ").strip().lower() != "yes":
            print("Nothing sent.")
            return 1

    sent = conflicts = 0
    for citekey, item_key, version, patch in planned:
        if version is None:
            print(f"  ! {citekey}: no item version, refusing to PATCH without one")
            continue
        if writer.patch_item(item_key, version, patch):
            sent += 1
            print(f"  patched {citekey}")
        else:
            conflicts += 1
            print(f"  ! {citekey}: 412, changed since this run read it -- left "
                  "alone. Re-run to pick it up.")
    print(f"\npatched {sent}, conflicts {conflicts}, already correct {skipped}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except requests.HTTPError as exc:
        die(str(exc))
