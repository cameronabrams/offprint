#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]
# ///
"""
mendeley_edit.py -- correct fields on references already IN Mendeley.

The mirror is one-way: Mendeley is the source of truth and everything in the
mirror folder is generated from it. Two scripts already break that on purpose --
inbox.py attaches files, mendeley_push.py adds references. This is the third,
and the most invasive, because it CHANGES metadata that is already there rather
than adding something new. Treat it accordingly.

    uv run --script mendeley_edit.py --edits fixes.json --dry-run   # always first
    uv run --script mendeley_edit.py --edits fixes.json             # asks before sending
    uv run --script mendeley_edit.py --edits fixes.json --yes       # for a run already approved

The edits file is JSON keyed by CITATION KEY, so you name references the way the
rest of the mirror does rather than juggling Mendeley's UUIDs:

    {
      "Huang2016Charmm":  {"year": 2017},
      "Bennett2023Microsecond": {
          "type": "journal", "source": "Science Advances", "year": 2024,
          "pages": "eadj0396", "identifiers": {"doi": "10.1126/sciadv.adj0396"}
      }
    }

Keys are resolved through .mirror/citekeys.json, which is also why a key that
the mirror has never seen is an error rather than a silent no-op.

Two behaviors worth knowing. "identifiers" is MERGED into the document's
existing identifiers, not substituted for them, so correcting a DOI does not
silently drop an ISSN or PMID. Everything else is replaced field-for-field.
And a value already equal to what Mendeley holds is skipped, not re-sent, so a
re-run after a partial failure is safe.

Nothing is sent under --dry-run, and nothing is sent without either a typed
confirmation or --yes.

Credentials are the mirror's, from the same per-machine store; run the mirror
once on a new machine to log in.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    from mendeley_mirror import (DEFAULT_OUT, Mendeley, config_dir, get_app_config,
                                 load_json, local_id, mirror_state_dir)
    from mendeley_push import DOC_CT
except ImportError as exc:  # pragma: no cover
    sys.exit(f"mendeley_edit.py must sit beside the other mirror scripts ({exc})")

API = "https://api.mendeley.com"


def die(msg: str):
    sys.exit(f"error: {msg}")


def show(label: str, value) -> str:
    if isinstance(value, (dict, list)):
        return f"{label}: {json.dumps(value, ensure_ascii=False)}"
    return f"{label}: {value!r}"


def plan_patch(current: dict, edits: dict) -> tuple[dict, list[str]]:
    """What to PATCH, and the before/after lines to show for it.

    Pure, so the two rules that keep this script from destroying good metadata
    are testable without touching an account:

    - "identifiers" is MERGED into what the document already has. Substituting
      it would drop an ISSN or a PMID every time someone corrected a DOI.
    - null REMOVES. The merge above protects good identifiers, but it also made
      a bad one impossible to delete, and a record whose DOI resolves to an
      unrelated paper is worse than one with no DOI at all. So an explicit null
      deletes: {"identifiers": {"doi": null}} drops the DOI and keeps the rest,
      and {"volume": null} clears a top-level field.
    - a field already equal to Mendeley's value is omitted, so a re-run after a
      partial failure sends nothing for the records that already succeeded.
    """
    patch: dict = {}
    lines: list[str] = []
    for field, new_value in edits.items():
        old_value = current.get(field)
        if field == "identifiers":
            merged = {**(old_value or {}), **new_value}
            for k, v in new_value.items():
                if v is None:
                    merged.pop(k, None)
            if merged == (old_value or {}):
                continue
            patch[field] = merged
            lines.append(f"    {show('identifiers  from', old_value)}")
            lines.append(f"    {show('identifiers    to', merged)}")
            continue
        if old_value == new_value:
            continue
        patch[field] = new_value
        lines.append(f"    {show(f'{field:12} from', old_value)}")
        lines.append(f"    {show(f'{field:12}   to', new_value)}")
    return patch, lines


def main() -> int:
    ap = argparse.ArgumentParser(description="Correct fields on existing Mendeley references.")
    ap.add_argument("--edits", type=Path, required=True, help="JSON file: {citekey: {field: value}}")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="mirror directory")
    ap.add_argument("--dry-run", action="store_true", help="show the diff, send nothing")
    ap.add_argument("--yes", action="store_true", help="do not ask before sending")
    args = ap.parse_args()

    out = args.out.expanduser()
    try:
        edits = json.loads(args.edits.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        die(f"cannot read {args.edits}: {exc}")
    if not isinstance(edits, dict) or not edits:
        die("the edits file must be a non-empty JSON object keyed by citation key")

    keymap = load_json(mirror_state_dir(out) / "citekeys.json", {})
    if not keymap:
        die(f"no citekeys.json under {out} -- run the mirror once first")
    # This script PATCHes a live Mendeley record, so it may only ever resolve a key
    # to a Mendeley id. A key belonging to another backend is refused by name
    # rather than silently skipped -- pointing an edit at the wrong account is the
    # expensive mistake this namespace exists to prevent.
    by_key = {key: raw for ident, key in keymap.items()
              if (raw := local_id(ident)) is not None}
    foreign = {key for ident, key in keymap.items() if local_id(ident) is None}

    missing = [k for k in edits if k not in by_key]
    wrong_backend = sorted(k for k in missing if k in foreign)
    if wrong_backend:
        die("citation key(s) that do not belong to Mendeley, and this script only "
            "edits Mendeley: " + ", ".join(wrong_backend))
    if missing:
        die("citation key(s) the mirror has never seen: " + ", ".join(sorted(missing)))

    tokens = load_json(config_dir() / "tokens.json", {})
    if not tokens.get("access_token"):
        die("no Mendeley tokens on this machine -- run ./run_mirror.sh once first.")
    client = Mendeley(get_app_config(), tokens)

    planned = []
    for key in sorted(edits):
        doc_id = by_key[key]
        resp = client.get(f"{API}/documents/{doc_id}", accept=DOC_CT, params={"view": "all"})
        if resp.status_code != 200:
            die(f"{key}: could not read the document ({resp.status_code}): {resp.text[:200]}")
        cur = resp.json()
        patch, lines = plan_patch(cur, edits[key])
        title = (cur.get("title") or "")[:66]
        if not patch:
            print(f"  = {key}: already correct, nothing to send")
            continue
        print(f"  - {key}  [{doc_id}]")
        print(f"      {title}")
        print("\n".join(lines))
        planned.append((key, doc_id, patch))

    if not planned:
        print("\nnothing to change.")
        return 0
    print(f"\n{len(planned)} reference(s) would be updated.")

    if args.dry_run:
        print("\n--dry-run: nothing sent.")
        return 0
    if not args.yes:
        reply = input(f"\nSend {len(planned)} update(s) to Mendeley? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("aborted.")
            return 0

    status = 0
    done = []
    for key, doc_id, patch in planned:
        resp = client.session.patch(
            f"{API}/documents/{doc_id}",
            headers={**client._auth_header(), "Content-Type": DOC_CT, "Accept": DOC_CT},
            data=json.dumps(patch),
            timeout=60,
        )
        if resp.status_code == 200:
            done.append(key)
            print(f"  ✓ {key}")
        else:
            print(f"  ! {key}: Mendeley refused it ({resp.status_code}): {resp.text[:300]}")
            status = 1

    # The closing block reports what is still wrong LAST, and says to refresh
    # only if something actually changed -- same rule as inbox.py, and for the
    # same reason: this output is usually read through `| tail`.
    if done:
        print("\nRun ./run_mirror.sh to pull the corrected metadata down.")
    failed = [k for k, _, _ in planned if k not in done]
    if failed:
        print(f"! {len(failed)} NOT updated: {', '.join(failed)}")
        print("  Mendeley still holds the old values for these.")
    return status


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
