# Correcting a reference

The one script that can destroy correct metadata rather than merely add wrong
metadata, and the properties that keep that from happening by accident.

## `zotero_edit.py`

It PATCHes fields on records that are already there. That makes it the most
invasive of the writers, because it changes metadata rather than adding
something new — so it is `--dry-run` first, always.

```
uv run --script zotero_edit.py --edits fixes.json --dry-run   # read the diff
uv run --script zotero_edit.py --edits fixes.json             # asks first
uv run --script zotero_edit.py --edits fixes.json --yes       # already approved
```

The edits file is JSON keyed by **citation key**, not by Zotero's item keys, so
you name records the way the rest of the mirror does:

```json
{
  "Huang2016Charmm": {"date": "2017"},
  "Bennett2023Microsecond": {
    "itemType": "journalArticle", "publicationTitle": "Science Advances",
    "date": "2024", "pages": "eadj0396", "DOI": "10.1126/sciadv.adj0396"
  }
}
```

Keys resolve through `.mirror/citekeys.json`, so a key the mirror has never seen
is an error rather than a silent no-op. A value already equal to Zotero's is
skipped rather than re-sent, so re-running after a partial failure is safe.

## The field names are Zotero's, not the mirror's

This is the part that catches people, including this repo on 2026-10-02.
`library.bib` and `index.md` are written from the mirror's own document shape —
`authors`, `source`, `year`, `identifiers`, people as
`first_name`/`last_name` — so anyone composing an edit from what they just read
reaches for those names first. Zotero calls them `creators`,
`publicationTitle`, `date`, and separate top-level `DOI`/`ISSN`/`ISBN`, with
people as `firstName`/`lastName`.

**Every one of those is refused by name, with the Zotero field named in the
error.** Guessing the translation would be guessing at what someone meant about
their own library, and an unknown field name is an error for the same reason: a
name that is not already a key of the record is a typo, and a typo that reached
Zotero would be ignored server-side — a silent no-op wearing the clothes of a
successful edit.

## The dangerous field is `creators`

Zotero's PATCH is a partial merge at the top level — "Properties not included in
the uploaded JSON are left untouched on the server" — so sending a DOI cannot
disturb the ISSN beside it, and scalar fields need no protection at all.

That guarantee stops one level down. `creators` is a **list**, and sending one
replaces the whole list, editors and translators included. So the single most
likely edit anyone makes, fixing an author list, is also the one that would
silently drop an editor. **An edit therefore replaces only the creator types it
mentions and preserves the ones it does not.** A list of authors replaces the
authors and keeps the editor; an entry with no `creatorType` is an author.

Order is the payload, not an accident: records in this library have their
authors in the wrong order, so the order you write is the order sent.

**An explicit `null` clears a field** — an empty string for a scalar, an empty
list for `creators`:

```json
{"Pecina1978Observation": {"DOI": null}}
```

A merge that protects good data also makes bad data undeletable, and a DOI that
resolves to an unrelated paper is worse than no DOI at all.

## Fixing a year does not renumber anything

Citation keys are assigned once per record id and kept in `citekeys.json`.
`Abrams2013Enhanced` keeps its handle while its date field reads 2014. The key
is a handle; the date field is the claim.

That example is the gentle case, and it can leave the impression that a key is
at worst a year out. It is not. The library holds `Lordi2011Molecular` over
`year = 2000` and `Pincus2002Excluded` over `year = 1976` — keys minted while
those records carried a wrong year, kept after the year was corrected, and now
11 and 26 years adrift. Both are behaving exactly as designed.

So: **read the year off the field, never off the key.** Renumbering is the one
repair the tool will not make, because a key is cited in manuscripts and names
files under `findings/`; a key that could be corrected would be a key that could
break someone's bibliography. A wrong-looking key is not a defect to report.

## When a reference looks like it is missing its journal

BibTeX only emits `journal` for entry type `article`, and the mirror's `generic`
type maps to `@misc`. A record with the right journal name but the wrong *type*
loses it on the way out. Fix the type, not the metadata.

## Credentials, and what it cannot do

The same `~/.config/offprint/zotero.json` every other script reads, except that
this one needs a key with **write** access. Nothing is sent until you confirm at
a `[y/N]` prompt; `--yes` skips the prompt, `--dry-run` prints the payload and
sends nothing at all.

A 412 means someone edited the record while this run was deciding what to send.
It is reported and the item is left alone — never re-read and retried, which
would overwrite the edit that caused the conflict.

It edits records that exist and cannot create or delete one: `zotero_push.py`
adds a reference, and `zotero_delete.py` removes one. Nor does it check for
duplicates, so if you are not sure whether something is already in the library,
grep `index.md` for the DOI first.
