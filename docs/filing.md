# Adding papers to the library


```{note}
**`zotero_inbox.py` is the live one**, since the cutover of 2026-10-02:

    uv run --script zotero_inbox.py              # dry run over <out>/inbox/
    uv run --script zotero_inbox.py --yes
    uv run --script zotero_inbox.py --key Abrams2013Enhanced paper.pdf --yes

It reuses the identification described on this page unchanged — that half was
never Mendeley-specific. What differs is the other half: it **creates an
attachment** on a record that already exists, and it will not create a *record*.
A paper not already in `library.bib` is reported, not added.

It refuses three further things, each because the alternative is a library that
is wrong rather than one that is missing something: a file whose bytes are
already attached to that record (checked by md5 before anything is created), a
PDF with no readable text, and a record it had to guess at.

The archive lags by one refresh for a newly filed paper: the bytes go to Zotero,
and `<out>/pdf/` gets its copy when the next refresh fetches them back.
```
Two paths into Mendeley: a PDF you downloaded by hand, and a reference with no PDF behind it. Both write to your live account, and both ask before they do.

## Filing PDFs you downloaded yourself

Open access covers under half the library, and publisher sites block scripted
downloads even for articles that are free to read — so fetching a paywalled PDF
stays a human job. Uploading it afterwards does not.

Save the PDF into `inbox/` under whatever name the publisher gave it, then:

```
uv run --script inbox.py --dry-run    # what it thinks each PDF is
uv run --script inbox.py              # file them
```

It works out which paper each file is, attaches it to the matching Mendeley
reference — creating the reference from the DOI if it is new — and moves the
file into the PDF cache outside the mirror, so it does not sync to every
machine. Run the refresh afterwards and the text appears in `text/` like any
other paper. Your part is one Save As; Mendeley never has to be opened.

Identification tries the file name, the PDF's embedded metadata, a DOI printed
on the first two pages, and finally a Crossref search on the first page's text.
Whatever it finds is checked before use: the title the DOI resolves to has to
appear on page 1. Anything that fails that check stays in the inbox with a note
rather than being filed against the wrong reference.

Two things follow from that. A scanned PDF has no text to check against, so
name it for its DOI — `10.2172_219366.pdf`, first underscore standing in for
the slash — and it will be filed, marked as unverified. And DOIs from outside
Crossref resolve too (OSTI reports, Zenodo deposits, most theses), because
lookups go through doi.org rather than one agency's API.

Some things have no DOI to be identified by at all — DTIC and NTIS reports,
theses, conference proceedings. For those, drop a JSON file next to the PDF
with the same stem, holding the fields Mendeley needs:

```json
{ "type": "report",
  "title": "Dilatometry on Thermoset Resins",
  "year": 1991,
  "source": "NRL Memorandum Report 6848, Naval Research Laboratory (DTIC ADA239276)",
  "authors": [{"first_name": "Arthur W.", "last_name": "Snow"}] }
```

A sidecar overrides identification completely — nothing is looked up, and what
you wrote is what gets filed. Since there is no DOI to match on, duplicates are
caught by an exact title match instead. The sidecar travels with its PDF into
the cache, so the metadata you typed once stays with the file.

A paper that is new to the library has no citation key until the next refresh,
so its cached PDF is named for its DOI at first. `get_pdf.py` renames it the
moment the key exists, rather than downloading a second copy.

The folder syncs, so you can drop a PDF on the Windows desktop and file it from
anywhere. Nothing is uploaded until you confirm.


## Adding a paper to Mendeley

The mirror is one-way. `mendeley_push.py` adds a single reference to Mendeley
itself from an arXiv ID or a DOI:

```
uv run --script mendeley_push.py --arxiv 2507.07887
uv run --script mendeley_push.py --doi 10.1088/2632-2153/ae4b07
uv run --script mendeley_push.py --arxiv 2507.07887 --dry-run   # look first
```

It touches nothing in the mirror. The document is POSTed to Mendeley, and the
next refresh brings it back down like any other reference — with a citation key,
extracted text if you later attach a PDF, and a row in `index.md`.

Metadata comes from arXiv's Atom API or from Crossref, so you get real authors
and a real year instead of a stub to fix up by hand later. "Real year" means the
**issue** year: Crossref's `issued` is the date a paper first appeared *online*,
so for an Advance Access paper it is a year early. Both writers prefer
`published-print`, falling back to `issued` only for work that was never printed —
a preprint, a data set, a born-digital journal. arXiv preprints go in
as `type: journal` with `source: arXiv`, because Mendeley has no preprint type.
