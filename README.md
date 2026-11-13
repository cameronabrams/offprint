# offprint

![offprint](docs/_static/offprint-banner.jpg)

Keeps a plain-folder copy of your reference library so that Claude — or LaTeX,
or `grep`, or anything else — can read it without needing the reference manager
itself.

**The backend is Zotero**, and `zotero_source.py --refresh` is the refresh. A
previous backend was retired on 2026-10-02 and its code was removed on
2026-11-13. The point of the split is that the mirror outlives whichever service it
came from — `library.bib`, `text/`, `annotations/` and `pdf/` are a complete
corpus that needs no account to read.

The tool and the library it writes are two separate directories. This repo is
the tool; nothing in it is generated, and it can be cloned anywhere.

```
offprint/                      ← this repo, clone it where you like
│
│   the refresh
├── zotero_source.py       read Zotero and rebuild the mirror (--refresh)
├── mirror.py              the mirror itself: the generators, the extraction and
│                          the id namespacing every other script imports
│
│   reading the library
├── get_pdf.py             pull one paper's real PDF when the text isn't enough
├── refs.py                what a paper cites, and which of those you already have
├── finding.py             record what was asked of a paper and where the answer is
├── pdbrefs.py             which papers cite which PDB structures
├── pdbxref.py             cited structures whose primary papers are missing
├── doixref.py             each record's own DOI against Crossref, read-only
├── citemap.py             which claims are citation-supported, and by what
├── whatbreaks.py          what rests on each paper, and what breaks if it is wrong
│
│   writing to the live account
├── zotero_edit.py         correct fields on records already in Zotero
├── zotero_attach.py       upload the mirrored PDFs into Zotero's attachments
├── zotero_inbox.py        file a PDF you downloaded onto the record it belongs to
├── zotero_delete.py       remove a record or a byte-less attachment
├── zotero_push.py         add one reference to Zotero, from a DOI or arXiv id
│
│   shared halves of filing a paper
├── identify.py            which paper is this PDF, and does the library have it
├── fromdoi.py             build a record from a DOI or an arXiv id
├── zotero.py              the read-only Zotero client and its credentials
│
│   one-offs and plumbing
├── run_mirror.sh/.bat     launchers for the refresh
├── install_schedule.bat   register/remove the hourly background refresh
├── refresh_quiet.bat/.vbs what that scheduled task actually runs
└── test_mirror.py         offline tests: pure functions plus a stubbed API
```

The mirror itself is everything the refresh writes. It defaults to
`~/Sync/library` (`%USERPROFILE%\Sync\library` on Windows); `--out` sends it
anywhere else. It had a different name until 2026-10-02; both resolved until
2026-11-13, by which time no machine was left on the old one. Every file in it is generated — edit in Zotero,
not here.

**One exception, added deliberately: `findings/`.** It is the only hand-written
directory in the mirror, it is never touched by a refresh, and it holds the record
of what was asked of a paper and where the answer sat. It lives here rather than
outside so it syncs to every machine and greps beside `text/` — a record that is
machine-local is a record nobody else can check. See "Findings" below.

```
Sync/library/                  ← the library, and only the library
├── mirror-status.md       last attempt, last success, last error
├── CLAUDE.md              orientation for a Claude session opened on this folder
├── library.bib            every reference, stable citation keys
├── index.md               one row per reference (key, year, author, title, doi)
├── folders.json           your collections -> citation keys
├── text/                  Muller2020Yield.md — extracted full text, page-marked
├── annotations/           Muller2020Yield.md — your highlights and notes, by page
├── inbox/                 drop zone for PDFs you saved by hand (see below)
├── .mirror/               citation keys, extraction state, run log
└── extraction-report.md   attachments whose text is not plain prose, and why
```

## Quickstart

1. Create a Zotero API key at <https://www.zotero.org/settings/keys>. Read
   access is enough for a refresh; the writing scripts need write access.
2. Save it as `~/.config/offprint/zotero.json`
   (`%LOCALAPPDATA%\\offprint` on Windows):
   `{ "api_key": "...", "user_id": 1234567 }`. The user id is the **number** on
   that page, not your username — the API addresses libraries by id and rejects
   the name.
3. Refresh:

```
uv run --script zotero_source.py --refresh --dry-run   # reads, writes nothing
uv run --script zotero_source.py --refresh
```

The dry run is worth reading once. The line to check is
`citation keys: N known, M newly assigned` — on an established library **M must
be 0**, and anything else means the backend namespacing is wrong and the run
would mint new keys over keys already cited in your manuscripts.

Nothing to install: the scripts carry a PEP 723 header, so `uv` builds and
caches the environment on the fly.

Commands assume you are in the clone. From anywhere else give the full path —
the scripts find the mirror by its own absolute default, not by where they
sit.

## Full documentation

**<https://mendeley-mirror.readthedocs.io>** — the published URL carries the
old project name. It is a slug, not a claim: renaming a Read the Docs project
breaks every link already pointing at it, so this one outlives the name.

| page | what it covers |
|---|---|
| Getting started | registering the app, authorizing, a second machine, where credentials live |
| What the mirror contains | what a refresh writes, and how to tell a usable extract from one that only looks usable |
| Reading a paper you have | `get_pdf.py` when you need the page itself, `refs.py` for what a paper cites |
| Adding papers | `zotero_inbox.py` for a PDF you downloaded, `zotero_push.py` for a reference with no PDF |
| Findings | recording what was asked of a paper and where the answer sat |
| Structures | which papers cite which PDB entries, and which cited structures have no paper here |
| Correcting a reference | `zotero_edit.py`, and the properties that keep it from destroying good metadata |
| Keeping it fresh | scheduling a refresh, and the rule that there is never more than one at a time |
| How it is run | why the clone and the library are separate, where state lives, and who may write |
| Reference | every command-line option, the tests, and the small print |

## The direction of travel

A refresh is one-way: the service to disk. A bad run can lose mirrored files
but cannot touch your library — edit in Zotero, re-run, done. Five scripts write
back, each by hand and each dry-run by default: `zotero_edit.py` corrects
fields, `zotero_attach.py` uploads the mirrored PDFs, `zotero_inbox.py` files a
PDF you downloaded, `zotero_push.py` adds a reference, and `zotero_delete.py`
removes one.

**`zotero_edit.py` deserves the most care**, because it is the only one that can
destroy correct metadata rather than merely add wrong metadata. Three properties
limit that: Zotero's PATCH is a partial merge so unnamed fields survive, a field
already equal is skipped so a re-run after a partial failure sends nothing, and
a `creators` edit replaces only the creator types it names — so correcting an
author list cannot silently drop an editor.

## Tests

```
uv run --script test_mirror.py
```

Offline throughout: pure functions plus a stubbed API, no network and no
account. That includes an end-to-end Zotero refresh against a stub, which
asserts the thing that would be both catastrophic and silent — that no citation
key is minted and none moves.

## Where this is going

[ROADMAP.md](ROADMAP.md) — what is likely next, and what is deliberately not
being done. The Zotero migration is finished: 2,739 records, and **not one
citation key moved**. What the roadmap mostly holds now is the record of how
each part was got wrong first, which is the more useful half.

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Cameron F. Abrams.
