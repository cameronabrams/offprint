# offprint

```{image} _static/offprint-banner.jpg
:alt: offprint
:width: 100%
```

Keeps a plain-folder copy of your reference library so that Claude — or LaTeX,
or `grep`, or anything else — can read it without needing the reference manager
itself.

**The backend is Zotero.** It was Mendeley until 2026-10-02, and the Mendeley
path is still here but is retired: `zotero_source.py --refresh` is the refresh
now. The point of the split is that the mirror outlives whichever service it
came from — `library.bib`, `text/`, `annotations/` and `pdf/` are a complete
corpus that needs no account to read.

The tool and the library it writes are two separate directories. This repo is
the tool; nothing in it is generated, and it can be cloned anywhere.

```
offprint/                      ← this repo, clone it where you like
│
│   the refresh
├── zotero_source.py       read Zotero and rebuild the mirror (--refresh)
├── mendeley_mirror.py     the Mendeley refresh, retired — and still the module
│                          every other script imports its generators from
│
│   reading the library
├── get_pdf.py             pull one paper's real PDF when the text isn't enough
├── refs.py                what a paper cites, and which of those you already have
├── finding.py             record what was asked of a paper and where the answer is
├── pdbrefs.py             which papers cite which PDB structures
├── pdbxref.py             cited structures whose primary papers are missing
├── doixref.py             each record's own DOI against Crossref, read-only
│
│   writing to the live account
├── zotero_edit.py         correct fields on records already in Zotero
├── zotero_attach.py       upload the mirrored PDFs into Zotero's attachments
├── zotero_inbox.py        file a PDF you downloaded onto the record it belongs to
├── inbox.py               file PDFs you saved into inbox/ (Mendeley, retired)
├── mendeley_push.py       add one reference to Mendeley (retired)
├── mendeley_edit.py       correct fields in Mendeley (retired)
│
│   one-offs and plumbing
├── zotero_migrate.py      pair every Mendeley record with its Zotero item
├── run_mirror.sh/.bat     launchers for the Mendeley refresh
├── install_schedule.bat   register/remove the hourly background refresh
├── refresh_quiet.bat/.vbs what that scheduled task actually runs
└── test_mirror.py         offline tests: pure functions plus a stubbed API
```

The mirror itself is everything the refresh writes. It defaults to
`~/Sync/mendeley` (`%USERPROFILE%\Sync\mendeley` on Windows); `--out` sends it
anywhere else. The directory still carries the old backend's name, which is
cosmetic and deliberate — renaming it would break every machine's sync
configuration to fix a word. Every file in it is generated — edit in Zotero, not
here.

**One exception, added deliberately: `findings/`.** It is the only hand-written
directory in the mirror, it is never touched by a refresh, and it holds the record
of what was asked of a paper and where the answer sat. It lives here rather than
outside so it syncs to every machine and greps beside `text/` — a record that is
machine-local is a record nobody else can check. See [Findings](findings.md).

```
Sync/mendeley/                 ← the library, and only the library
├── mirror-status.md       last attempt, last success, last error
├── CLAUDE.md              orientation for a Claude session opened on this folder
├── library.bib            every reference, stable citation keys
├── index.md               one row per reference (key, year, author, title, doi)
├── folders.json           your Mendeley collections -> citation keys
├── text/                  Muller2020Yield.md — extracted full text, page-marked
├── annotations/           Muller2020Yield.md — your highlights and notes, by page
├── inbox/                 drop zone for PDFs you saved by hand
├── .mirror/               citation keys, extraction state, run log
└── extraction-report.md   attachments whose text is not plain prose, and why
```

Commands throughout these pages are written as `uv run --script refs.py` and
assume you are in the clone. From anywhere else, give the full path —
`uv run --script ~/Git/offprint/refs.py` — since the scripts find the
mirror by its own absolute default, not by where they sit.

## Where to go next

```{toctree}
:maxdepth: 2

getting-started
the-mirror
reading
filing
findings
structures
corrections
scheduling
operating
reference
```
