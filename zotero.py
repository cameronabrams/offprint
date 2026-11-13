#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]
# ///
"""
zotero.py -- the read-only Zotero client, and the credentials it needs.

Not a CLI. Every script here that reads Zotero goes through this one class, and
**it is read-only on purpose**: `zotero_attach.py` holds the write client, so
that adding a write method here cannot quietly retire a guarantee half the repo
is relying on.

Credentials live in ~/.config/offprint/zotero.json (%LOCALAPPDATA% on Windows):

    { "api_key": "...", "user_id": 1234567 }

It also holds the small readers that several callers share: `parse_bib` for
`library.bib`, and the `norm_doi` / `year_of` normalizers that decide whether
two records are the same paper.

Until 2026-11-13 this file was `zotero.py` and also carried the one-time
pairing that gave every record in the library a `zotero:<itemKey>` citekeys
entry beside the id it already had. That pairing completed on 2026-10-02, its
result is durable in `.mirror/citekeys.json`, and the backend it paired against
no longer exists -- so the code was removed rather than kept as a path nobody
can exercise.
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
    from mirror import DEFAULT_OUT, load_json, mirror_state_dir, save_json
except ImportError:
    sys.exit("zotero.py must sit in the same folder as mirror.py")

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
    """Where a Zotero key lives: `<config>/offprint/zotero.json`.

    Credentials are per-machine on purpose. A public repo and a synced library
    both stay free of them, which is why this is the one thing in the tool that
    does not travel with `<out>/.mirror/`.

    A second location was read alongside this one until 2026-11-13, left over
    from when the credential directory was named after the first backend. No
    machine had a key there, and a fallback nobody uses is a path nobody tests.
    """
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    d = base / "offprint"
    d.mkdir(parents=True, exist_ok=True)
    return d


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







# --------------------------------------------------------------------------







