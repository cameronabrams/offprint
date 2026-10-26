#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]   # transitive: mendeley_mirror imports it for DEFAULT_OUT
# ///
"""
whatbreaks.py -- what breaks if a paper is wrong.

`citations/` answers which papers cite which. This answers the question no
citation graph can: what has actually been *concluded* here, what each
conclusion rests on, and **what role each source plays** in carrying it.

    uv run --script whatbreaks.py Snow1991Dilatometry   what rests on it
    uv run --script whatbreaks.py --thin                claims on a single source
    uv run --script whatbreaks.py --load                papers ranked by what they carry
    uv run --script whatbreaks.py --orphans             claims carried by nothing
    uv run --script whatbreaks.py --check               every citekey against library.bib
    uv run --script whatbreaks.py --render              rewrite claims.md

    uv run --script whatbreaks.py --add tg-ranking --status open \\
        --statement "..." --note dicyanate-tg-by-bridge
    uv run --script whatbreaks.py --link tg-ranking --key FANG1995Polycyanate \\
        --role primary --why "Fig 22 p.95, the only single source covering most bridges"
    uv run --script whatbreaks.py --set-status tg-ranking --status superseded

The ledger is `<out>/claims.json`, beside `library.bib` and `findings/`. It came
from the library session, which built and still owns its contents; this repo owns
the tool only. **The data does not travel with the code** -- `claims.json` is a
judgment about one person's literature and changes when a paper changes his mind,
not when this repo cuts a release.

**Role weights live in the ledger, not here.** How much a `primary` carries
relative to a `corroborating` is a judgment about this library. `FALLBACK` below
is used only when the file omits `weights`, and a run that falls back says so --
scoring with a different table than the ledger declares is exactly the kind of
quiet substitution the counting rules here exist to prevent.

**Every count says what it is a count OF.** `--orphans` reported claims with an
empty `sources` list, found 2, when 4 claims rested on nothing: two more carried
`context` links only, which is resting on nothing in precisely the same way. The
number was smaller than the truth and nothing in the output said so. The same
shape is why the ranked listings print "top 25 of 91" rather than 25 rows, and
why "what would be left: NOTHING" counts the non-evidential links it is
discarding instead of letting them vanish.

**A statement is never rewritten in place.** The sources on a claim vouch for the
words that were there when they were linked, so editing the words under a fixed
id silently moves what they vouched for. The ledger's own status vocabulary
already names the right move -- `superseded`, "replaced by a later claim" -- so
restating is `--set-status <id> --status superseded` plus a fresh `--add`. Hand
editing the JSON is still available and still fine; the tool simply will not do
it in one step and call it an edit.

**`--check` proves a citekey resolves in `library.bib`. It does not prove the key
is the paper meant** -- a typo landing on a real key passes. `--titles` prints
every linked key with its title and year so a person can read them, which is how
the first 91 were verified and is the only way that check gets made.

This script never touches Zotero and never leaves `<out>`.
"""

from __future__ import annotations

import argparse
import collections
import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    from mendeley_mirror import DEFAULT_OUT, __version__
except ImportError as exc:  # pragma: no cover
    sys.exit(f"whatbreaks.py must sit beside the other mirror scripts ({exc})")

# Used only when the ledger omits `weights`; a run that reaches it says so.
FALLBACK = {"sole": 1.0, "primary": 0.6, "contradicting": 0.4,
            "corroborating": 0.2, "method": 0.3, "context": 0.0}

# The roles that CARRY a claim: if all of these go, the claim goes. `context`
# and `method` support the reasoning without standing under the conclusion, and
# `contradicting` is evidence against it.
CARRYING = ("sole", "primary")
EVIDENTIAL = ("sole", "primary", "corroborating")

TOP_DEFAULT = 25
RENDER_TOP = 15


def die(msg: str):
    sys.exit(f"error: {msg}")


def ledger_path(out: Path) -> Path:
    return out / "claims.json"


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------

def validate(d, where: str) -> list[str]:
    """Every problem, not the first one.

    A hand-edited ledger is the normal case -- that is how claims are added --
    so drift is not an exceptional event to crash on, it is the thing to
    report completely. Returning early on the first bad claim would send
    someone round the loop once per typo.
    """
    bad: list[str] = []
    if not isinstance(d, dict):
        return [f"{where} is not a JSON object"]
    for field in ("roles", "statuses", "claims"):
        if field not in d:
            bad.append(f"{where} has no '{field}'")
    if bad:
        return bad

    weights = d.get("weights") or FALLBACK
    for r in d["roles"]:
        if r not in weights:
            bad.append(f"role '{r}' is documented but has no weight")
    for r in weights:
        if r not in d["roles"]:
            bad.append(f"role '{r}' has a weight but is not documented in 'roles'")

    seen: set[str] = set()
    for i, c in enumerate(d["claims"]):
        at = c.get("id") or f"claim #{i + 1}"
        if not isinstance(c, dict):
            bad.append(f"{at}: not an object")
            continue
        for field in ("id", "statement", "status", "sources"):
            if field not in c:
                bad.append(f"{at}: no '{field}'")
        if "id" in c:
            if c["id"] in seen:
                bad.append(f"{at}: duplicate id")
            seen.add(c["id"])
        if "status" in c and c["status"] not in d["statuses"]:
            bad.append(f"{at}: status '{c['status']}' is not one of "
                       f"{', '.join(sorted(d['statuses']))}")
        for s in c.get("sources", []):
            if not isinstance(s, dict) or not {"k", "role", "why"} <= set(s):
                bad.append(f"{at}: a source is missing k/role/why")
                continue
            if s["role"] not in weights:
                bad.append(f"{at}: source {s['k']} has unknown role '{s['role']}'")
    return bad


def doc(out: Path) -> dict:
    path = ledger_path(out)
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        die(f"no ledger at {path}")
    except ValueError as exc:
        die(f"{path} is not readable JSON: {exc}")
    bad = validate(d, path.name)
    if bad:
        die(f"{path} does not match the ledger schema:\n  "
            + "\n  ".join(bad))
    return d


def carry(d: dict) -> tuple[dict, bool]:
    """Weights, and whether they came from the ledger or from FALLBACK."""
    w = d.get("weights")
    return (w, True) if w else (FALLBACK, False)


def say_weights(from_file: bool):
    if not from_file:
        print("note: the ledger declares no 'weights'; scoring with this "
              "script's FALLBACK table, which may not be the judgment you "
              "meant.\n")


def by_source(claims) -> dict:
    idx = collections.defaultdict(list)
    for c in claims:
        for s in c["sources"]:
            idx[s["k"]].append((c, s))
    return idx


def report(d: dict, keys):
    claims = d["claims"]
    idx = by_source(claims)
    CARRY, from_file = carry(d)
    say_weights(from_file)
    for k in keys:
        hits = idx.get(k)
        print(f"\n=== {k} ===")
        if not hits:
            print("  nothing in the ledger rests on it.")
            print("  That is not evidence it is sound -- only that nobody has "
                  "leaned on it yet.")
            continue
        sole = [c for c, s in hits if s["role"] == "sole"]
        prim = [c for c, s in hits if s["role"] == "primary"]
        print(f"  {len(hits)} claim(s); {len(sole)} would be GONE, "
              f"{len(prim)} would lose their primary support")
        for c, s in sorted(hits, key=lambda h: -CARRY[h[1]["role"]]):
            mark = {"sole": "!!", "primary": " !",
                    "contradicting": " ~"}.get(s["role"], "  ")
            print(f'\n  {mark} [{s["role"]}] {c["id"]}   ({c["status"]})')
            print(f'       {c["statement"]}')
            print(f'       role here: {s["why"]}')
            print(f"       what would be left: {remainder(c, k)}")


def remainder(c: dict, k: str) -> str:
    """What still supports the claim once `k` is withdrawn.

    Only evidential roles count, which means a claim left with nothing but a
    `method` or `context` link really is left with nothing. But printing a
    bare NOTHING there drops links that exist, and a reader who then opens the
    ledger finds them and has to work out which answer was wrong. So the
    discarded links are counted in place.
    """
    others = [o for o in c["sources"] if o["k"] != k]
    kept = [o["k"] for o in others if o["role"] in EVIDENTIAL]
    if kept:
        return ", ".join(kept)
    rest = collections.Counter(o["role"] for o in others)
    if not rest:
        return "NOTHING"
    detail = ", ".join(f"{n} {r}" for r, n in sorted(rest.items()))
    return f"NOTHING evidential ({detail} link(s) remain)"


def thin_rows(claims):
    out = []
    for c in claims:
        carriers = [s for s in c["sources"] if s["role"] in CARRYING]
        if len(carriers) <= 1:
            out.append((c, carriers))
    return out


def thin(d: dict):
    claims = d["claims"]
    out = thin_rows(claims)
    print(f"{len(out)} of {len(claims)} claims stand on one carrying source "
          "or none:\n")
    for c, carriers in sorted(out, key=lambda x: (len(x[1]), x[0]["status"])):
        who = carriers[0]["k"] if carriers else \
            "(NO CARRYING SOURCE -- see --orphans)"
        print(f'  [{c["status"]:11s}] {c["id"]:34s} <- {who}')


def rank(d: dict):
    CARRY, _ = carry(d)
    idx = by_source(d["claims"])
    rows = []
    for k, hits in idx.items():
        w = sum(CARRY[s["role"]] for _, s in hits)
        rows.append((w, len(hits),
                     sum(1 for _, s in hits if s["role"] == "sole"), k))
    return sorted(rows, reverse=True)


def load_rank(d: dict, top: int | None):
    rows = rank(d)
    say_weights(carry(d)[1])
    shown = rows if top is None else rows[:top]
    if len(shown) < len(rows):
        print(f"top {len(shown)} of {len(rows)} papers "
              f"(--all for every one)\n")
    else:
        print(f"all {len(rows)} papers\n")
    print(f'{"weight":>7} {"claims":>7} {"sole":>5}  paper')
    for w, n, so, k in shown:
        print(f"{w:7.1f} {n:7d} {so:5d}  {k}")


def orphan_groups(claims):
    """Claims with no link at all, and claims with no CARRYING link.

    These were one group, reported as the first, and the count came out 2 when
    the truth was 4: two claims carry a `context` cite apiece and nothing else,
    which is resting on nothing in exactly the way an empty list is. Keeping
    them separate is the point -- they are different facts about a claim -- but
    neither may be the only one printed.
    """
    bare, ctx = [], []
    for c in claims:
        if not c["sources"]:
            bare.append(c)
        elif not [s for s in c["sources"] if s["role"] in CARRYING]:
            ctx.append(c)
    return bare, ctx


def orphans(d: dict):
    bare, ctx = orphan_groups(d["claims"])
    print(f"{len(bare) + len(ctx)} of {len(d['claims'])} claims rest on no "
          "carrying source, in two different ways:")
    for head, group in (("no source at all", bare),
                        ("no CARRYING source (context/corroboration only)",
                         ctx)):
        print(f"\n-- {len(group)} claim(s) with {head} --\n")
        for c in group:
            print(f'  [{c["status"]}] {c["id"]}\n       {c["statement"]}')
            for s in c["sources"]:
                print(f'         ({s["role"]}) {s["k"]}')
            print()


BIB_ENTRY = re.compile(r"@\w+\{([^,]+),")


def bib_index(out: Path) -> dict:
    """citekey -> (year, title), read straight out of library.bib.

    Parsed rather than imported because `library.bib` is the artifact other
    people's bibliographies are built on, and what it literally says is the
    question being asked.
    """
    path = out / "library.bib"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        die(f"cannot read {path}: {exc}")
    idx = {}
    for m in BIB_ENTRY.finditer(text):
        end = text.find("\n@", m.end())
        body = text[m.end():end if end != -1 else len(text)]
        fields = {}
        for f in ("title", "year"):
            fm = re.search(rf"^\s*{f}\s*=\s*[{{\"](.*?)[}}\"],?\s*$",
                           body, re.M | re.S)
            if fm:
                fields[f] = re.sub(r"[{}]", "", fm.group(1)).strip()
        idx[m.group(1)] = (fields.get("year", "????"),
                           fields.get("title", "(no title)"))
    return idx


def check(d: dict, out: Path, titles: bool):
    idx = bib_index(out)
    claims = d["claims"]
    links = by_source(claims)
    bad = collections.defaultdict(list)
    for c in claims:
        for s in c["sources"]:
            if s["k"] not in idx:
                bad[s["k"]].append(c["id"])
    n = sum(len(c["sources"]) for c in claims)
    print(f"{len(claims)} claims, {n} source links, {len(links)} distinct "
          "papers")
    if not bad:
        print("all citekeys resolve in library.bib")
    else:
        print(f"{len(bad)} citekey(s) NOT in library.bib:")
        for k, v in sorted(bad.items()):
            print(f'  {k}  ({", ".join(v)})')
    print("\nResolving is not being right: a typo that lands on another real "
          "key passes this\ncheck. --titles prints what each key actually "
          "names, which is the only way that\ngets verified.")
    if titles:
        print()
        for k in sorted(links):
            year, title = idx.get(k, ("????", "NOT IN library.bib"))
            print(f"  {k:36s} {year:>6}  {title}")


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------

def save(out: Path, d: dict):
    """Atomic, mode-preserving, and byte-stable for an unchanged ledger.

    `indent=1, ensure_ascii=False` is what the ledger is already written with,
    so a one-field edit is a one-field diff rather than a 60 KB reflow -- which
    matters because hand editing is a supported way to use this file. The
    rename is atomic so a crash mid-write cannot leave a ledger that no mode
    can read.
    """
    path = ledger_path(out)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=1, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    if os.name != "nt":
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass
    tmp.replace(path)


def find_claim(d: dict, cid: str) -> dict:
    for c in d["claims"]:
        if c["id"] == cid:
            return c
    die(f"no claim '{cid}' in the ledger "
        f"(ids are listed by --thin and --orphans)")


ID_OK = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*$")


def do_add(d: dict, out: Path, args):
    if not args.statement:
        die("--add needs --statement")
    if not args.status:
        die("--add needs --status")
    if not ID_OK.fullmatch(args.add):
        die(f"'{args.add}' is not a lowercase-kebab id "
            "(the ledger's ids are its only handles; keep them greppable)")
    if any(c["id"] == args.add for c in d["claims"]):
        die(f"claim '{args.add}' already exists; "
            "restate by superseding it, not by overwriting it")
    if args.status not in d["statuses"]:
        die(f"status '{args.status}' is not one of "
            f"{', '.join(sorted(d['statuses']))}")
    d["claims"].append({"id": args.add, "statement": args.statement,
                        "status": args.status, "note": args.note or "",
                        "sources": []})
    d["built"] = _dt.date.today().isoformat()
    save(out, d)
    print(f"added {args.add} [{args.status}] with no sources. "
          "It will show in --orphans until something is linked to it.")


def do_link(d: dict, out: Path, args):
    c = find_claim(d, args.link)
    if not args.key or not args.role or not args.why:
        die("--link needs --key, --role and --why")
    CARRY, _ = carry(d)
    if args.role not in CARRY:
        die(f"role '{args.role}' is not one of {', '.join(sorted(CARRY))}")
    if any(s["k"] == args.key for s in c["sources"]):
        die(f"{args.key} is already linked to {args.link}; "
            "--unlink first if the role is what changed")
    idx = bib_index(out)
    if args.key not in idx:
        die(f"{args.key} is not in library.bib")
    year, title = idx[args.key]
    c["sources"].append({"k": args.key, "role": args.role, "why": args.why})
    d["built"] = _dt.date.today().isoformat()
    save(out, d)
    # The citekey resolved, which is all the tool can check. Printing what it
    # names is what lets the person notice they linked the wrong paper, now,
    # while they still remember which one they meant.
    print(f"linked {args.key} to {args.link} as {args.role}")
    print(f"  that key is: {year}  {title}")


def do_unlink(d: dict, out: Path, args):
    c = find_claim(d, args.unlink)
    if not args.key:
        die("--unlink needs --key")
    keep = [s for s in c["sources"] if s["k"] != args.key]
    if len(keep) == len(c["sources"]):
        die(f"{args.key} is not linked to {args.unlink}")
    c["sources"] = keep
    d["built"] = _dt.date.today().isoformat()
    save(out, d)
    print(f"unlinked {args.key} from {args.unlink}; "
          f"what is left: {remainder(c, args.key)}")


def do_set_status(d: dict, out: Path, args):
    c = find_claim(d, args.set_status)
    if not args.status:
        die("--set-status needs --status")
    if args.status not in d["statuses"]:
        die(f"status '{args.status}' is not one of "
            f"{', '.join(sorted(d['statuses']))}")
    was = c["status"]
    c["status"] = args.status
    d["built"] = _dt.date.today().isoformat()
    save(out, d)
    print(f"{args.set_status}: {was} -> {args.status}")


# --------------------------------------------------------------------------
# render
# --------------------------------------------------------------------------

def render_text(d: dict) -> str:
    claims = d["claims"]
    CARRY, from_file = carry(d)
    npap = len(by_source(claims))
    nlink = sum(len(c["sources"]) for c in claims)
    stat = collections.Counter(c["status"] for c in claims)
    bare, ctx = orphan_groups(claims)
    rows = rank(d)
    L = ["# Claim ledger\n"]
    L.append(f'Built {d["built"]} · {len(claims)} claims · {nlink} source '
             f"links · {npap} papers\n")
    L.append(f"*Generated from `claims.json` by `whatbreaks.py --render` "
             f"(offprint {__version__}). Edit the JSON, not this file.*\n")
    L.append(d.get("scope", "") + "\n")
    L.append("## Why not the citation graph\n")
    # Read from the ledger, not written here. The paragraph this replaced was
    # a good one and carried four measured numbers -- edge count, a leading
    # in-degree, two percentages, all dated. Hardcoded in a renderer they would
    # have gone stale on the next refresh with nothing to say they had, and the
    # person who can re-measure them is the one who maintains the ledger.
    L.append(d.get("why_not_citations") or
             "`citations/` records which papers cite which, and that answers "
             "a different question. In-degree measures popularity among the "
             "papers that happen to be collected here; a paper can carry a "
             "conclusion outright and be cited once. What a role is — and "
             "therefore what breaks — is not in the edges.")
    L.append("")
    L.append("## Roles\n")
    L.append("| role | meaning | weight |\n|---|---|---|")
    for r, m in d["roles"].items():
        L.append(f"| `{r}` | {m} | {CARRY[r]} |")
    L.append("\nWeights are how much a role carries if its source falls. They "
             "live in `claims.json`, not in the tool"
             + ("" if from_file else
                " — **and this file declares none, so the table above is the "
                "tool's fallback, not a judgment about this library**")
             + ".\n")
    L.append("## Status\n")
    L.append("| status | meaning | n |\n|---|---|---|")
    for s, m in d["statuses"].items():
        L.append(f"| `{s}` | {m} | {stat.get(s, 0)} |")
    L.append("")
    L.append(f"**{len(thin_rows(claims))} of {len(claims)} claims stand on one "
             f"carrying source or none**, and {len(bare) + len(ctx)} stand on "
             f"no carrying source at all ({len(bare)} with no link whatever, "
             f"{len(ctx)} with only context or corroboration).\n")
    L.append("## Papers by what they carry\n")
    L.append(f"Top {min(RENDER_TOP, len(rows))} of {len(rows)}; "
             "`whatbreaks.py --load --all` prints every one.\n")
    L.append("| weight | claims | sole | paper |\n|---:|---:|---:|---|")
    for w, n, so, k in rows[:RENDER_TOP]:
        L.append(f"| {w:.1f} | {n} | {so} | `{k}` |")
    L.append("")
    L.append("## Claims\n")
    for c in sorted(claims, key=lambda c: c["id"]):
        L.append(f'### `{c["id"]}` — {c["status"]}\n')
        L.append(c["statement"] + "\n")
        if c.get("note"):
            L.append(f'*from* `{c["note"]}`\n')
        if c["sources"]:
            L.append("| source | role | why |\n|---|---|---|")
            for s in sorted(c["sources"], key=lambda s: -CARRY[s["role"]]):
                L.append(f'| `{s["k"]}` | {s["role"]} | {s["why"]} |')
        else:
            L.append("*No source. This is reasoning, arithmetic, or a negative "
                     "established by enumeration — see the statement.*")
        L.append("")
    return "\n".join(L)


def render(d: dict, out: Path):
    path = out / "claims.md"
    path.write_text(render_text(d), encoding="utf-8")
    print(f'wrote {path}  ({len(d["claims"])} claims)')
    # The rendered file is overwritten whole, so prose that was in the old one
    # and is not in the ledger is gone, and the replacement reads as if it had
    # always been there. Say which paragraph that was.
    if not d.get("why_not_citations"):
        print("note: the ledger declares no 'why_not_citations', so the "
              "section of that name is this script's generic paragraph. If "
              "the file you just overwrote had measured numbers in it, they "
              "belong in claims.json, where they can be re-measured.")
    if not d.get("scope"):
        print("note: the ledger declares no 'scope', so the file says nothing "
              "about what ground it covers.")


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="What breaks if a paper is wrong.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("keys", nargs="*", help="citekeys to report on")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT,
                    help="mirror directory")
    ap.add_argument("--thin", action="store_true",
                    help="claims standing on one carrying source or none")
    ap.add_argument("--load", action="store_true",
                    help="papers ranked by what they carry")
    ap.add_argument("--all", action="store_true",
                    help="with --load, print every paper rather than the top "
                         f"{TOP_DEFAULT}")
    ap.add_argument("--orphans", action="store_true",
                    help="claims with no carrying source, in two groups")
    ap.add_argument("--check", action="store_true",
                    help="every citekey against library.bib")
    ap.add_argument("--titles", action="store_true",
                    help="with --check, print what each key names")
    ap.add_argument("--render", action="store_true",
                    help="rewrite <out>/claims.md from the ledger")
    ap.add_argument("--add", metavar="ID", help="add a claim (needs "
                                                "--statement and --status)")
    ap.add_argument("--link", metavar="ID", help="link a source to a claim "
                                                 "(needs --key --role --why)")
    ap.add_argument("--unlink", metavar="ID", help="remove a source from a "
                                                   "claim (needs --key)")
    ap.add_argument("--set-status", metavar="ID", help="change a claim's "
                                                       "status (needs --status)")
    ap.add_argument("--statement", help="the claim, in one sentence or two")
    ap.add_argument("--status", help="one of the ledger's own statuses")
    ap.add_argument("--note", help="the memory note or finding it came from")
    ap.add_argument("--key", help="citekey, for --link/--unlink")
    ap.add_argument("--role", help="one of the ledger's own roles")
    ap.add_argument("--why", help="what this source does for this claim")
    ap.add_argument("--version", action="version",
                    version=f"offprint {__version__}")
    args = ap.parse_args()

    writes = [a for a in (args.add, args.link, args.unlink, args.set_status)
              if a]
    if len(writes) > 1:
        die("one write at a time")

    d = doc(args.out)

    if args.add:
        return do_add(d, args.out, args)
    if args.link:
        return do_link(d, args.out, args)
    if args.unlink:
        return do_unlink(d, args.out, args)
    if args.set_status:
        return do_set_status(d, args.out, args)

    if args.thin:
        return thin(d)
    if args.load:
        return load_rank(d, None if args.all else TOP_DEFAULT)
    if args.orphans:
        return orphans(d)
    if args.check:
        return check(d, args.out, args.titles)
    if args.render:
        return render(d, args.out)
    if args.keys:
        return report(d, args.keys)
    ap.print_help()


if __name__ == "__main__":
    main()
