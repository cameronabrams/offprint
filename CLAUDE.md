# Working on this repo

This is the tool, not the library. Everything here is hand-written source; the
mirror it produces — `library.bib`, `index.md`, `text/`, `annotations/` — lives
in a separate directory and is entirely generated. Read `README.md` first for what the tool
does; `docs/` holds the long-form documentation it points at, published to Read
the Docs, and is where prose about *using* the tool belongs.

**This file is about working on the tool. Using it is documented where its user
is:** `<out>/CLAUDE.md` in the mirrored library — the filing loop, how to read
an extract, what to quote and how to cite it. A session working in this repo
does not run the writing scripts against the live account and does not start a
refresh; those belong to the session that owns the library.

## The one structural rule

Scripts never locate data relative to themselves. `DEFAULT_OUT` in
`mendeley_mirror.py` is an absolute path (`~/Sync/library`, which resolves on
Windows too), and every entry point takes `--out`. That is what lets the clone
sit anywhere while the library sits in a synced folder. Don't introduce a
`Path(__file__).parent / "text"` or a bare relative path — it will work in
testing and break for everyone whose clone is not inside their library.

`Path(__file__).parent` on `sys.path` is fine, and is how the four satellite
scripts import `mendeley_mirror` as a module.

## The second structural rule: stored ids are namespaced, live ids are not

Ids that **persist** — the keys of `citekeys.json` and of `state.json`'s `files`
— are written `mendeley:<id>`. Ids that arrive in an **API response** and live
only for the run — `docs_by_id`, `files_by_doc`, `folder_docs` — are bare. That
asymmetry is deliberate: the stored maps have to survive a second backend, and
the in-memory ones are rebuilt every run and never meet one.

They meet in exactly one place, the lookup at the head of `harvest_attachments`,
and that is where `qualify()` belongs. Getting it wrong there does not raise: the
guard beside it was written for the rare document with no citation key, and a
lookup that misses on *every* document takes that branch every time. On
2026-09-24 that skipped all 2,733 attachments and still wrote `**ok**`.

So: `keymap` and `state["files"]` are indexed through `qualify()`, always;
`local_id()` is how a raw id comes back out, and it returns `None` rather than a
wrong id for another backend's.

**The Zotero backend landed on 2026-10-02 and this is where it was handled.**
`BACKEND` is still `"mendeley"`, and every function that qualifies a stored id
now takes a `backend` parameter rather than reading that constant:
`assign_citekeys`, `write_bibtex`, `write_index`, `write_folders` and
`harvest_attachments`. A Zotero run that used the constant would namespace every
Zotero id as a Mendeley one, miss all 2,739 existing entries, and mint 2,739 new
citation keys over the top of keys already cited in manuscripts — silently, and
indistinguishably from a first run. `test_mirror.py` asserts both halves: the
right backend keeps the existing key, and the default backend produces the
second one.

**`qualify_map` is deliberately NOT given the backend.** It migrates *bare* ids,
and a bare id means Mendeley's by definition, whatever run reads the file. A
test covers that too, because it looks exactly like an omission.

Since 2026-09-30 `citekeys.json` holds both halves — 2,739 `mendeley:<id>` and
2,739 `zotero:<key>` entries pointing at the same 2,739 citation keys. Two things
follow. `taken` in `assign_citekeys` is built from **values** and is deliberately
namespace-blind, so a key spoken for by either half is spoken for; don't "fix"
that by filtering to one backend, or a Zotero record will be handed a key a
Mendeley record already owns. And the map is append-only — nothing in the tool
removes an entry — so a key cited in a manuscript keeps resolving after its
record is deleted. The consequence for anyone pruning by hand is in
`docs/reference.md`: both halves or neither, because dropping one leaves the key
reserved and the surviving line pointing at nothing in `library.bib`.

**A dependency belongs to whoever reaches it, and there are two ways to reach
one.** A module-level `import pymupdf` in a module you import is *yours* —
`inbox.py:60` is at column 0, so importing one function from it costs the whole
dependency whatever that function touches. A lazy import inside a function is
the *caller's*, which no import graph can show. `test_mirror.py` checks both by
reading the graph and recursing, after getting this wrong three times in one
day: by missing the lazy case, by then assuming `inbox` was lazy when it is
not, and by replacing a correct broad rule with a hand-maintained list that went
stale within the hour.

Two habits that came out of the same failure, and are cheaper than the debugging:

- **Searching for one access pattern is not searching for the accesses.** Every
  `keymap[...]` was fixed and every `keymap.get(...)` was missed. Grep the name,
  not a spelling of it.
- **A pass that does no work does not get to report success.** If attachments
  were listed and none were examined, `harvest_attachments` raises. Counting to
  zero is a result the tool must be able to tell apart from doing nothing.

## What lives where, and why

| where | what | why |
|---|---|---|
| the clone | all code | replaceable; `git pull` is the update mechanism **for other machines** |
| `<out>/.mirror/` | `citekeys.json`, `state.json`, `removed.tsv`, `pairings.tsv`, `mirror.log` | travels with the library, so every machine agrees what a citation key means and nobody re-extracts 2500 PDFs |
| `~/.config/mendeley-mirror` (`%LOCALAPPDATA%` on Windows) | app ID, secret, tokens | per-machine on purpose: a public repo and a synced library both stay free of credentials |
| `~/.cache/mendeley-mirror/pdf` | fetched PDFs | outside the library so grabbing one doesn't sync it everywhere |

Moving any of those three out of its column breaks something real. The state
directory in particular is load-bearing: `mirror_state_dir()` still carries a
migration from the old credential-directory location.

## What other people's files depend on

Citation-key generation, the shape of `state.json`, the `<!-- p. N -->` markers
and the content of `text/<key>.md` are contracts, not implementation details:
other people's bibliographies and other sessions' answers are already built on
them. A change to any of them is announced to the library's owner and to the
session that owns the library *before* it ships, not after — the 2026-09-11 OCR
path and the 2026-09-16 garbled-text fix both altered extracts that already
existed.

Corollary, learned the hard way while this repo and its user were one session:
**a fix verified only against the case that prompted it is a check that cannot
fail.** Ask the library session for cases it picked.

Second corollary: **a change to the format of anything this tool writes bumps
`__version__` in the same commit.** The version exists so a reader can tell which
rules produced the file in front of them, and it can only do that if it moves
when the rules do. 2026-09-24 shipped a new `mirror-status.md` layout under an
unchanged `0.1.0`, which left two hours of output from before and after the
change indistinguishable by the one line put there to distinguish them. The
announcement rule above covers the library's *files*; this covers the tool's own
*output*, which nobody else was going to notice.

The rule reads as being about mirrored files, and on 2026-10-02 that reading was
too narrow. `zotero_attach.py` writes nothing into the mirror, so seven commits
went out under an unchanged version — and the library session, comparing one dry
run against another after two fixes, had to track which was which by quoting
commit hashes in a message. Those run logs are kept; they are the record of 2,740
writes to a live library, and nothing in them said what produced them. **If a
run's output is saved, it is a file, whatever it is printed on.** So a script
that writes to someone's account prints `__version__` in its header and takes
`--version`, and a change to what it does bumps it.

Third: announce a contract change **before the working tree carries it, not
before the push**. The clone is what the systemd timer runs, so a saved file is
already live — there is no staging step between editing and shipping, and the
next `:07` is the deadline.

**The same is true of the library session, and it is easier to forget.** There is
one clone on this machine, `~/Git/offprint`, and that session runs its scripts
from it by absolute path. So it never pulls, there is no version boundary between
an edit here and a run there, and a run started a second after a save is on the
new code with nobody having done anything. Telling it to "pull before you run" is
worse than useless: it is a no-op dressed as a safety step, and this session said
it repeatedly on 2026-10-02 before Cameron pointed out that the clone is local.
The row above is about the Windows laptops, which do pull and which is why the
sentence reads that way.

What follows is that **the version a run prints is the only record of which code
it used** — not a convenience for labelling logs. And that a long run is on the
code as it stood when Python read the file, so editing during one is safe for
that run and silently changes the next. The 09-24 namespacing was announced with 45 minutes
to spare and the library session found a consumer of `citekeys.json` that the
repo could not see: a procedure in its own notes that would have started
handing Mendeley a namespaced id and getting a 404, months later, with nothing
left to connect the two.

## Entry points

`mendeley_mirror.py` is the refresh and the module everything else imports.
`get_pdf.py`, `refs.py`, `inbox.py`, `mendeley_push.py`, `mendeley_edit.py`,
`finding.py`, `pdbrefs.py`, `pdbxref.py`, `doixref.py`, `zotero_migrate.py`,
`zotero_source.py`, `zotero_attach.py`, `zotero_edit.py` and `zotero_inbox.py`
are separate CLIs that reuse its `Mendeley` client,
`config_dir()`, and `DEFAULT_OUT`. `finding.py` and `pdbrefs.py`
touch neither Mendeley nor the network; `pdbxref.py` queries RCSB and
`doixref.py` queries Crossref, both public and unauthenticated, and neither
writes anything — `doixref.py` caches to `~/.cache/offprint/` precisely so that
a read-only audit never touches the library.

The `.bat`, `.sh`, and `.vbs` launchers are thin — keep `run_mirror.sh` and
`run_mirror.bat` in step when either changes. Neither scheduled entry point may ever
prompt or pause: `refresh_quiet.bat` is what a Windows scheduled task runs
(`install_schedule.bat` sets it up), and `run_mirror.sh` is what a systemd user
timer runs on Linux. Which one owns the hourly refresh is a per-machine fact that
has changed before, so check it rather than assuming it.

## Direction of travel

Since 2026-10-02 **Zotero is the remote and Mendeley is retired**, so the three
`mendeley_*` writers have no live account to write to and `~/Sync/library` is a
frozen snapshot — `--retire` makes it say so. The rules below still describe how
they work, because the next backend's writers are built in their image and
`mendeley_edit.py`'s three properties are the reason `zotero_edit.py` has three
of its own.

The refresh is strictly one-way: Mendeley to disk. A bad run can lose mirrored
files but cannot touch the library. Five scripts break that on purpose —
`inbox.py` attaches files to references and can create them, `mendeley_push.py`
POSTs a new reference, `mendeley_edit.py` PATCHes fields on a reference that
already exists, and `zotero_attach.py` uploads the archive into Zotero. The three
Mendeley ones are interactive by default; `--dry-run` is the safe thing to run and
to show someone, and `--yes` is for a run a person has already approved, not a way
past a prompt. **All four act on someone's real library, and this session is not
the one that runs them.**

`zotero_attach.py` is dry-run by default instead, because a per-file prompt
across 2,740 files is not a safeguard. It never creates an attachment item: where
Zotero has no `imported_file` child, it reports rather than inventing one, and
where a record's attachments cannot be paired unambiguously it uploads nothing
for that record. Both are the same rule — guessing writes a paper's PDF under
another paper's citation key, silently.

It also PATCHes each attachment's `filename` and `contentType` to match the
archived file. **That is the second thing in this repo that can destroy correct
metadata**, so it carries the same three properties as `mendeley_edit.py`:
Zotero's PATCH is a partial merge so unnamed fields survive, only differing
fields are sent so a re-run writes nothing, and a content type is never invented
— bytes that are not a PDF keep the type the item declares. A 412 is a
concurrent edit: report it and leave the item alone. Never re-read and retry,
which would overwrite the edit that caused the conflict.

`zotero_edit.py` is `mendeley_edit.py` for the live backend, and **the dangerous
field moved**. Mendeley kept DOI, ISSN and PMID in one nested object its PATCH
replaced wholesale, which is why `identifiers` is merged by hand there. Zotero's
PATCH is a top-level partial merge, so those are separate fields and that hazard
is gone — and it reappears in `creators`, a list that a PATCH replaces entire,
editors included. So an edit replaces only the creator types it mentions and
keeps the ones it does not. Don't "simplify" that into assigning `creators`
directly; it drops an editor the first time someone fixes an author list.

**Its edits file is not `mendeley_edit.py`'s**, whatever the similarity
suggests: `authors` is `creators`, `first_name`/`last_name` are
`firstName`/`lastName`, and the identifiers are separate top-level fields. This
repo claimed otherwise on 2026-10-02 and the claim reached a peer about to run
`--yes`. Mendeley's field names and person shape are both refused by name now,
with the translation in the error.

Its third property is new and belongs to this backend: **an unknown field name
is an error.** Zotero returns every field valid for an item's type including the
empty ones, so a name that is not already a key of `data` is a typo — and a typo
that reached Zotero would be ignored server-side, which is a silent no-op
wearing the clothes of a successful edit.

**`zotero_inbox.py` creates both kinds of Zotero item**, a child attachment
and — since 0.29.0 — the record under it when none exists. It reuses `inbox.py`'s
identification pipeline unchanged, which was always backend-agnostic; only the
create-and-upload half was Mendeley's.

Separating the two creates was deliberate and was reversed deliberately, which
is worth reading before re-separating them. The separation guarded against a
second record for a paper already held — but the check behind it read
`library.bib`, which only knows what the last refresh saw, so a record pushed
an hour earlier was invisible to it anyway. The separation cost two full
refreshes per paper and closed nothing. `zotero_doi_index()` asks **Zotero**
before creating, which is what actually closes it and is what `inbox.py` always
did for Mendeley. Four refusals keep the create narrow: no record without a
DOI (a title match may attach, never mint — the metadata would be guesswork),
never bytes a record already holds (md5, checked before anything is created),
never a PDF with no readable text, never a guess at which record. `--no-create`
restores the old refusal.

**Record metadata goes through `from_doi`, not through the Crossref message the
script is already holding.** That looks like a wasted request and is not:
`from_doi` is where `csl_year()` lives, and a second creator of records doing
its own date handling is a second chance at filing an Advance Access paper
under its online year, which is how CHARMM36m was cited as 2016.

**`zotero_delete.py` is the only operation here with no undo**, so it is the
most guarded. A dry run names what **survives** as well as what goes, because
the dangerous deletion is the sibling nobody listed; a deletion that would leave
no record sharing that title is refused unless `--allow-last-copy` is passed.
Siblings match by title rather than by key shape, since `assign_citekeys`
suffixes only when it must. The backup is automatic and goes to the mirror
rather than a cache. It touches neither `citekeys.json` nor `pdf/` nor `text/`,
and `--stubs` identifies a byte-less attachment by asking the **file endpoint**
— never by `md5: None`, which this repo already read wrong once.

`zotero_migrate.Zotero` is read-only and its docstring says so. The write client
lives in `zotero_attach.py` for exactly that reason: adding a write method there
would retire a guarantee every other caller is relying on. `zotero_edit.py`
imports that client rather than growing a second one.

**`mendeley_edit.py` is the one to be most careful with**, because it is the only
one that can *destroy* correct metadata rather than merely add wrong metadata.
Two properties exist to limit that and should not be removed: `identifiers` is
merged rather than substituted, so fixing a DOI cannot silently drop an ISSN or
PMID; and a field already equal to Mendeley's value is skipped, so a re-run after
a partial failure is safe. A third exists to make it complete: **an explicit
`null` removes a field**, because the merge alone left a wrong identifier
undeletable, and a DOI resolving to an unrelated paper is worse than no DOI.
Mendeley's PATCH replaces the whole `identifiers` object rather than merging it
server-side — verified against a live record — which is what makes removal work.
Edits are keyed by citation key and resolved through `citekeys.json`, so a key
the mirror has never seen is an error, never a no-op.

Fixing a year does not renumber anything: `assign_citekeys` assigns a key once
per document id and keeps it. Expect `Abrams2013Enhanced` to carry `year = 2014`
and leave it that way — the key is a handle, the year field is the claim.

## Acquisition: the year, and what "distilled" means

Publishers block automated downloads, so fetching a paywalled PDF stays a human
job. That is a boundary, not a limitation to engineer around — don't script a way
past a paywall. The two acquisition paths that write to the live account are
`inbox.py` (a PDF the human saved into `<out>/inbox/`) and `mendeley_push.py` (a
reference with no PDF behind it).

`zotero_push.py` is the live one; `inbox.py` and `mendeley_push.py` are its
retired Mendeley counterparts, and `zotero_inbox.py` files a PDF onto a record
that already exists. All take the **issue** year through `csl_year()`, never
Crossref's `issued`.
`issued` is the date a work first appeared *online*, so an Advance Access paper
arrives a year early — that is how CHARMM36m was filed as 2016 and cited that way
in a manuscript before anyone noticed. If you add another acquisition path, call
`csl_year()` rather than reading a date-part yourself.

The next refresh then distills a filed paper: text extracted to `text/<citekey>.md`
with `<!-- p. N -->` markers, highlights to `annotations/<citekey>.md`, and the PDF
deleted. **That extract is the durable artifact and all that survives, which is why
extraction is deterministic** — a paraphrase written into it would be
indistinguishable, later, from what the paper actually said.

Two implementation notes that exist because of real failures:

- **A status is the verdict of the reader that produced it.** `no-text` means
  the plain reader found nothing; `--ocr` is a stronger reader whose whole
  purpose is to re-read exactly those. Treating the weaker verdict as final made
  the flag a no-op — an OCR recovery run on 2026-10-02 reported "0 extracted,
  2730 unchanged" and reached none of the 110 scans it existed for. So a run
  carrying a capability the previous run lacked must not inherit its verdict.
  `not-pdf` is deliberately exempt: OCR does not help a video file.
- **A run that reads nothing must not delete what an earlier run read.** On
  2026-10-02 a re-extract without `--ocr` read 0 characters from 108 image-only
  scans and unlinked all 108 OCR'd extracts — a deliberate pass's work, gone,
  with nothing warning. The state could not help: under a new backend the prior
  entry is keyed by a different id and knows nothing of the OCR run. **The
  extract itself is the only durable evidence, which is why it carries
  `ocr: true`**, and the guard reads the file rather than the state. Same family
  as the 0.11.0 archive erosion — a run destroying an artifact it did not
  create, on the assumption that its own result is authoritative.
- **An extract can exist and say nothing.** Some attachments are image-only scans
  whose only text is a stamp (`Reproduced with permission of the copyright owner`)
  repeated on every page. `content_chars()` discards lines that repeat across most
  pages and judges what is left; a stamped ProQuest scan had cleared the old
  per-page threshold by 1.3x. Papers filed before that check still carry the old
  extracts — `stamp-only-extracts.tsv` in the mirror lists the ones found so far —
  because the state cache skips an attachment whose filehash has not changed.
- **Every attachment on a record is extracted**, and all but the first are
  suffixed, so `text/<key>-2.md` is a second file, not a second paper. Anything
  that reports extraction results must say which it means.

## Attachment order is a contract

Position decides the archive stem — `<key>.pdf`, then `<key>-2.pdf` — and
therefore which attachment `text/<key>.md` is the extract OF. Until 0.29.0 that
order came from `items/<key>/children`, preserved by accident because the same
endpoint answered the same way twice.

0.29.0 replaced 2,739 `children` requests with **one** `items?itemType=attachment`
query, which is where a 13-minute refresh for two changed attachments was
going. A bulk query has no per-record order, so `order_by_recorded_stems()`
reimposes it from the `stem` recorded in `state.json` — the only authority that
cannot drift, since it is what the files on disk were actually named for. New
attachments go last, because first position is already spoken for by the file
sitting in `text/<key>.md`. Two attachments claiming one position is reported,
never silently resolved: `pairings.tsv` is where that decision gets recorded.

Don't re-derive the order from the API, however stable its sort looks. The
cost of being wrong is two papers' extracts swapping names, silently, under
names other sessions already quote page numbers out of.

`--only` narrows the **extraction** and nothing else. `library.bib`, `index.md`
and `folders.json` are still rebuilt whole, because they are regenerated from
the full document and attachment maps — narrowing those maps would rewrite the
index with every unnamed record's attachments missing. It also leaves
`mirror-status.md` alone: that file says when the mirror was last verified, and
a run that examined three records out of 2,780 does not get to reset it.

**It does not make an ordinary refresh faster, and it was built believing it
would.** Measured 2026-10-05 on the live library: full 203.1 s, `--only` one
record 199.4 s. Extraction is 0.1 s of that, because skipping an attachment
whose filehash is unchanged costs nothing — the time is in three API fetches
that `--only` cannot avoid and must not avoid. Keep that number next to the
flag, or the next reader will reach for it to speed up a refresh.

What it is actually for is bounding an **expensive** re-read: `--ocr` or
`--reassess` over named records rather than 2,780. That is the bounded
`--reassess` this ROADMAP recorded as deliberately not built, and it arrived as
a side effect of building the wrong thing.

## A state entry outlives its attachment

`state.json` is keyed by attachment id and nothing removes an entry when the
attachment goes. On 2026-10-05 four carried `status: failed` with a 404 from
the file endpoint, and `library` confirmed all four permanently deleted from
Zotero — residue from the 11 duplicate records pruned on 10-02.

**The bug was not the residue. It was that residue and a live failure read
identically.** `failed` is deliberately outside `DONE_STATUSES` so a failed
attachment is retried next run — but a key absent from the attachment listing
is never visited, so the retry can never fire, and the run reports `0 failed`
while the file holds four. Both readings fit the evidence, which is the whole
problem. A refresh now names them.

`--prune-state` removes them, and is **guarded against its own coupling**: the
stale set is computed from *this run's* listing, so a listing that came back
empty or short would condemn state for a library that is perfectly intact and
cost a full re-extraction of every attachment. An empty listing is a broken
run, never an empty library — that is the same reasoning as "a pass that does
no work does not get to report success", applied to a pass that would destroy
something. `--only` cannot prune either: it does not speak for the mirror, and
pruning is a whole-library claim.

Report by default, destroy only when asked, and refuse when the input that
justifies the destruction looks wrong.

## Tests

```
uv run --script test_mirror.py
```

Offline throughout: pure functions plus a stubbed API, no network, no account.
That is the test surface for this session — it needs no credentials and touches
nothing live. Anything touching BibTeX escaping, citation-key generation, or
annotation rendering should get a case there — those are the parts whose output
other people's files already depend on.
