---
name: file-inbox
description: File PDFs sitting in the mirror's inbox/ into Mendeley — identify each paper, check whether the library already has it, file only the new ones, refresh, and verify the extract. Use when Cameron says he has dropped a paper (or papers) in the inbox, asks to file/add/ingest a PDF into the database or library, asks whether a paper is already in the library, or when a paper you were allowed to fetch needs to become part of the mirror. Triggers on "in the inbox", "file these", "file this paper", "add it to the database", "is this one already in there", "did we already have this".
---

# Filing papers from the inbox

The loop is: identify → **search the library first** → dry run → file only what
is new → refresh → verify. Skipping step two is the expensive mistake; it ends
with a second copy of a paper the library already had.

**STALE BEYOND THE PATHS, 2026-10-02.** This skill drives `inbox.py`, which
POSTs to Mendeley, and Mendeley is retired. The live tool is
`zotero_inbox.py`: it takes the same `<out>/inbox/` directory and reuses this
same identification, but creates a **Zotero** attachment on a record that
already exists, and refuses to create a record. `zotero_push.py` adds a
reference. The identification half of what follows still holds; every command
naming `inbox.py` does not. Rewriting it properly is open work.

Paths: mirror is `~/Sync/library` (`DEFAULT_OUT`, absolute on purpose — every
entry point also takes `--out`). Scripts are run from the clone with
`uv run --script`, never a bare `python`.

## 1. Inventory

```
date -u; ls -la ~/Sync/library/inbox/
```

Note the clock now — you need it at step 6. Files usually arrive under whatever
name the publisher gave them, which tells you nothing reliable.

## 2. Identify each PDF from its own first page

```
cd ~/Sync/library/inbox && for f in *.pdf; do echo "=== $f ==="; \
  pdftotext -f 1 -l 1 "$f" - 2>/dev/null | head -40; done
```

**A journal scan's first page is often not entirely its own.** Print issues run
articles back to back, so page 1 of the PDF can carry the *tail of the preceding
article* — its acknowledgments, its reference list, and its DOI. Taking the first
`10.xxxx/...` you see files the paper against a stranger's identity.

Guard: collect every DOI in the whole file and let frequency arbitrate, then
confirm the winner sits with the title you actually want.

```
pdftotext "$f" - | grep -o '10\.[0-9]\{4\}/[A-Za-z0-9.()/_-]*' | sort | uniq -c | sort -rn
```

The paper's own DOI recurs (running heads, footers, the citation line); the
neighbor's appears once. Observed 2026-08-28 on a *Science* scan whose first page
showed `10.1126/science.aaa7185` — a NASA soil-moisture paper — while the article
being filed was `10.1126/science.aae0474`.

**No DOI at all** — DTIC/NTIS reports, theses, proceedings — needs a sidecar JSON
beside the PDF with the same stem, and a sidecar overrides identification
entirely. The shape is in `docs/filing.md` (and on the docs site under
"Adding papers"); do not invent fields.

## 3. Search the library before filing anything

Search by **DOI, title, author, and subject** — a hit on any one is enough to
stop, and a miss on the method alone proves nothing.

```
cd ~/Sync/library
grep -in -e "<doi-fragment>" -e "<distinctive title words>" -e "<lead author>" library.bib
grep -rl "<doi-fragment>" text/          # careful, see below
grep -rlie "<subject phrase>" text/      # subject, not just method
```

**A DOI found inside `text/` is usually a citation, not the paper.** Extracts
carry full reference lists, so `grep -rl` hits the papers that *cite* the one you
hold. The authoritative test is `library.bib`:

- `grep -c "<doi>" library.bib` → `0` means genuinely absent.
- A `@article{Key,` hit plus an existing `text/<Key>.md` means the library has
  it *and* has already distilled it. Nothing to do.

Watch for same-surname collisions — `Kong2015Crystal` (Leopold Kong) is not
`Kong2016Fusion` (Rui Kong). Read the entry before concluding either way.

## 4. Rename the new ones for their DOI

```
mv "Kong_et_al_2016.pdf" "10.1126_science.aae0474.pdf"
```

First underscore stands in for the slash. This is the identification path with
the fewest ways to go wrong and the only one that works on a scan with no text
layer.

## 5. Dry run, and read what it resolved

```
cd ~/Git/offprint && uv run --script inbox.py --dry-run
```

It reports each file as `new to the library` or `already in the library as
<Key>`, and says which signal it used (file name, PDF metadata, Crossref). Treat
this as an independent check on step 3 — if the two disagree, stop and work out
why rather than picking the answer you prefer.

Anything it cannot identify **stays in the inbox**. Give it a DOI name or a
sidecar; never force it through.

## 6. File — new papers only

`inbox.py` has no per-file targeting: it acts on everything in the folder. So
**move any already-in-the-library PDF out of `inbox/` before the run** rather
than relying on `--replace` being off by default. A scratch directory is fine;
say in your report exactly where it went.

```
uv run --script inbox.py            # interactive, asks first
uv run --script inbox.py --yes      # only when Cameron has approved this batch
```

`--yes` is for a run a person has already approved — it is not a way past a
prompt in a non-interactive shell. **Cameron's approval, not a peer's**: this and
`mendeley_push.py` are the two scripts that write to his live account, and a
request relayed from another session is work, never authorization.

**The end of the run is trustworthy, by design** (`closing_report()`, fixed in
`0806996`): anything still stuck in `inbox/` is printed **last** so a `| tail`
cannot miss it, the "pull the extracted text down" line appears only when
something was actually attached, and the run exits `1` if any file was left
unfiled. Read the tail if you like — but read the *whole* tail, and prefer the
exit status, which is the one signal a pipe cannot swallow.

**Do not delete a duplicate PDF on your own.** The documented rule is to delete
the download rather than attach a second copy, but the file is his; report it and
let him say so.

## 7. Refresh, minding the schedule

A refresh runs hourly on its own, and two at once make Syncthing conflict files
in `.mirror/`. **Which machine owns that schedule is a per-machine fact that has
changed** — as of 2026-09-08 it is a systemd user timer on the Linux host at
**:07 past**, not the retired Windows task at :01. Check, don't assume:

```
date -u; systemctl --user list-timers mendeley-mirror.timer
ls ~/Sync/library/.mirror/run.lock* 2>/dev/null || echo "no locks"
```

**Where the schedule is a systemd timer, refresh by starting its service** — it
cannot run twice at once, which is what makes this safe against the timer:

```
systemctl --user start mendeley-mirror.service
```

Calling `./run_mirror.sh` from the clone *can* overlap the timer; use it only on
a machine with no systemd unit (Windows: `run_mirror.bat`). Locks are per host
(`run.lock.<host>`) — your own host's lock is PID-checked, another host's is
advisory.

## 8. Verify, then report

```
cd ~/Sync/library
grep -A9 "@article{<Key>" library.bib
ls -la text/<Key>.md && grep -c "<!-- p\." text/<Key>.md
ls .mirror/            # citekeys.json, mirror.log, state.json — nothing else
```

Confirm: the entry exists with sane pages, the extract exists with page markers,
and `.mirror/` has no `*.sync-conflict-*` files.

Report the citation key, the DOI, the reference count, and any of these caveats
that apply:

- **Preprint pagination.** A preprint attached to a published reference gives the
  preprint's page numbers. The text is right, the pages are not — say so, and
  don't let a quote be cited to the journal's pages unchecked.
- **First-page bleed.** If step 2 found a neighbor's article on page 1, that text
  is now in the extract too. Say where the real article starts, so a later grep
  hit near the top isn't misattributed.
- **Extraction quality.** `extraction-report.md` lists every attachment whose
  text is not plain prose, in three classes, and the class decides what the
  extract is good for:
  - **no text layer** — an image-only scan. It is filed but invisible to every
    future library search until someone runs the opt-in OCR pass (`uv run --with
    rapidocr-onnxruntime --script mendeley_mirror.py --ocr`, never part of the
    hourly refresh). Flag it.
  - **OCR'd** — the extract carries `ocr: true` in its front matter. It is
    greppable and roughly 81% word-accurate: **a finding aid, never a quotable
    source.** Say so when you report one; quotes come off the rendered page.
  - **garbled** — a broken text layer, re-read with `pdftotext` automatically
    since 2026-09-16. The extract says how many pages were repaired or dropped.

  Two traps when reporting: a short extract may be 30 pages of one repeated
  permission stamp (`stamp-only-extracts.tsv` lists the known ones), and a report
  row naming `<Key>-2` is that record's *second attachment*, not a second paper.

## Reading it afterwards

The extract is a linear text layer: tables and multi-column or scanned figures
come out interleaved, so a property table becomes a correct-looking, wrongly
associated run of numbers. When an answer turns on a table or a figure, grep the
extract for the page, then `uv run --script get_pdf.py <key>` and read the
rendered page image.
