# Reading a paper you have

The extract answers most questions. These two commands cover the rest: when you need the page itself, and when you need to know what a paper cited.

## When you need the actual PDF

```
uv run --script get_pdf.py Muller2020Yield        # prints the path
uv run --script get_pdf.py --search "packed bed"  # find the key first
uv run --script get_pdf.py Muller2020Yield --open # and open it
uv run --script get_pdf.py Muller2020Yield-2     # the SECOND attachment
```

**Three sources, in order, and the first two need no account.** The local
cache; then `<out>/pdf/`, if this machine holds an archive — served *in place*,
since that directory belongs to the mirror; then Zotero.

On Cameron's library the middle one is empty: the archive was a transit buffer
for the Mendeley → Zotero migration and was deleted on 2026-10-02 once every
attachment had been verified present in Zotero. So in practice a miss goes to
Zotero. `mendeley_mirror.py --attachments keep --backfill` rebuilds an archive
if one is ever wanted again.

Fetched PDFs are cached *outside* the mirror (`~/.cache/mendeley-mirror/pdf`, or
`%LOCALAPPDATA%\mendeley-mirror\pdf` — that directory kept its old name
deliberately, since moving a cache costs something and buys nothing), so
grabbing one to look at a figure does not push it to every synced machine.
Unknown keys, cache hits and archived papers are all handled before any network
call, so a typo never reaches an account.


## What a paper cites

```
uv run --script refs.py FANG1995Polycyanate            # every reference
uv run --script refs.py FANG1995Polycyanate --have     # only the ones you hold
uv run --script refs.py FANG1995Polycyanate --missing  # only the ones you don't
```

Two sources, in order. First Crossref, keyed by the paper's DOI: the
publisher's own deposited reference list, structured, with DOIs on the
individual references. That is the good case, and it covers roughly three
quarters of the DOIs in the library — but almost nothing published before about
1995, because the practice did not exist yet. Failing that, the list is parsed
out of the extracted text, which handles numbered reference lists and
author-year lists that came out one entry per line, and does badly on reflowed
two-column ones. The output always names the source it used.

Each reference is then matched against `library.bib` and the rule is shown:
`doi` and `title` are reliable, `title-in-raw` and `author-year+` less so. The
matching is deliberately conservative — the same author publishing twice in a
year is common enough that surname-plus-year is not treated as a match — so the
error you should expect is a reference you own being reported as not found, not
a reference being tagged with the wrong key.

Which makes the useful reading of `--missing` "here is what to go look at",
and the useful reading of `--have` "here is what to cite from your own shelf".

## A record can have more than one attachment

`text/<key>-2.md` is a second *file* on the same reference, not a second paper —
supporting information, a corrigendum, sometimes the paper itself. Ask for it by
the same name the extract carries: `get_pdf.py <key>-2`.

The first attachment is not always the article. `Brenner1990Empirical`'s first
is a one-page errata sheet that opens with an unrelated erratum; the 14-page
paper is the second. Until 2026-09-24 `get_pdf.py` could only serve the first
and rejected `<key>-2` as an unknown citation key, so following the extract's own
advice to check a quotation against the rendered page produced a different
paper's document under the right name.

Two things now guard that. Asking for a plain key on a record with several
attachments says so on stderr and names the others. And a downloaded file whose
page count does not match what the extract records is served with a warning and
a non-zero exit, because the order this script sees is not promised to be the
order the refresh numbered them in.

### Which records actually have a second attachment

```
uv run --script get_pdf.py --attachments
```

One bulk listing, no downloads — `itemType=attachment` against Zotero, which is
the direct analogue of the `/files` call this was built around. It reports, for
every record with more than one extract, how many PDF attachments the library
currently holds, and names two different problems:

- **ORPHAN** — the mirror wrote `text/<key>-N.md` but the account no longer
  reports that many attachments. The file was deleted after it was extracted,
  so **the extract may be the only remaining copy of that document.**
  It cannot be regenerated, and it must not be deleted to force a re-extraction.
- **DUPLICATE** — two extracts of one record with the same body: the same file
  attached twice. Nothing is lost by ignoring it, though a search will hit the
  paper twice.
- **NEAR-DUPLICATE** — the same, where the two differ slightly, as two scans of
  one article do. A prompt to look, not a verdict: exact equality is too strict
  to catch these, and `Shan2011How`'s two extracts are the same paper at 99.3%.
- **NO BASE** — the first attachment produced no extract, so `text/<key>.md` does
  not exist and only `<key>-2` onward do. Such a record is in `index.md` and
  `library.bib` but appears in no extraction report, which is worth knowing
  before concluding the mirror does not have the paper.

Every pair of a record's extracts is compared, not each one against the base —
with no base there is nothing to compare against, and that is exactly the case
where two copies of one paper hide.

The bodies are compared rather than the files, because the front matter names the
key and so `<key>-2.md` and `<key>.md` always differ as bytes even when they came
from the same PDF.
