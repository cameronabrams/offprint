# What the mirror contains

What a refresh writes, what it deliberately throws away, and how to tell a usable extract from one that only looks usable.

## What happens to the PDFs

Each attachment is downloaded, its text extracted, and the PDF then deleted. You
read papers in a reference manager; a second copy on disk earns nothing but
gigabytes. What
stays behind is `text/<citekey>.md`: YAML front matter with title, authors, year,
and DOI, then the text with `<!-- p. 7 -->` markers so a quote can carry its page.

Extraction is deterministic — the words off the page, not a summary. That matters
if you are going to quote from it: a paraphrase written months earlier is
indistinguishable, later, from what the paper actually said.

What it loses: equations come out mangled, table structure does not survive, and
figures are gone entirely. Each file says so at the top. `--attachments keep`
keeps the PDFs too, if you ever want them local.

If a previous run already downloaded PDFs into `pdf/`, they are re-used for
extraction instead of being fetched again, then removed.

### When the text is not plain text

Not every PDF yields clean prose, and the difference decides what the extract is
good for. `extraction-report.md` lists every attachment that is not ordinary
text, in four classes:

- **No text layer** — an image-only scan. It is mirrored and citable but
  invisible to any text search, so the report exists to make that gap a known
  one rather than a silent hole.
- **Read by OCR** — those same scans, once you run the opt-in pass below. The
  extract carries `ocr: true` in its front matter, and that marker matters:
  OCR output runs around 81% word-accurate, which is enough to *find* a passage
  and not enough to *quote* one. Treat an OCR'd extract as a finding aid and read
  the rendered page before quoting it.
- **Garbled text layer** — a PDF whose embedded text decodes to nonsense, usually
  a broken font encoding. These used to be mirrored as plausible-looking gibberish.
  Since 2026-09-16 each garbled page is re-read with `pdftotext` when that
  produces something better, and the extract records how many pages were repaired
  or dropped.
- **Not a PDF** — the attachment was examined and discarded, because its bytes
  carry no PDF header. Often that is right: a figure, a structure file, a video
  attached to a paper. But where it is the record's *only* attachment, it is a
  paper with no extract, so the row prints what the bytes actually were and the
  two can be told apart. Until 0.12.0 this class was skipped **silently** —
  nothing counted it and nothing listed it, so a readable paper could be missing
  from every text search while the file whose job is to explain missing extracts
  said nothing at all.

  Which attachment counts as a PDF is decided by the bytes, not by the MIME type
  the backend reports. Mendeley described a complete 20-page paper as something
  else, and under that older rule it was skipped, cached as skipped, and never
  reconsidered by any later refresh.

### Deleting an attachment from the archive, and making it stay deleted

`<out>/pdf/` is an archive a person built, and a person may want something out
of it again. (On Cameron's library it is currently **empty** — the archive was
deleted on 2026-10-02 once every attachment was verified present in Zotero. The
mechanism below applies to any machine that holds one, and to this one if
`--backfill` is ever run again.) — a video, a structure file, a scan that was never a paper. Deleting
the file is not enough on its own: `--backfill` tests only whether a file is on
disk, so it reads the gap as evacuation it has not finished yet and fetches the
file back. That command is *documented as safe to repeat*, which is what made it
the dangerous one.

`.mirror/removed.tsv` records the difference. One stem per line — `<citekey>` or
`<citekey>-N`, matching the archive's own naming — optionally followed by a tab,
a date and a reason:

```
# taken out by hand, 2026-10-01
Shan2011How	2026-10-01	a video, not a paper
Hoover1979Exact	2026-10-01	a PNG of one equation
```

Both `--backfill` and `--attachments keep` then leave those alone and say how
many they withheld. The match is on the exact stem, so a line for `Shan2011How`
has no effect on `Shan2011How-2`.

**It never causes a deletion.** A listed stem whose file is present on disk is
left exactly where it is; the file withholds a fetch and nothing else. Keeping
it that way matters — a list of filenames the tool consults is precisely how a
third archive-eroding path would arrive.

### Pairing an attachment Zotero cannot be matched to automatically

`zotero_attach.py` refuses to guess which archived file belongs to which Zotero
attachment when a record has several and nothing distinguishes them. The refusal
prints what the decision needs: each archived file with its size, and each Zotero
attachment with its key, filename, link mode and content type. Filenames carry
author surnames and subject words, which is usually enough to see the answer.

`.mirror/pairings.tsv` is where the answer goes — one line per attachment, the
stem and the Zotero attachment key, with a date and a reason if you want them:

```
# decided by hand after reading the filenames, 2026-10-02
Won2001Influence	ZK1AB2CD	2026-10-02	filename names Won, not Shan
Won2001Influence-2	ZK3EF4GH	2026-10-02	the supplement
```

A stem named here bypasses every automatic rule, so the file is checked strictly:
a key that is not on that record, two stems claiming one attachment, or a line
missing its key each stop the run rather than being skipped. This is the one
place a human assertion overrides the tool's refusal to guess, and a typo in it
would otherwise become a wrong upload.

Partial coverage is fine and is usually the quickest route — deciding one
attachment on a record often leaves the rest unique enough for the extension rule
to settle. Re-run after each few decisions; an attachment already uploaded is
skipped.

Two smaller traps worth knowing. A short extract is not evidence of a short
paper: some scans are dozens of pages of one repeated permission stamp, and the
known ones are listed in `stamp-only-extracts.tsv`. And every attachment on a
record is extracted, with all but the first suffixed — so `text/<key>-2.md` is a
*second file on that record*, not a second paper, and a report row naming
`<key>-2` is not saying the paper is unreadable.

**Running OCR.** It is never part of a scheduled refresh, because it is slow and
needs an extra dependency:

```
uv run --with rapidocr-onnxruntime --script zotero_source.py --refresh --ocr
```

It reads only the attachments that failed the text-layer check. On a large
library this takes hours; if a refresh is scheduled, stop the timer first and
restart it afterwards, since two runs at once will make sync conflicts out of
the state files.
