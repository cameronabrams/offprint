# Reference

Command-line options, the tests, and the accumulated small print.

## Options

### `zotero_source.py` — the refresh

```
--refresh              rebuild library.bib, index.md, text/, annotations/
                       and folders.json from Zotero
--dry-run              with --refresh: read and report, write nothing
--ocr                  with --refresh: read scanned attachments that have no
                       text layer (needs --with rapidocr-onnxruntime; the run
                       checks for it once at the start and exits, rather than
                       failing per attachment)
--reassess             with --refresh: re-read attachments whose extract was
                       produced under older extraction rules
--compare              regenerate every BibTeX entry from Zotero and diff it
                       against the existing library.bib, writing nothing
--rescue-identifiers   emit a zotero_edit.py edits file restoring identifiers
                       the bib holds and Zotero does not
--out DIR              mirror somewhere else
--version              print the version and exit
```

**`--ocr` is not only for a first pass.** A `no-text` status is the verdict of
the *plain* reader, so a run carrying OCR re-examines those attachments rather
than inheriting it. Without the flag they are skipped, which is correct and is
also why an OCR recovery run must be given the flag or it does nothing at all.

### `zotero_edit.py` — correcting a record

```
--edits FILE           JSON, {citekey: {field: value}}, with ZOTERO field names
--dry-run              show the diff, send nothing
--yes                  do not ask before sending
```

### `zotero_inbox.py` — file a downloaded PDF onto a record

```
[FILE ...]             PDFs to file; default is everything in <out>/inbox/
--key CITEKEY          file ONE named PDF under this key, skipping identification
--yes                  create and upload; without it this is a dry run
```

Creates a child **attachment** on a record that already exists. It will not
create a record, will not attach bytes the record already holds (checked by md5
before anything is created), will not file a PDF with no readable text, and will
not guess which record.

### `zotero_push.py` — add one reference

```
--doi DOI              e.g. 10.1088/2632-2153/ae4b07
--arxiv ID             e.g. 2507.07887
--yes                  create it; without this it is a dry run
```

Refuses a record the library already has, by DOI and then by normalized title.
Does **not** assign a citation key — the next refresh does that, once, and keeps
it. A dry run needs no Zotero key, though it does need the mirror readable.

### `zotero_attach.py` — upload the mirrored PDFs into Zotero

```
--yes                  upload; without it this is a dry run that writes nothing
--key CITEKEY          limit to one citation key (repeatable)
--limit N              stop after this many uploads
--pair-by-order        for records that cannot be paired by extension, pair by
                       archive order -- a GUESS, off by default
--no-metadata          upload bytes only; leave filename and content type alone
```

Fills attachment slots that already exist; it cannot create one. Where a record's
attachments cannot be paired unambiguously it uploads nothing for that record and
prints the candidates.

### `zotero_delete.py` — remove a record or a byte-less attachment

```
--key CITEKEY          citation key of a record to delete (repeatable)
--stubs                find attachments whose file endpoint 404s and remove them
--yes                  actually delete; without it this is a dry run
--allow-last-copy      permit deleting a record with no surviving sibling
```

**The one operation here with no undo.** A dry run names what *survives* as well
as what goes, because the dangerous deletion is the sibling you did not list. It
backs up every item and its children to `.mirror/deleted-<timestamp>.json` before
sending anything, and it touches neither `citekeys.json` nor `pdf/` nor `text/`.

A byte-less attachment is found by asking the **file endpoint**, never by
`md5: None` — that field says the item records no file, which is a different
claim, and 1,060 attachments reported it while Zotero held bytes for all of them.

### `zotero_migrate.py` — pair Mendeley records with Zotero items

```
--write                write the zotero: half into citekeys.json
--selftest             run the matcher's fixtures
```

A one-off, run on 2026-09-29. Read-only without `--write`. It is what made the
migration keep every citation key: 2,739 of 2,739 paired, by DOI, then title and
year, then by hand.

### `doixref.py` — each record's DOI against Crossref

```
--report FILE          write the report here as well as to stdout
--key KEY              limit to one citation key (repeatable)
--limit N              stop after this many lookups
```

Read-only and public; it never writes to the library, and it caches to
`~/.cache/offprint/` so a re-run resumes.

### `mendeley_mirror.py` — the retired refresh

```
--retire "REASON"      declare this mirror frozen: rewrite mirror-status.md as
                       a standing staleness notice and refuse further refreshes
--successor WHERE      with --retire, where the live library now is
--backfill             with --attachments keep: download attachments whose text
                       is already extracted, so every file lands in <out>/pdf/
run_mirror.bat --reconfigure       re-enter application ID, secret, redirect URL
run_mirror.bat --attachments keep  keep the PDFs as well as the extracted text
run_mirror.bat --attachments none  metadata and annotations only
run_mirror.bat --no-abstracts      leave abstracts out of library.bib
run_mirror.bat --out D:\refs       mirror somewhere else
run_mirror.bat --reauth            forget tokens and log in again
```

Three more, not shown above because they are not part of an ordinary refresh:

```
--ocr              read scanned attachments that have no text layer, and mark
                   the extract ocr: true (needs --with rapidocr-onnxruntime)
--no-annotations   skip the annotation export
--quiet            for scheduled runs: no progress output, never prompt, log to
                   .mirror/mirror.log
--version          print the version and exit
```

There is **no re-extract flag.** A refresh skips any attachment whose file hash
is unchanged *and* whose stored status says it was examined. Improving
extraction does not by itself revisit papers already mirrored; re-reading one
means removing its entry from `.mirror/state.json` and refreshing — back that
file up first.

**An `ok` is a verdict reached under a particular set of extraction rules.**
`state.json` records which (`rules`), and a run reports how many extracts
predate the current ones. It does **not** re-read them by default, because
re-extracting a library on every change to extraction is a real cost — but it
says how many and what to pass, so the staleness cannot go unnoticed.
`--reassess` is what acts on it.

`Kirkpatrick1983Optimization` is why this exists: it was extracted under rules
1, stored `ok`, and the run that *shipped* rules 2 skipped it as already done —
so the fix never reached the one paper it was written for. Every improvement to
extraction has that shape, because the papers that would benefit are exactly the
ones already marked finished.

What counts as "examined" is an explicit list — `ok`, `ocr`, `garbled`,
`no-text`, `not-pdf` — and **anything else, including `failed` and an
unrecognised value, is unfinished work and gets re-examined.** It is a list of
what is done rather than of what is not, so a failure status added later
defaults to being retried. That default is load-bearing: a run that had recorded
2,732 attachments as `failed` once skipped every one of them on the next pass,
because a text file from an earlier backend still existed and was mistaken for
evidence of work.


## Tests

```
uv run --script test_mirror.py
```

Offline throughout: pure functions plus a stubbed API, no network and no
Mendeley account. It prints a line per check and exits non-zero on failure.


## Notes and limits

- `library.bib` is UTF-8. Modern `biblatex` + `biber` handles that directly; old
  `bibtex` + `inputenc` works too, but expect the usual accent grumbles.
- Titles are double-braced (`title = {{Yield of CO2 capture}}`) so styles that
  lowercase titles cannot mangle formulas and acronyms.
- Mendeley's API returns the *position* of a PDF highlight but not always the
  highlighted **text**. Those show up as `*(highlight, p. 7)*` with no quote.
  If a lot of yours come out that way, the text can be recovered by cropping the
  PDF at those coordinates — worth doing only if you find you need it.
- Collections page differently: documents, files, and folders accept 500 per
  request, annotations only 200. If a collection rejects a page size anyway, the
  script backs it off and retries instead of dying.
- Annotations and folders are fetched *after* documents and files, and a failure
  in either is reported and skipped rather than losing the whole run.
- PDF downloads are resumable. Progress is checkpointed every 25 files and on
  Ctrl-C, so an interrupted first run picks up where it left off.
- The mirror is one-directional, by design. A refresh never writes back to
  Mendeley, so a bad run can't damage your library. Edit in Mendeley, re-run,
  done. Three scripts write to Mendeley, all by hand and all with a confirmation
  prompt: `inbox.py` attaches files, `mendeley_push.py` adds a reference, and
  `mendeley_edit.py` corrects fields on one that is already there.


## Which version wrote this mirror?

Extraction behaviour has changed more than once — OCR for scanned attachments,
then a `pdftotext` fallback for garbled text layers — and an extract carries no
sign of which rules produced it. So the version is recorded where a reader will
actually find it:

- `mirror-status.md` says `written by offprint X.Y.Z` beside the run times.
- `.mirror/state.json` carries `mirror_version` alongside `last_run`.
- `.mirror/health.json` carries the consecutive-failure count, when the streak
  began, and which kind the last failure was. It is separate from `state.json`
  on purpose: that file's shape is something other people's tooling reads, and a
  counter that changes on every failed run does not belong in it.
- `mendeley_mirror.py --version` prints it.

That makes "this extract predates the garbled-text fix" a question the library
can answer about itself, rather than one answered from a git log that the
library does not carry.


## Namespaced ids, and why a reference has two entries

Ids in `citekeys.json` and in `state.json` are **namespaced** — `mendeley:<id>`,
`zotero:<key>` — so that a second backend can be added without two services' ids
colliding. A bare id is read as Mendeley's, so a map written before this change
still works, and it is rewritten in place on the next run. The citation *key*
never changes: it is assigned once per id, is cited in manuscripts, and names
files under `findings/`.

Since 2026-09-30 `citekeys.json` carries both halves. Every reference appears
twice — once as `mendeley:<id>`, once as `zotero:<key>` — pointing at the same
citation key. That pairing *is* the migration map, and it is what lets a key
already in a manuscript survive the move between services.

The file is **append-only**: `assign_citekeys` adds an entry for a document id it
has not seen and never removes one. So a reference deleted in Mendeley keeps its
line here, on purpose — a key cited last year should still tell you which paper
was meant, long after the record behind it is gone.

**If you ever do prune the file by hand, drop both halves of a reference or
neither.** A new key is chosen against the keys already in use, and that check is
namespace-blind by design: a key spoken for by either half is spoken for, so a
Zotero record can never be handed a key some Mendeley record already owns. The
cost is one sharp edge — removing just the `mendeley:` line does not release the
key, and leaves a `zotero:` line pointing at a citation key that nothing in
`library.bib` answers to any more. Half a deletion is worse than none.
