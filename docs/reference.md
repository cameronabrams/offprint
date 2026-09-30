# Reference

Command-line options, the tests, and the accumulated small print.

## Options

```
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
is unchanged and whose extract already exists, so improving extraction does not
by itself revisit papers already mirrored. Re-reading one means removing its
entry from `.mirror/state.json` (keyed by Mendeley *file* id) and refreshing —
back that file up first.


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
