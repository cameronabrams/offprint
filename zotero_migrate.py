#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]
# ///
"""
zotero_migrate.py -- map this library's citation keys onto Zotero item keys.

The citation keys are the thing worth protecting. `assign_citekeys` hands a key
to a *document id* once and never changes it, and those keys are cited in
manuscripts and are the file names under `findings/`. Zotero's item keys are not
Mendeley's, so importing the same library into Zotero and refreshing against it
would assign ~2,700 brand new keys: every citation in a draft would rot and every
findings file would orphan.

This script builds the bridge. It reads `library.bib` and `citekeys.json` from
the mirror, reads the items from Zotero (read only), matches them, and reports
what it could not match rather than guessing. With `--write` it adds
`zotero:<itemKey> -> <citekey>` entries to `citekeys.json` alongside the existing
`mendeley:<docid>` ones, so the same paper resolves to the same key from either
backend and no existing entry is touched.

    uv run --script zotero_migrate.py                    # report only
    uv run --script zotero_migrate.py --map map.json     # and save the pairs
    uv run --script zotero_migrate.py --write            # extend citekeys.json

Credentials live in ~/.config/offprint/zotero.json (%LOCALAPPDATA% on Windows):

    { "api_key": "...", "user_id": 1234567 }

A read-only key is enough and is what this wants; nothing here writes to Zotero.
"""

from __future__ import annotations

import argparse
import collections
import difflib
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from mendeley_mirror import DEFAULT_OUT, load_json, mirror_state_dir, save_json
except ImportError:
    sys.exit("zotero_migrate.py must sit in the same folder as mendeley_mirror.py")

import requests

ZOTERO_API = "https://api.zotero.org"
PAGE = 100

# A fuzzy title match is the last resort before giving up, and it only runs
# within a single year. 0.87 sits below the worst real pair seen on this library
# (0.895, a title whose LaTeX \alpha survived on one side and not the other) and
# far above anything unrelated. MARGIN keeps a near-tie from being decided by a
# thousandth: a best score that close to the runner-up is a coin flip, and a coin
# flip is what "report it instead" exists for.
TITLE_FUZZ = 0.87
TITLE_MARGIN = 0.05


# --------------------------------------------------------------------------
# credentials
# --------------------------------------------------------------------------

def zotero_config_dir() -> Path:
    """Where a Zotero key lives, preferring the tool's own name.

    `config_dir()` in mendeley_mirror still answers `mendeley-mirror`, which was
    fine while that was the only backend and became wrong the moment a second
    one's credentials had to go in it. New credentials go to `offprint`; the old
    directory is still read so a machine that has one keeps working. See the
    naming entry in ROADMAP.md.
    """
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    new = base / "offprint"
    if (new / "zotero.json").exists():
        return new
    old = base / "mendeley-mirror"
    if (old / "zotero.json").exists():
        return old
    new.mkdir(parents=True, exist_ok=True)
    return new


def load_zotero_credentials() -> tuple[str, str]:
    path = zotero_config_dir() / "zotero.json"
    if not path.exists():
        sys.exit(
            f"No Zotero credentials at {path}.\n"
            "Create a read-only key at https://www.zotero.org/settings/keys and write:\n"
            '  { "api_key": "...", "user_id": 1234567 }'
        )
    try:
        cfg = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        sys.exit(f"{path} is not valid JSON: {exc}")
    key, uid = cfg.get("api_key"), cfg.get("user_id")
    if not key or not uid:
        sys.exit(f"{path} needs both api_key and user_id.")
    if not str(uid).isdigit():
        sys.exit(
            f"user_id in {path} is {uid!r}, which is not numeric.\n"
            "It is the number on the API-keys page, not your username -- the\n"
            "API addresses libraries by numeric id and rejects the name."
        )
    return str(key), str(uid)


# --------------------------------------------------------------------------
# the read-only slice of the Zotero API this needs
# --------------------------------------------------------------------------

class Zotero:
    """Read-only Zotero Web API v3 client.

    Deliberately small: the backend work needs list-items, list-children and
    fetch-file, and nothing here needs more than the first. It takes no write
    method at all, so a mistake in this script cannot become a mistake in
    someone's library.
    """

    def __init__(self, api_key: str, user_id: str):
        self.base = f"{ZOTERO_API}/users/{user_id}"
        self.session = requests.Session()
        self.session.headers.update({
            "Zotero-API-Version": "3",
            "Zotero-API-Key": api_key,
        })

    def paged(self, path: str, quiet: bool = False, **params) -> list:
        out, start, total = [], 0, None
        while True:
            p = dict(params, limit=PAGE, start=start)
            r = self.session.get(f"{self.base}/{path}", params=p, timeout=60)
            if r.status_code == 403:
                sys.exit("Zotero refused the key (403). Check it is valid and has read access.")
            r.raise_for_status()
            if total is None:
                total = int(r.headers.get("Total-Results", 0))
            rows = r.json()
            if not rows:
                break
            out.extend(rows)
            start += len(rows)
            if not quiet:
                print(f"\r  {path}: {start}/{total}", end="", file=sys.stderr, flush=True)
            if start >= total:
                break
            time.sleep(0.05)
        if not quiet and total:
            print(file=sys.stderr)
        return out

    def items_top(self) -> list:
        return self.paged("items/top")


# --------------------------------------------------------------------------
# normalizing the two sides so they can be compared at all
# --------------------------------------------------------------------------

_ENTITY = re.compile(r"&#?\w+;")
_LATEX = re.compile(r"\\[a-zA-Z]+\{?|\$|\{|\}|\\")
_NONWORD = re.compile(r"[^0-9a-z]+")
_YEAR = re.compile(r"(1[6-9]\d{2}|20\d{2})")


def norm_doi(s: str | None) -> str:
    if not s:
        return ""
    s = s.strip().lower()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    s = re.sub(r"^doi:\s*", "", s)
    return s.rstrip(" .;,")


def norm_title(s: str | None) -> str:
    """Flatten a title to comparable words.

    Entities go FIRST and this is not cosmetic. Zotero's Mendeley import writes
    `&#8208;` where the BibTeX carries a plain hyphen; strip non-word characters
    before entities and the digits survive as the token `8208`, which makes two
    identical titles differ in three places. That alone accounted for half the
    unmatched records on the first run of this over the live library.
    """
    if not s:
        return ""
    s = _ENTITY.sub(" ", s)
    s = _LATEX.sub(" ", s)
    s = _NONWORD.sub(" ", s.lower())
    return " ".join(s.split())


def year_of(s: str | None) -> str:
    m = _YEAR.search(s or "")
    return m.group(1) if m else ""


# --------------------------------------------------------------------------
# reading the mirror's own BibTeX
# --------------------------------------------------------------------------

_ENTRY = re.compile(r"^@(\w+)\{([^,\n]+),\n", re.M)
_FIELD = re.compile(r"^\s{2}(\w+)\s*=\s*\{", re.M)


def _balanced(text: str, start: int) -> tuple[str, int]:
    """Return the text up to the brace that closes the one just opened."""
    depth, i = 1, start
    while i < len(text) and depth:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    return text[start:i - 1], i


def parse_bib(text: str) -> list[tuple[str, str, dict]]:
    """Read the mirror's own generated BibTeX.

    Not a general BibTeX parser and does not want to be: it reads what
    `write_bibtex` writes, where every field is `  name = {value},` and the only
    hard part is that titles are brace-protected and abstracts contain braces.
    Both are handled by counting, which is why this does not use a regex for the
    value.
    """
    out = []
    for m in _ENTRY.finditer(text):
        body, _ = _balanced(text, m.end())
        fields = {}
        for fm in _FIELD.finditer(body):
            value, _ = _balanced(body, fm.end())
            fields[fm.group(1)] = value
        out.append((m.group(2).strip(), m.group(1), fields))
    return out


# --------------------------------------------------------------------------
# the matching itself
# --------------------------------------------------------------------------

Row = collections.namedtuple("Row", "ident doi title year raw")


def _index(rows, keyfn, taken):
    d = collections.defaultdict(list)
    for r in rows:
        if r.ident in taken:
            continue
        k = keyfn(r)
        if k:
            d[k].append(r)
    return d


def match(mendeley: list[Row], zotero: list[Row]) -> tuple[dict, list, list, collections.Counter]:
    """Pair mirror citation keys with Zotero item keys, most-confident first.

    Each pass consumes what it resolves, so a weaker rule never gets to overrule
    a stronger one, and a Zotero item is claimed at most once. Ambiguity is not
    an error here -- it falls through to the next pass, and what survives every
    pass is reported rather than guessed at.
    """
    pairs: dict[str, str] = {}
    taken: set[str] = set()
    stats: collections.Counter = collections.Counter()
    todo = list(mendeley)

    passes = [
        ("doi", lambda r: r.doi),
        ("title+year", lambda r: (r.title, r.year) if r.title and r.year else None),
        ("title", lambda r: r.title or None),
    ]
    for name, keyfn in passes:
        idx = _index(zotero, keyfn, taken)
        # Both sides have to be unambiguous, not just Zotero's. Checking only the
        # candidate side lets two mirror records that share a title race for one
        # Zotero item, and the winner is whichever the loop reached first -- an
        # arbitrary choice made silently inside a pass whose whole claim is that
        # it is exact. Duplicates belong to the counted pass at the end.
        mine = collections.Counter(k for k in (keyfn(m) for m in todo) if k)
        still = []
        for m in todo:
            k = keyfn(m)
            cands = [c for c in idx.get(k, []) if c.ident not in taken] if k else []
            if len(cands) == 1 and mine[k] == 1:
                pairs[m.ident] = cands[0].ident
                taken.add(cands[0].ident)
                stats[name] += 1
            else:
                still.append(m)
        todo = still

    # Fuzzy, within a year, and only where both sides are down to one candidate
    # for that title. A near-tie is left alone: see TITLE_MARGIN.
    by_year = collections.defaultdict(list)
    for z in zotero:
        if z.ident not in taken:
            by_year[z.year].append(z)
    # Same rule as the exact passes, and it bites harder here: two mirror records
    # with the same title both score 1.0 against the one Zotero item left, and
    # whichever the loop reaches first would take it while the other is reported
    # missing. That reads as "one copy was lost" when the truth is "two copies
    # became one". Duplicates go to the counted pass below, which says so.
    dup = collections.Counter((m.title, m.year) for m in todo)
    still = []
    for m in todo:
        if dup[(m.title, m.year)] > 1:
            still.append(m)
            continue
        best = second = 0.0
        pick = None
        for z in by_year.get(m.year, []):
            if z.ident in taken:
                continue
            r = difflib.SequenceMatcher(None, m.title, z.title).ratio()
            if r > best:
                best, second, pick = r, best, z
            elif r > second:
                second = r
        if pick and best >= TITLE_FUZZ and (best - second) >= TITLE_MARGIN:
            pairs[m.ident] = pick.ident
            taken.add(pick.ident)
            stats["fuzzy title"] += 1
        else:
            still.append(m)
    todo = still

    # What is left is duplicate records: the same paper filed two or three times,
    # with no DOI to tell the copies apart. Where both sides hold the same number
    # of copies they are interchangeable by construction -- identical title, year
    # and authors, nothing to choose between them -- so pair them in a stable
    # order. Where the counts differ, something is actually missing, and that is
    # reported.
    mg = collections.defaultdict(list)
    for m in todo:
        mg[(m.title, m.year)].append(m)
    zg = collections.defaultdict(list)
    for z in zotero:
        if z.ident not in taken:
            zg[(z.title, z.year)].append(z)
    still = []
    for k, ms in mg.items():
        zs = zg.get(k, [])
        if len(ms) == len(zs) and ms:
            for m, z in zip(sorted(ms, key=lambda r: r.ident), sorted(zs, key=lambda r: r.ident)):
                pairs[m.ident] = z.ident
                taken.add(z.ident)
                stats["duplicate group"] += 1
        else:
            still.extend(ms)
    todo = still

    unclaimed = [z for z in zotero if z.ident not in taken]
    return pairs, todo, unclaimed, stats


# --------------------------------------------------------------------------

def rows_from_bib(path: Path) -> list[Row]:
    if not path.exists():
        sys.exit(f"No library.bib at {path} -- point --out at the mirror.")
    return [
        Row(key, norm_doi(f.get("doi")), norm_title(f.get("title")),
            year_of(f.get("year")), f.get("title", ""))
        for key, _kind, f in parse_bib(path.read_text(encoding="utf-8"))
    ]


def rows_from_zotero(items: list) -> list[Row]:
    return [
        Row(it["key"], norm_doi(it["data"].get("DOI")),
            norm_title(it["data"].get("title")),
            year_of(it["data"].get("date")), it["data"].get("title", ""))
        for it in items
    ]


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Map the mirror's citation keys onto Zotero item keys.")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT,
                    help=f"mirror directory (default: {DEFAULT_OUT})")
    ap.add_argument("--map", type=Path,
                    help="write the citekey -> zotero-key pairs here as JSON")
    ap.add_argument("--write", action="store_true",
                    help="add zotero:<key> entries to the mirror's citekeys.json")
    ap.add_argument("--items", type=Path,
                    help="read Zotero items from this JSON file instead of the API")
    args = ap.parse_args()

    out = args.out.expanduser()
    if args.items:
        items = json.loads(args.items.read_text())
    else:
        items = Zotero(*load_zotero_credentials()).items_top()

    mrows = rows_from_bib(out / "library.bib")
    zrows = rows_from_zotero(items)
    pairs, unmatched, unclaimed, stats = match(mrows, zrows)

    print(f"mirror references : {len(mrows)}")
    print(f"zotero items      : {len(zrows)}")
    print()
    for name, n in stats.most_common():
        print(f"  matched by {name:18s} {n}")
    print(f"  {'TOTAL matched':29s} {len(pairs)}")
    print(f"  {'unmatched (mirror)':29s} {len(unmatched)}")
    print(f"  {'unclaimed (zotero)':29s} {len(unclaimed)}")

    if unmatched:
        print("\nUnmatched mirror references -- these keep their Mendeley id and")
        print("will not resolve from Zotero until they are paired by hand:")
        for m in unmatched:
            print(f"  {m.ident:34s} {m.year}  {m.raw[:60]}")
    if unclaimed:
        print("\nZotero items nothing claimed:")
        for z in unclaimed:
            print(f"  {z.ident:34s} {z.year}  {z.raw[:60]}")

    if args.map:
        args.map.write_text(json.dumps(pairs, indent=1, sort_keys=True) + "\n")
        print(f"\nwrote {args.map}")

    if args.write:
        state = mirror_state_dir(out)
        keymap = load_json(state / "citekeys.json", {})
        added = 0
        for citekey, zkey in pairs.items():
            ident = f"zotero:{zkey}"
            if keymap.get(ident) == citekey:
                continue
            if ident in keymap:
                sys.exit(f"{ident} already maps to {keymap[ident]!r}, not {citekey!r} -- "
                         "refusing to rewrite an existing entry.")
            keymap[ident] = citekey
            added += 1
        save_json(state / "citekeys.json", keymap)
        print(f"\nadded {added} zotero: entries to {state / 'citekeys.json'}")


if __name__ == "__main__":
    main()
