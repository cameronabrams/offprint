# Roadmap

What this tool is likely to do next, why, and — for the things deliberately not
being done — what would change that. Items move by being finished or by being
struck out with a reason; nothing here is a promise about a date.

---

## 1. Zotero as a second backend, then the primary one

**Why.** Mendeley's API is running on inertia. The developer portal's most
recent content is dated **2 November 2020**, and the last substantive change was
the February 2021 round that *removed* endpoints. There is no sunset notice and
no sign of maintenance either. Meanwhile Elsevier encrypted the local Mendeley
database, so Zotero's local importer only works against Mendeley Desktop 1.18,
from before the encryption; the supported migration path is an **online import
through the same API this tool depends on**. The escape route runs through the
thing that might fail, which is the argument for not being caught flat-footed.

Zotero, by contrast, is developed by a non-profit whose whole team it employs,
is open source, and keeps its data in a local SQLite database you can read
without asking anyone. The longevity case is not that the organization will
outlive Elsevier — it is that if it did not, the data is still readable.

**Exposure today is low and should be stated plainly**, so this is done
carefully rather than urgently: the mirror is one-way, so `library.bib`, the
page-marked extracts, `annotations/` and `findings/` are already a complete,
service-independent corpus. If the API went dark tonight, what stops is refresh
and filing — not the library.

**The hard part is not the API. It is the citation keys.**

`assign_citekeys` assigns a key once per *document id* and never changes it, and
those keys are cited in manuscripts and are the file names under `findings/`.
A naive re-import under Zotero item ids would reassign all ~2,700 of them: every
citation in a draft would rot and every findings file would orphan. So:

- [x] Namespace the identifiers (`mendeley:<id>`, `zotero:<id>`) in
      `citekeys.json` and in `state.json`, which is keyed by Mendeley *file* id.
      **Done 2026-09-24**, before any key is assigned from a second source, which
      was the whole point — it is impossible to retrofit safely afterwards.
      Migration is automatic and idempotent; a bare id still reads as Mendeley's,
      so a map written by an older version stays readable. Verified against the
      live library on a copy: 2,727 of 2,727 keys resolve to the same paper, no
      key text changed, and 2,733 of 2,733 attachment states keep their filehash,
      so nothing re-extracts.
- [x] Write the migration map by matching old records to new: DOI first, then
      title+year, then by hand. Report the unmatched rather than guessing —
      roughly 17% of this library's records carry no DOI.
      **Done 2026-09-29**, `zotero_migrate.py`: **2,739 of 2,739**, nothing left
      by hand. DOI 2,291 · title+year 405 · duplicate group 36 · title 4 · fuzzy
      title 3. The map is a bijection, and its keyset is exactly `library.bib`'s
      — checked by the library session against the `.bib` rather than against
      `citekeys.json`, so it is not the same list agreeing with itself.

      Verified against fields the matcher never reads, **over all 2,739 pairs**:
      first-author surname agrees on 2,738, and the one that differs is a
      mangled name in the source (`Binder, M.Woods, L.`, a missing separator,
      split differently by each side). Year agrees on 2,738, with one record
      carrying no year on either side.

      The first run of that check reported 2,731 of 2,736 and 2,735 of 2,735,
      which is the failure this file keeps recording in other forms: **a
      denominator that shrinks to fit the numerator reads as a perfect score.**
      Both gaps were the checker's, not the map's — it folded diacritics by
      deleting the letter, and it read Zotero creators only where
      `creatorType == "author"`, which silently dropped all three of this
      library's patents: `Abrams2016Compositions`, `Antczak2003Methods` and
      `Brinn1969Purification`, each `itemType: patent` in Zotero, with `inventor`
      creators, a null `date` and the year in `issueDate`. Named here so the
      claim is checkable, which is how the library session confirmed it: all
      three are in `library.bib` with author and year present. Every record in
      the `.bib` except `BenkikindYaml` carries both, so the skips were the
      Zotero-side read and never missing data. See item 7 for why they were hard
      to find from the `.bib` side.

      A check that skips a record must say how many it skipped, in the same
      breath as the score.

      Two things made the difference between 98.5% and 100%. Zotero's import
      writes `&#8208;` where the BibTeX has a hyphen, so normalizing punctuation
      before entities left `8208` standing as a word in otherwise identical
      titles — half the initial misses. The rest are records filed two or three
      times in Mendeley with no DOI; where both sides hold the same number of
      copies they are interchangeable and get paired in a stable order, and where
      the counts differ nothing is guessed.
- [x] Assert, as a test, that every pre-migration citation key still resolves to
      the same paper afterwards. This is the acceptance criterion; everything
      else is mechanics. It is in `test_mirror.py` and it covers the case that
      matters next: entries from a second backend added to an existing map must
      not renumber the first backend's.

**The mechanics really are mechanics.** The Mendeley-specific surface in
`mendeley_mirror.py` is one base URL, one media type string, one `Mendeley`
class and two `document_id` references. Extraction, page markers, BibTeX,
annotations and findings all work on a generic document/file/annotation shape.

- [x] **Test the escape hatch before relying on it** — run Zotero's online
      importer once and confirm items, attachments and annotations arrive intact.
      An exit nobody has tested is not an exit.
      **Done 2026-09-28**, into the real library rather than a throwaway, because
      by then it was the migration and not a rehearsal. 2,739 records, and the
      four Mendeley folders arrived as subcollections of an import wrapper — so
      `folders.json` keys gain one level of prefix under Zotero. Attachments:
      2,745 real files (2,739 PDF + 6 other), which is exactly what the mirror
      holds, counted independently. The 2,894 the API first reports include 149
      `linked_url` bookmarks that are links, not bytes.

      Annotations were the one thing that did not come across cleanly, and it
      does not matter: Mendeley held 15, all of them `(p. 0)` publisher strings
      like `Publisher: Royal Society of Chemistry`. Not one highlight in the
      library. Zotero shows 2 annotations and 14 notes, the same nothing.

      The real size was **4.48 GB** (4.17 GiB — `du -h` prints the second and
      storage tiers are sold in the first), so Zotero's 6 GB tier covers it. It
      may not be needed at all: with file sync off the API returns **404** for
      `/items/<key>/file`, which is the measurement that settles the backend
      shape — records from the API, PDFs from local disk.
- [ ] Abstract the client behind the small interface the rest of the code
      already implies: list documents, list files, fetch file, list annotations.
- [ ] Decide what "primary" means for the writing scripts (`inbox.py`,
      `mendeley_push.py`, `mendeley_edit.py`). They may only ever target one
      account; pointing them at the wrong one is the expensive mistake.
- [ ] **Decide whether the first Zotero refresh re-extracts everything.** It
      will by default, and that is not a small default. `state.json` is keyed by
      attachment *file* id; Zotero's ids are not Mendeley's, so every attachment
      reads as new, `prior` is empty, the filehash cannot match, and all ~2,700
      are downloaded and extracted again. Checked, not assumed.

      That is not harmful — a failed extraction leaves the existing text file
      alone — but it decides a real question. Re-extraction needs every PDF
      readable, which means either Zotero File Storage (the API serves files only
      from there, not from WebDAV or linked files) or the files on local disk.
      **The alternative is an adoption path**: where a citation key already has
      `text/<key>.md` and the new attachment is otherwise unknown, record the
      state entry as adopted instead of re-extracting. That trades verification
      for bandwidth, and the citation key is the anchor either way.

      Re-extracting also regenerates every extract with current code, which
      would clear the 2,459 carrying control characters as a side effect. The
      adoption path does not, and those two facts have to be decided together.

---

## 2. Make a failing refresh loud

**Why.** The failure this tool is least equipped to notice is silent
authentication death: the timer keeps firing, `mirror-status.md` keeps being
rewritten, and a reader three weeks later sees a file that *looks* maintained.
Every claim about mirror state has a shelf life of under an hour, and nothing
currently shouts when that stops being true.

- [x] Distinguish an auth failure from a transient network failure in the status
      file and the log, and say which. `classify_failure` returns auth / network /
      interrupted / other, and each gets different advice. Failing to *reach* the
      token endpoint classifies as network, not auth, which is the confusion this
      item was written about.
- [x] Surface consecutive-failure count, so "failed once" and "has not succeeded
      in nine days" do not read the same. `.mirror/health.json` holds the streak
      and its start; one success clears it.

**Brought forward 2026-09-24.** Cameron's Mendeley membership auto-renews
2027-01-01 and cancelling is the plan, so the account's behaviour will change on
a known date. An auth failure arriving as a shrug was a general risk when this
item was written; with a cancellation date it is a specific one.

Still open, and now the interesting part: **nothing reads the status file unless
a person opens it.** The streak is recorded and legible, but a mirror that has
not refreshed in nine days still announces it only to whoever looks. That is
acceptable while the library session reads the file routinely; it stops being
acceptable the moment nobody does.

This matters more given item 1: an auth failure is exactly the signal that says
*migrate now*, and it should not arrive as a shrug.

---

## 3. Continuous integration

**Why.** 220 offline checks that pass on one laptop and nowhere visible. Now
that the repo is licensed and published, a green badge is the cheapest possible
evidence that it works, and the test suite needs no credentials and no network.

- [ ] A GitHub Actions workflow running `uv run --script test_mirror.py` on push.
- [ ] Run it on Windows as well as Linux — the Windows launchers exist precisely
      because that platform behaves differently, and nothing tests them.

---

## 4. Better page-offset derivation

**Why.** Re-measured 2026-09-23 against the 2,425 extracts whose bib entry
records a numeric first page — a larger set than the 2,129 the `derive_offset`
docstring quotes, because the library has grown, so the two are not directly
comparable. The edge scan derives 2,109 offsets: **1,789 right, 320 wrong**, and
refuses 316. Every one of those 320 is a plausible-looking journal page that
nobody will re-check.

**What the 09-21 tie-refusal cost, which was never measured at the time.**
Before `d902a36` the scan derived 2,322: 1,903 right and 419 wrong. So that fix
removed 99 wrong derivations and 114 correct ones — precision 82.0% → 84.8%,
and **137 papers that used to derive correctly now refuse**. The trade is
defensible (a refusal is loud, a wrong locator is silent and gets copied into a
manuscript) but it was made blind, and `literature` found it as a regression
against a locator its own findings file already carried: `Knight2015Memgen`,
derived +2896 on 09-09, refused on 09-23.

Of those 137, `confirm_offset` accepts the true first page for **120** once the
operator passes `--journal-page`. Seventeen still need a hand-recorded locator.

- [ ] Recover the 17 the confirm path cannot reach.
- [ ] Treat the bib page range as evidence when the record has one. The
      derivation currently ignores a number the library already knows — and it
      is the cheap oracle that would have caught this regression on the day.
- [x] Regression corpus — **built and run 2026-09-23 by `literature`**, which
      proposed it. Every `## N · FINDING ·` record in its `findings/*.md` carries
      a (citekey, marker page, printed page) tuple checked by hand when it was
      written: 98 records across 57 files, an oracle this tool did not generate.
      Replayed against `53cedcc`: 89 agree, 4 disagree, **0 lost, 0 gained**, 5
      bare-marker records with nothing to compare. No paper that carried a
      printed page lost one.

      All four disagreements are papers the 09-21 hand audit had already caught
      and retracted — Ikeno2011Molecular, Lin1999Effect, Hamerton1996Molecular,
      Kendrick1990Calculated. Four of four known positives, zero false alarms,
      and the first time the code and that audit name the same four papers.

      It lives in the library session because that is where the findings are.
      This repo's own suite cannot replace it: `test_mirror.py` fixtures are
      written by whoever writes the code, and three of them this month encoded
      an assumption rather than a fact. An oracle built by the tool's *user*,
      before the change existed, is the only kind that can contradict it.

**Measured and rejected 2026-09-23:** widening the edge window from characters
to whole lines (first and last *n* non-blank lines, in addition to
`EDGE_CHARS`). It recovers real papers — 1,880 correct at *n*=2 against 1,789 —
but carries 368 wrong against 320, so precision falls to 83.6%. Deriving is the
silent path; buying recall with precision there is backwards. The same insight
*was* a clean win in `confirm_offset` (`53cedcc`), where the human supplies the
hypothesis and the wrong-claim rate did not move at all.

---

## 5. Mark a proof extract at extraction, not at inspection

> Ranked below the orphan hazard in item 6 after testing, not before.

**Why.** Fourteen extracts in this library carry placeholder pagination —
`xxx–xxx`, `XXXX, XXX, 000–000` — because the PDF is an accepted proof rather
than the version of record. Nothing says so. `get_pdf.py --attachments` lists
them and names which of a pair to cite, but that is a report someone has to run;
a proof filed on its own still reads as a normal paper.

Fourteen is this pattern's count, and it is not obviously the right one. A
tighter pattern run by the library session found five, of which it judged one
spurious — but that one, `Price2008Hydrogen`, is genuine: the line is the
article's own ASAP footer, `Biochemistry XXXX, xxx, 000–000 … Published on Web
05/22/2008`. Every one of the fourteen sits beside a journal name and a DOI on
inspection, which is what an article-level footer looks like and not what a
reference to someone else's in-press paper looks like. That is evidence, not
proof, and the number should be settled by the extraction-time check below
rather than by comparing two regexes.

The consequence is specific: an extract's own front matter invites a reader to
cite it, and the page markers of a proof are not the journal's pages. It is the
same failure family as the OCR'd scans, where `ocr: true` and a banner were
added precisely so a machine-read extract could not be quoted as if typeset.

- [ ] Detect placeholder pagination during extraction and record it in the front
      matter, the way `ocr: true` is recorded, with a line in the extract saying
      the page numbers are not citable.
- [ ] Announce it first: this changes `text/<key>.md`, which is a contract, and
      it only takes effect on re-extraction.
- [ ] Consider whether `finding.py` should say so when asked about a proof. It
      does **not** currently derive a wrong page from one — that claim was made
      here without running it, and it is wrong. Tested on five of the ten proofs
      filed alone: all emit a bare `locator: marker p. 1`, and an
      operator-supplied `--journal-page` is refused, because `000–000` is not a
      number the scan can match. The confirm-only design already fails safe. No
      proof in this library has produced a locator in any findings record.

      So this item is worth doing for the **label**, not to close a live
      wrong-locator path. Priority accordingly.

---

## 6. An orphaned extract can be the only citable copy

**Why.** `Shan2011How-3` is both the orphan and the only copy with real page
numbers. The extract that cannot be re-fetched is the published version; what
Mendeley still holds is the proof. Deleting it would lose no *content* — the pair
is 99.3% alike — only the **pagination**, which is the one thing that differs and
the only reason anyone wants that copy. `Caparco2018Effect` is worse in a
different way: its base is a proof and its `-2` is supporting information, so the
record holds no citable copy at all.

`get_pdf.py --attachments` now says `IRREPLACEABLE` for the Shan shape, which
inference it states as one — the file listing does not say which attachment went
missing, so it is read from the numbering.

- [ ] Nothing *protects* such an extract. The re-extraction procedure that fixed
      the NUL bytes deletes `text/<key>.md` to force a rebuild, and doing that to
      an orphan destroys it. That procedure grew a restore-from-backup step for
      exactly this reason, but the protection lives in a one-off script rather
      than in the tool.
- [ ] Detect the no-citable-copy case (`Caparco2018Effect`): every extract of a
      record is a proof or supporting information. Needs an SI heuristic, which
      is a second guess stacked on the proof one — worth measuring before
      shipping.

---

## Renamed, and what still carries the old name

The project became **offprint** on 2026-09-21, in anticipation of Zotero: a module
called `mendeley_mirror` that also talks to Zotero would be actively misleading.
An offprint is a separately printed copy of one article, pulled from the issue and
kept — which is what this produces, and what survives when the service does not.

Changed: the GitHub repository, the clone directory, the documentation, and the
name the tool prints in `mirror-status.md` and `--version`.

**No longer deliberate: the config and cache directories.**

`~/.config/mendeley-mirror` was kept on the grounds that the path is invisible to
users, so renaming it buys nothing against the cost of a credential migration.
**That reason expired on 2026-09-29**, when the answer to "where does the Zotero
API key go?" turned out to be a directory named `mendeley-mirror` and Cameron
asked whether we were married to the name. A path that names the service its
credentials belong to is not invisible; it is wrong as soon as it holds two.

What happens instead of a migration:

- **New credentials go to `~/.config/offprint/`**, starting with `zotero.json`.
- Mendeley's `config.json` and `tokens.json` **stay exactly where they are**.
  Nothing moves, so the hourly refresh and the OAuth tokens are never at risk —
  re-authorizing is a real cost against an account that lapses 2027-01-01.
- `config_dir()` reads the new location first and falls back to the old, which
  turns the eventual cleanup into a deletion rather than a migration.

Two config directories side by side is a smell, and a bounded one: it resolves
in the backend abstraction in item 1, which is where `mendeley_mirror.py`'s own
rename already belongs, for the same reason — one refactor rather than two.
`~/.cache/mendeley-mirror/pdf` moves in that same change; it is already
backend-agnostic in everything but its name.

**Still called mendeley-mirror, deliberately:**

- The systemd unit `mendeley-mirror.service`/`.timer`, which is referenced from
  the file-inbox skill, the library's own CLAUDE.md, and `inbox.py`'s
  `REFRESH_UNIT`. Renaming it is a stop/disable/enable dance on the one job that
  must not silently stop.
- The Read the Docs project, so the published URL is still
  mendeley-mirror.readthedocs.io. Renaming it there changes a URL the README
  advertises.
- `mendeley_mirror.py` and the `from mendeley_mirror import` in eight scripts.
  **This one is deferred on purpose:** it belongs in the same change as the
  backend abstraction in item 1, so there is one refactor rather than two.
  `mendeley_push.py` and `mendeley_edit.py` keep their names permanently — they
  write to Mendeley specifically, and that stays true.

## 7. The BibTeX writer drops the venue, and some references cannot be cited

**This is the most urgent item on this list**, and it is numbered last only
because the numbers are referenced from `test_mirror.py`. It is a correctness
defect in `library.bib`, which is the tool's primary output and which two other
sessions build bibliographies from.

`bibtex_entry` emits `source` for three of seven entry types — `article` takes it
as `journal`, `inproceedings` and `incollection` as `booktitle` — and there is no
`else`. For `@misc`, `@techreport`, `@phdthesis` and `@book`, **66 records here**,
it is discarded without trace.

The result is not an entry that looks incomplete. It is an entry that looks
complete and cannot be cited:

- Across the whole file, **68** records carry `volume`, `number`, `pages`, `doi`
  or `issn` and no `journal` or `booktitle`; **40** carry citation apparatus and
  no venue field of *any* kind — not `publisher`, not `school`, not
  `institution`. **54** and **28** of those are in the four types that drop
  `source`, which is the part this item can fix.
- `Berman2000Protein` is the Protein Data Bank paper. The entry gives volume 28
  and number 1 and names no journal, so a bibliography prints `28(1)` with
  nothing to put it in.
- `Park2004Calculating` carries volume, number, pages, DOI, ISSN and PMID, and
  does not say *J. Chem. Phys.*
- `Snow1991Dilatometry` is reduced to author, title and year. What it lost was
  `NRL Memorandum Report 6848, Naval Research Laboratory (DTIC ADA239276)`.

**The other 14 are not this bug, and the acceptance criterion has to say so.**
Twelve `@article` and two `@incollection` records also have no venue — but the
writer *does* emit `source` for those types, as `journal` and `booktitle`. Their
venue is missing because Mendeley holds none, so fixing the writer cannot touch
them. Anyone re-measuring "records with no venue" after the fix will still find
14 and may read it as the fix having half-worked.

So the criterion is **not** "no record lacks a venue". It is: *every record whose
backend `source` is non-empty carries that text somewhere in its entry.* Measured
against the backend, not against the output — the output is the thing under test,
and a test that reads only its own subject cannot fail in the interesting
direction.

**And not measured from the extracts either**, though they are on disk, faithful
and carry `source:` in their front matter, which makes them the obvious shortcut.
**71 of the 2,739 references have no `text/<key>.md` at all** — among them
`Abraham2015Gromacs`, `Berendsen1995Gromacs`, `Abrams2004Dual` and
`BenkikindYaml` — and **five of the 71 sit in the four source-dropping types**.
A front-matter sweep would therefore test 61 of the 66 records this item exists
to fix, find them all good, and report a pass it never ran on the rest. Either
query the API, or state the 71 as unmeasured. The five, so the criterion has
something to be checked against:

| citekey | type | apparatus in the entry |
|---|---|---|
| `BenkikindYaml` | `@misc` | none |
| `Bindermwoods2009Comparative` | `@techreport` | none |
| `Delia1998Kognitiv` | `@misc` | volume, number, pages, issn |
| `Ribeiro2020Psfgen` | `@misc` | none |
| `Shirts2004Calculating` | `@phdthesis` | none |

Two of those are already known from the other direction: `BenkikindYaml` is the
single record with no year on either side of the Zotero map, and
`Bindermwoods2009Comparative` is its single author disagreement — `Binder,
M.Woods, L.`, a missing separator that also built the citekey. **A record
surfacing in two independent audits is worth one repair, not two.** This is the third distinct shape of
the same failure in one afternoon — a probe that cannot find what the format
discarded, a denominator that shrank to fit its numerator, and now a sample that
quietly omits the cases under test — and the only defence that has worked each
time is naming the denominator out loud.

The 14 are named in the library session's notes; four are damaged records rather
than sparse ones, and two more (`Abrams2004Dual`, `RAUCHER1983Sorption`) have
venues recoverable from Crossref on the DOI they already carry. All of that is a
Mendeley edit and the library's repair — **item 7 does not inherit it**, and a
writer change must not be judged by whether those records improved.

**And the library's own filing procedure writes into the field the writer
discards.** `<out>/CLAUDE.md:279`, on grey literature:

> Put the accession number in `source` — there is no identifier field for it, and
> it is what someone needs to find the thing again.

Reports, theses and patents are exactly what become `@techreport` and `@misc`.
So the documented procedure puts the one thing that makes the reference findable
into a field the documented output throws away, and has been doing so on every
such record.

## The type collapse, which is the same bug seen from further back

`TYPE_MAP` (`mendeley_mirror.py:547`) collapses 14 Mendeley types into 7 BibTeX
ones, and nothing downstream records what was collapsed. **2,727 of 2,739
records — 99.6% — sit in a bucket that lost information.** Only twelve survive
one-to-one.

| BibTeX | count | Mendeley types collapsed into it |
|---|---|---|
| `@article` | 2,660 | journal · magazine_article · newspaper_article |
| `@misc` | 34 | web_page · computer_program · patent · generic |
| `@techreport` | 25 | report · working_paper |
| `@incollection` | 8 | book_section · encyclopedia_article |
| `@book`, `@inproceedings`, `@phdthesis` | 12 | one-to-one |

It is not recoverable after the fact: `.mirror/state.json` keeps extraction state
only — filehash, chars, pages, status — so the source type exists nowhere on disk
and can only be asked of the backend.

**Found by a probe that could not have succeeded.** On 2026-09-29 the library
session searched `library.bib` for patent-shaped records, grepping `patent`,
`assignee`, `inventor` and US-style numbers, and found none — because the writer
had already deleted every one of those tells. It reported a library with no
patents while holding three. This file's usual lesson is a check that cannot
fail; this is its mirror image, **a check that cannot succeed**, and the format
under it is what made it so.

**The patents are not the expensive case.** `@article` conflating a journal with
a magazine and a newspaper is 2,660 records, and a newspaper piece rendered as a
journal article is a wrong venue in someone's manuscript — the same class of harm
as the `issued`-vs-issue-year bug that filed CHARMM36m as 2016.

**And one mapping does the opposite: it invents.** Mendeley has a single `thesis`
type; `TYPE_MAP` renders it `@phdthesis`, asserting a doctorate the source never
claimed. Losing a distinction is bad; manufacturing one is worse, and it is the
failure the extraction rules already forbid in prose.

The library holds the demonstration. `Deserno2000Counterion` is a dissertation —
*Dissertation zur Erlangung des Grades Doktor der Naturwissenschaften*, Johannes
Gutenberg-Universität Mainz, 165 pp. `Deserno1999Efficient` is **chapter 3 of
it**, printed pp. 95–141, the offset verified at both ends by the library session
(extract marker p. 1 → printed 95, p. 47 → 141). Both carry `@phdthesis`, under
different years. On the chapter, that type asserts two false things at once: that
it is a thesis, and that it is a *separate* one.

**The bib is lossier than this tool's own extract, for data Mendeley already
holds.** Mendeley records `source` as `PhD Thesis, Ch 3` on that record, and
`text/Deserno1999Efficient.md` preserves it in its front matter. `library.bib`
drops it: the entry carries author, title and year, and nothing else.
`bibtex_entry` only emits `source` for three of the seven entry types — `article`
takes it as `journal`, `inproceedings` and `incollection` as `booktitle` — and
there is no `else`, so for `@misc`, `@techreport`, `@phdthesis` and `@book`,
**66 records in this library**, it is discarded without trace. The one record
where a human typed the disambiguating fact by hand is the one where the writer
throws it away.

- [x] **Emit `source` when no other field claimed it.** **Done 2026-09-29**,
      `0.10.0`. Anything not taken by `journal` or `booktitle` goes to `note`,
      which is valid in every standard entry type. `@article` and
      `@incollection` are untouched, so the 2,668 entries that were already
      right do not change. Demonstrated on the named records: `Berman2000Protein`
      gains `note = {Nucleic Acids Research}` beside its bare `28(1)`;
      `Snow1991Dilatometry` gains the full `NRL Memorandum Report 6848, Naval
      Research Laboratory (DTIC ADA239276)`; `Park2004Calculating` gains
      `Journal of Chemical Physics`. Real `bibtex` parses the result with zero
      warnings.
- [ ] Keep the source type whenever `TYPE_MAP` flattens it — **but not as
      written, and worth less than it looks now.** `note` prints. Putting
      `note = {journal}` on 2,660 `@article` entries would append the word
      *journal* to almost every reference in every bibliography built from this
      file, which is a larger harm than the one it fixes. And the box above
      already recovers most of its value: a magazine is told from a journal by
      its name, and the name is now in the entry. What remains is the case where
      the venue name alone does not settle it. That needs a field that does not
      print, which is a format decision rather than a bug fix, so it waits for
      one rather than being smuggled in beside a correctness change.
- [x] Stop asserting `@phdthesis` for an unqualified `thesis`. **Done
      2026-09-29**, `0.10.0`, and without changing the entry type: standard
      styles let `type` override the printed label, so the entry stays
      `@phdthesis` — which is what styles and `school` know how to handle — and
      carries `type = {Thesis}`. No style now prints a degree Mendeley never
      recorded. `Deserno1999Efficient`, which is chapter 3 of someone else's
      dissertation, comes out as `type = {Thesis}` with
      `note = {PhD Thesis, Ch 3}`: still the wrong entry type for a chapter, but
      no longer claiming a doctorate, and now saying what it actually is.
- [ ] Decide separately whether `@patent` is worth emitting. Base BibTeX styles
      do not define it and biblatex does, which is presumably why `misc` was
      chosen. Not losing the fact and rendering it are different questions, and
      only the first is urgent.

This is a change to `library.bib`, which other people's bibliographies are built
on, so it is announced before it ships and bumps `__version__` in the same
commit.

Separately, and the library's to judge rather than the tool's: none of the three
patent records carries a patent number in any field, so nothing in them lets a
reader find the document again. That is a defect in the records, not in the
writer, and the library session is raising it with Cameron.

## 8. A default refresh erodes the archive that --backfill built

`--backfill` (0.8.0) put 2,745 attachments, 4.48 GB, into `<out>/pdf/` so the
library would survive the Mendeley account lapsing on 2027-01-01. A default
refresh — `--attachments text`, which is what the timer and every scheduled entry
point run — deletes from that directory:

```python
if mode == "text" and local.exists():
    local.unlink()   # the text is the artifact; Mendeley keeps the PDF
```

That comment was true when it was written and is now false — but not because of
the 2027 lapse, which is how this was first argued and is the weaker case. The
defect stands without any expiry date: `<out>/pdf/` is an archive somebody built
deliberately, and a refresh deletes from it. The lapse only sets a deadline on
noticing.

**On a static library the loss is zero**, which is what makes it easy to miss.
The skip branch fires when the filehash matches and the extract exists, and
`continue`s long before the unlink, so all 2,745 currently skip. The exposure is
exactly the attachments that changed in Mendeley since the last run: each one is
reprocessed, and its archived copy is then removed. One changed paper, one PDF
gone. Nothing reports it, because deleting the PDF is the documented behaviour of
that mode.

**This is the standing configuration, not a hazard of some future manual run.**
`run_mirror.sh` passes `"$@"`, and the unit's `ExecStart=%h/Git/offprint/run_mirror.sh`
carries no arguments, so every hourly fire runs `--attachments text`. The archive
survived ~48 hours of that intact — 2,745 files, none modified since the
evacuation finished — only because nothing changed in Mendeley in the meantime.

And it is **not currently safe, only quiet**: the timer is `inactive` but still
`enabled`, so it returns at the next login or reboot. Worse than returning on
schedule — the timer is `OnCalendar=*:07` with **`Persistent=true`**, and
`~/.local/share/systemd/timers/stamp-mendeley-mirror.timer` records a last
trigger of 2026-09-29 11:08:12 EDT. So the moment it becomes active again
systemd sees a missed elapse and fires **immediately**, not at the next `:07`,
with no arguments, in delete mode. The unit's own comment says catching up after
the machine was asleep is the whole point, and it is: the behaviour is correct
and predates the archive it now threatens.

panacea has been up since 2026-09-16, so a reboot is ordinary rather than
imminent — which is the kind of interval that makes a latent defect look like a
safe one. The archive's protection today rests on the machine not restarting.

**Until this is fixed, a refresh on this library is run with `--attachments
keep`.** That path never unlinks. But a flag an operator must remember, on a
schedule nobody watches, is not a fix — which is why the second box below is not
optional.

- [x] Do not delete a PDF this run did not download. **Done 2026-09-29**,
      `0.11.0`: the unlink is guarded by `not from_disk`. `text` mode writes
      nothing to `pdf_dir` — extraction runs on bytes in memory — so what stays
      reachable is a zero-byte stub from a failed download, which is still
      cleared because it is nobody's archive.

      **Two existing tests asserted the defect.** `test_mirror.py` staged a PDF
      on disk, ran the mode the timer runs, and *required* the file to be gone
      afterwards — the erosion written down as a specification, which is why it
      survived every previous reading of that code. They now assert the
      opposite. The new check fails without the fix, verified by restoring the
      old line and watching it go red.
- [ ] **Make the scheduled path safe without anyone remembering a flag.** Either
      the code fix above makes `text` harmless to an existing archive, or the
      unit and `refresh_quiet.bat` carry `--attachments keep` before the timer is
      ever restarted. The code fix is the better of the two: it protects a
      machine whose unit nobody edited, and `run_mirror.sh` and
      `refresh_quiet.bat` have to be kept in step either way.
- [x] `systemctl --user disable mendeley-mirror.timer` on panacea. **Done
      2026-09-29**; stopping it was not enough, since an enabled timer comes
      back. `mendeley-mirror.service` is `static`, so it stays startable on
      demand and `inbox.py`'s `REFRESH_UNIT` path still works.

      **Do not re-enable it as a repair.** A disabled hourly timer reads like
      something broken, and it is not. Two separate reasons put it there, and
      only one of them has been fixed:

      1. The archive hazard above — **fixed** in `0.11.0`, so this reason no
         longer applies on a machine running that code.
      2. Cameron is retiring the Mendeley backend. The library is final, the
         account lapses 2027-01-01, and a refresh against a degrading account
         would fail loudly and regenerate nothing anyone wants. **This reason
         still stands**, and it is his decision to reverse, not a defect to
         repair.

      The reboot on 2026-09-30 is the evidence that the disable holds: the
      archive came back byte-identical at **2,745 files / 4,476,820,874 bytes**,
      and `stamp-mendeley-mirror.timer` was still 2026-09-29 11:08:12,
      unrewritten. Those two numbers are the baseline for any later check.

**Still open: Cameron's two laptops.** Neither session can see their units, and
the Windows scheduled task runs `refresh_quiet.bat`. The code fix protects them
without anyone editing anything, which is the argument for having done it that
way — but it only protects them once they pull.

## 9. A generated `metadata-report.md` — REQUESTED, NOT DECIDED

Filed by the library session 2026-10-01, relayed as being at Cameron's
direction. **Recorded here because a peer's relay is not approval; nothing is
built until Cameron says so himself.** Spec and runnable probes:
`~/.local/state/fleet/drops/metadata-report-spec-20261001/`.

The case for it is the honest one: library defects live in the library session's
memory, tool-side conclusions live in this repo's commits and `docs/`, neither
session reads the other's, and **both go stale** — the library session's did
twice in one day. A hand-maintained shared list would catch the same disease, so
it should be derived from `library.bib` on every refresh, the way
`extraction-report.md` already is.

What makes the proposal more than a list of greps is its organising principle:
**every line names who fixes it.**

| bucket | fixer |
|---|---|
| the writer dropped it | this repo, in code |
| the Mendeley record is wrong | Cameron, in Mendeley |
| unfixable by design | nobody, and the report must say so |

That third row is the one that earns the file. The three frozen-year keys are
correct behaviour, and without an explicit "do not report this" at the point of
discovery they get rediscovered every few months by whoever next notices a 2002
key on a 1976 paper. `f2e00a5` says it in `docs/`; a reader of the report is not
reading `docs/`.

**Verified before recording, not taken on trust:** `--selftest` passes 22
fixtures in both directions, and the probes reproduce the shipped baseline
byte-identical against the live 2,739-reference library.

### What it would cost, and why it is Cameron's call and not mine

- A new generated file in the library is a **new output format**: `__version__`
  bumps, and the library session is told before the working tree carries it.
- It is a **ninth entry point's worth of surface** that must stay offline, and
  every probe arrives with the fixtures it must reject as well as accept. That
  is not negotiable: on this family of probes the first draft was wrong by an
  order of magnitude *every time* — 163 → 5, 103 → 2, 3 → 2.

### One defect found while verifying, to fix before any of this ships

The `and others` rows print a fixed-width **tail** of the author field, so
`De Meyts, P. and Roth, J. and Neville, D.M. and others` displays as
`nd Roth, J. and Neville, D.M. and others`. Detection is right; the display
invents a mangled author name, which is precisely the kind of line a reader
would then re-report as a defect. A report that manufactures findings is worse
than no report.

### The part worth preserving if it is built

The library session is deliberately **not** handing over every probe, because
what made this work was disjoint blind spots — this repo's comma probe could not
see `Jbames` (zero commas, not two); its front-matter probe could not see
`Abrams2002Biphasic` (no extract). If every probe moves into the tool and both
sessions read the same generated report, the cross-check dies and two copies of
one opinion remain. Known classes go in; the hunt for unknown ones stays out.


## Deliberately not doing

**Packaging (PyPI, conda-forge, console entry points).** The PEP 723 headers
mean `uv run --script` needs nothing installed, which is what makes this work on
a bare Windows laptop. A package would add a release process paid on every
change, for the benefit of users who do not exist yet.
*What would change it:* a second person needing to install it, or a machine in
the fleet that should run it without a clone.

**A pyproject.toml purely to deduplicate the ten dependency headers.** The test
suite now checks that every header matches what its script actually imports, and
fails on both a forgotten dependency and a spurious one. The duplication is
visible and verified rather than silent.

**Rewriting the test runner as pytest.** It is a bespoke runner, which is
unusual, but it is offline, dependency-light and prints a line per check. The
cost of converting is paid immediately and the benefit arrives with the first
outside contributor.
*What would change it:* an outside contributor.
