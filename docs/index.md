# offprint

```{image} _static/offprint-banner.jpg
:alt: offprint
:width: 100%
```

Keeps a plain-folder copy of your reference library so that Claude — or LaTeX,
or `grep`, or anything else — can read it without needing the reference manager
itself.

**The backend is Zotero**, and `zotero_source.py --refresh` is the refresh.
A previous backend was retired on 2026-10-02 and its code was removed on
2026-11-13. The point of the split is that the mirror outlives whichever service
it came from — `library.bib`, `text/`, `annotations/` and `pdf/` are a complete
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
anywhere else. It had a different name until 2026-10-02; both resolved for as
long as any machine might still have the old one, and the fallback was removed
on 2026-11-13 once none did. There is one clone and no pull step, which is why
a rename gets an overlap at all: a machine renamed later,
or not at all, keeps working. Every file in it is generated — edit in Zotero,
not here.

**One exception, added deliberately: `findings/`.** It is the only hand-written
directory in the mirror, it is never touched by a refresh, and it holds the record
of what was asked of a paper and where the answer sat. It lives here rather than
outside so it syncs to every machine and greps beside `text/` — a record that is
machine-local is a record nobody else can check. See [Findings](findings.md).

```
Sync/library/                  ← the library, and only the library
├── mirror-status.md       last attempt, last success, last error
├── CLAUDE.md              orientation for a Claude session opened on this folder
├── library.bib            every reference, stable citation keys
├── index.md               one row per reference (key, year, author, title, doi)
├── folders.json           your collections -> citation keys
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
claims
structures
corrections
scheduling
operating
reference
```
