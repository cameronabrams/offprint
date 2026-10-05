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

      **CORRECTED 2026-10-02. The first version of this item said Zotero holds
      no attachment bytes at all, and it was wrong by 39%.** The claim rested on
      a 12-item sample in which every attachment reported `md5: None`, and then
      on a whole-library dry run reporting "already in Zotero: 0". The live
      upload answered `{"exists": 1}` for **1,060 of 2,608**.

      The error is one inference: **`md5: None` in item metadata does not mean
      no bytes in storage.** It means the item does not record a file. Only the
      upload-authorization request consults storage, and a dry run never makes
      one — so the dry run could not have known, and its zero was a restatement
      of the same unreliable field rather than a second source. The caveat
      originally kept beside the number ("`md5: None` strictly means 'not in
      zotero.org storage'") was itself the mistaken reading, stated as the
      careful one. The library session found this and recorded it as its own.

      What survives: the adapter must carry each attachment's **filename**, not
      just an id, because the archive is named `<citekey><real suffix>` and the
      suffix comes from that filename. An adapter exposing only an id and a
      download URL cannot find the bytes it needs.

      What does not survive: "the local archive is the only source of bytes."
      Zotero could already serve a large minority of them before the upload, and
      after it, all 2,608. `find_archived()` is resilience and bandwidth, not
      the sole supply. `<out>/pdf/` was the copy that was not behind a service
      — until 2026-10-02, when Cameron deleted it; see item 19. The durability
      argument was his to make and he made it differently.
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
      would clear the 1,087 carrying control characters as a side effect. The
      adoption path does not, and those two facts have to be decided together.

---

## 11. Getting the PDFs into Zotero — `zotero_attach.py`, built 2026-10-01

Cameron bought the 6 GB tier and granted write on the existing API key the same
day. The archive is 2,740 files and 4,468,935,079 bytes — 4.47 GB, 4.16 GiB —
so it fits either way the tier is counted.

**This is smaller than it sounds, because the attachment items already exist.**
The 09-28 import created every one as `imported_file` with a parent, a filename,
a content type and `md5: None`. So the job is supplying bytes for items that are
already linked, not creating 2,740 attachments. `zotero_attach.py` **never
creates one**: where Zotero has no `imported_file` child, it reports. Inventing
an item is a different kind of write and nobody asked for it.

Protocol verified against Zotero's own docs on the day rather than written from
memory: authorization POST with `If-None-Match: *`, then `prefix + bytes +
suffix` to the returned URL under the content type it names, then a registration
POST carrying the upload key. `mtime` is milliseconds; the docs say so twice and
seconds would be accepted and wrong.

**Pairing is the one place this can be wrong without anyone noticing.** 71
archived files carry a `-N` suffix across 62 records. One archived file against
one attachment is unambiguous and covers the rest. Several are paired on file
extension only where that is unique on both sides and the two sets agree;
anything else is **reported, not guessed**, which is the rule that took
`zotero_migrate.py` from 98.5% to 2,739 of 2,739. A wrong pairing files one
paper's PDF under another's citation key, and nothing downstream would ever say
so.

Zotero's child order is never used to decide anything. It is *measured*: on
every record paired by extension, the run reports whether child order would have
agreed. Enough agreement and index order becomes a defensible fallback for the
records that cannot be paired at all — but that is a later decision made on
evidence, not a shortcut taken now.

- [ ] **The first live run is `literature`'s**, and should start with `--limit`
      or a single `--key`. A dry run performs no write at all: it stops before
      the authorization, which is itself a POST.
- [x] **Both metadata decisions made by Cameron, 2026-10-01: do them.** The
      script now PATCHes each attachment to describe the file it is given,
      before uploading it.

      `Vanommeslaeghe2009Charmm` declared `application/octet-stream` for a
      complete PDF under a mangled `.-_charmm_g` filename, so Zotero would have
      stored a PDF it did not treat as one. And Mendeley's filenames are
      replaced throughout by the archive's own `<citekey><ext>`, which is the
      name `library.bib`, `text/<key>.md` and `findings/` already use — one name
      for one paper across the whole system, instead of a per-service accident.

      Three properties keep a bulk metadata write from being the dangerous kind,
      and they are the same three `mendeley_edit.py` needed. Zotero's PATCH is a
      real partial merge, so unnamed fields are untouched. Only fields that
      actually differ are sent, so a re-run after a partial failure writes
      nothing. And a content type is never invented: bytes that are not a PDF
      keep whatever the item declares, because `looks_like_pdf` knows one thing
      and the function must not pretend to know more.

      A 412 is reported and the item left alone — never re-read and retried,
      since a retry overwrites exactly the edit that caused the conflict.
      `--no-metadata` uploads bytes only.

### What the first dry run found, 2026-10-01 — including two defects in this script

`literature` ran it against `3e35d96` and wrote the log up at
`~/.local/state/fleet/drops/zotero-attach-dryrun-20261001/`. 2,582 would upload,
4,212,233,505 bytes; 2,582 + 158 stranded = 2,740 exactly, so every archived file
is accounted for. Zero already in Zotero, which confirms the 12-item sample as a
census. `Vanommeslaeghe2009Charmm` is the only content-type correction in the
whole run; every other PATCH is a filename.

**My estimate of the at-risk set was low, and wrongly scoped.** I said 71 files
across 62 records from counting `-N` suffixes in the archive. The real figure is
158 files across 88 records, because `-N` only describes *one* of three ways
pairing fails. The other two I had not thought of: **24 records whose Zotero item
has no `imported_file` attachment at all**, and 3 count mismatches. The 61
no-unambiguous-pairing records are the class I did estimate, and that part was
close.

**Defect 1: a record with nothing archived was invisible.** `Hoover1979Exact`
appears nowhere in 5,263 lines. Its only attachment was the PNG that was deleted
that morning, so it has no archived file, and the problem report was keyed on
records that *have* files. A record with nothing to upload said nothing at all —
the same silence 0.12.0 fixed in the extraction report, arriving by a different
road and in a script written after that fix. Those records are now listed under
their own heading.

**Defect 2: I asked for a number the code could not produce.** The child-order
agreement figure was computed only for records already paired by extension, and
a record pairs by extension only when its extensions differ — which is never the
case for the records that needed the evidence. The measurement could not fire in
the one situation it was designed to inform, and it printed nothing at all when
the count was zero, so its absence was indistinguishable from a zero. I then
told the library session to report a figure I had never seen the code emit. It is
gone, replaced by `--pair-by-order`: an explicit, off-by-default guess whose
proposed pairing the dry run prints for every record it would apply to, so the
decision is checked by a person rather than inferred from a statistic.

**Pairing now allows the counts to differ**, which fixes two of the three
mismatches honestly. All three were that morning's deletions — Zotero still holds
a stub for each removed file — so `Bailey1967Crystal` is one archived `.pdf`
against a `.pdf` and a `.cif`, and a unique extension match is not a guess.
`Shan2011How` stays reported: two PDFs among three attachments cannot be told
apart.

- [x] **The 24 records with "no attachment to put them in" had one.** Not a
      migration finding at all — a third link mode the filter dropped. Zotero
      has four and **two of them are storage-backed**: `imported_file` and
      `imported_url`. The filter kept one.

      All 24 have exactly one child and all 24 are `imported_url`, each with
      `application/pdf`, an `m-api-<uuid>.pdf` filename and `md5: None` — the
      same shape as every `imported_file` in the census. A real bookmark looks
      nothing like it: the `linked_url` child of `Vanommeslaeghe2009Charmm` has
      no filename and an empty content type. `imported_url` is what Mendeley's
      import produced for attachments it had recorded as fetched from a URL; the
      provenance differs, the storage behaviour does not.

      **The function was called `imported_files`, and that is how this stayed
      invisible.** The name made the filter look obviously correct, and the
      docstring reasoned carefully about the mode it excluded on purpose while
      never mentioning the one it excluded by accident. A comment explaining why
      one thing is out reads, at a glance, as an account of everything that is
      out. It is now `storage_attachments`, with all four modes named and a
      reason beside each.

      The second half is worse than the 24 and the library session flagged it
      without being able to measure it: **a record holding both an
      `imported_file` and an `imported_url` child.** The old filter saw one of
      them, so the arithmetic ran against a short count and "1 archived file
      against 1 attachment" came out *unambiguous* — a confident wrong pairing,
      which is the one outcome this script is built to refuse. With both modes
      counted it is two `.pdf` candidates and therefore a report.

- [ ] **UNVERIFIED: whether Zotero accepts an upload against an `imported_url`
      item.** Nobody has tested it, because testing it is a write. The first
      live run does one alone with `--key` before the rest.
- [x] **The headline counter reported lines as records.** On `8970622`: 2,608
      to upload, 4,250,487,080 bytes, and the arithmetic closes at 2,740. But
      the header announced "123 record(s) uploaded nothing" where the truth is
      **131** — 69 with no archived file plus 62 that could not be paired, no
      overlap.

      One list held both the per-record headline and the indented
      `--pair-by-order` hint lines, and `len()` of it was printed as a count of
      records: 62 records plus 61 hints is 123. It **under**-reported, which is
      the direction that hides records rather than inventing them, and it landed
      close enough to the real figure to read as right. The third instance today
      of the same family, and the first where the number was present and wrong
      rather than absent.

      Two rules out of it, both now enforced by a test. A count of records comes
      from a list holding only records — `unpaired`, `no_archive` and
      `write_errors` are three lists because they are three different things,
      and the last is per-attachment and must not be added to a record count.
      And **the file arithmetic has to close**: every archived file is uploaded,
      already present, stranded, or withheld, and the tool says whether that
      sums to what is in `pdf/` rather than leaving a reader to check. The
      library session did that addition by hand and that is how this was caught.

**An honest negative, recorded because a fix with no instances is still worth
distinguishing from one that repaired something.** Zero records in this library
hold both an `imported_file` and an `imported_url`, so the confident-wrong-
pairing hazard was real in principle and had no instances here. It was checkable
only because the failure message prints the link modes — which is the argument
for printing them.

**And the `--pair-by-order` hint lines earned their place.** The library session
could see that `Brenner1990Empirical` would pair correctly, and that
`Won2001Influence` would hand a Won paper a Shan filename, and that
`Theodorou1989Variablea` would pair melt-surfaces against melt-solid-interfaces.
The filenames carry author names and subject words; a person reads that in
seconds. The agreement statistic that this replaced could not have shown it, and
could not have been computed at all. If the 62 are ever to be resolved
automatically, that filename text is the signal to use — not ordering.

### The upload ran, 2026-10-02

Cameron authorized it directly, to the library session, after declining to have
it lifted by relay through here. **Done and verified independently:** a random
sample of 30 single-attachment records compared Zotero's md5 against the local
file's — 30 of 30 match, 0 mismatch, 0 missing.

    uploaded 1,548 · already in Zotero 1,060 · patched 2,599
    1,548 + 1,060 + 132 stranded + 0 withheld = 2,740, the archive exactly

One transient: `authorize` on `PMNRQQQJ` returned HTTP 502, was retried once and
succeeded. `Zheng2008Random` carries its md5. The retry path earned itself.

**A fourth counter defect, same family as the other three.** The summary printed
`uploaded: 1548 (4,246,587,930 bytes)` — but that byte total accumulated for
every file the run read and offered, including the 1,060 Zotero already had. The
library session proved it arithmetically rather than by reading the code: the dry
run over 2,608 files reported 4,250,487,080 bytes, the live run 4,246,587,930,
and the difference of exactly 3,899,150 is the six files already done under
`--key` and `--limit 5`, which `needs_upload()` then skipped before the read. So
the figure was bytes *considered*, attached to a count of files *sent*. Bytes
actually uploaded were not recoverable from the output at all.

Now two totals: `sent_bytes`, incremented only after a successful transfer, and
the read-and-offered total, labelled as such. And a dry run says plainly that it
cannot know how much would really be sent, because it never authorizes.

**Four counting defects in one tool in two days** — a silent zero, a measurement
that could not fire, lines counted as records, and a byte total labelled as a
different byte total. Three of the four read as plausible and only one looked
missing. Every one was found by someone running the tool and adding the numbers
up by hand, none by reading the code. That is the argument for the tool doing
the addition itself, which it now does.

### The 62 are done — 2026-10-02, by hand, with evidence

`literature` worked through every one and uploaded them.

    pairings.tsv: 126 lines, each carrying its evidence
    uploaded 85 (152,674,932 bytes sent) · already in Zotero 41 · patched 126
    85 + 41 + 6 stranded + 0 withheld = 132, reconciles

It verified 18 of the evidence-based pairings by md5 — chosen as the ones where a
swap would be a real error, supplement-against-paper and part I-against-part II —
18 correct, 0 wrong.

Both of the day's late fixes earned themselves on this run. The sent-versus-read
split printed `85 (152,674,932 bytes sent)` beside `read and offered:
216,503,800 across 126`; under the old single counter it would have reported
216 MB as sent. And the dry-run caveat about `md5: None` printed exactly where it
was needed, because 41 of the 126 turned out to be already held.

**Two records still refuse, correctly** — not three. `literature` first reported
`Won2001Influence` as a merge and withdrew it before it reached Cameron;
Crossref settles it, and all six DOIs below were re-checked here against
`api.crossref.org` rather than taken on report.

`Won2001Influence` is **one paper with the wrong author list**. The record's
journal, volume, issue and pages are `10.1002/app.1171` — Shan, Robertson,
Verghese & Burts, *Influence of vinyl ester/styrene network structure…*, JAPS
80(7) 917–927 — and both archived files are that paper. Won, Fulchiron &
Douillard's JAPS 80(7) paper is `10.1002/app.1185`, *Effect of the pressure on
the crystallization behavior of polyamide 66*, 1021–1029, a different subject
that is not in this archive at all. The Zotero filename reading "Won, Fulchiron,
Douillard - 2001 - Influence of vinyl ester/styrene…" is the mislabel: it puts
Won's name over Shan's title. Pairing was safe, both files uploaded.

The other two are real merges, each a pair of companion papers in one issue:

| record | the record's own DOI | the second paper |
|---|---|---|
| `Daoulas2005Molecular` | `10.1021/ma050177j`, Macromolecules 38(13) 5796–5809, Harmandaris et al. | `10.1021/ma050176r`, 38(13) 5780–5795, Daoulas et al. |
| `Hirota2000Effect` | `10.1021/jp0014418`, JPCB 104(42) 9904–9908, Gong et al. | `10.1021/jp001438o`, 104(42) 9898–9903, Hirota et al. |

**All three share one shape, and that is the finding worth more than the three
records.** Each carries identifiers — DOI, volume, issue, pages — from one paper
and an author list headed by a *different* paper's first author. The citation
keys prove it without reading the records, because `make_citekey` builds them
from the stored first-author surname: `Won2001Influence` on a Shan paper,
`Daoulas2005Molecular` on a Harmandaris paper, `Hirota2000Effect` on a Gong
paper. Identifiers from A, authors from B, three times. That is a pattern, not
three coincidences, and it is almost certainly a Mendeley import artefact.

- [x] **`doixref.py` finds the class.** Cameron asked for it 2026-10-02. It
      queries Crossref — public, unauthenticated — and **never writes to the
      library**, caching to `~/.cache/offprint/` so that a read-only audit stays
      one. `pdbxref.py` is the precedent and the naming.

      **The comparison is three-way, and the obvious two-way version would have
      been wrong.** Crossref's first author against the *citation key's* surname
      flags every record whose authors were ever legitimately corrected, because
      a key is frozen at assignment. So it compares key · `library.bib`'s current
      first author · Crossref, and reports on the middle against the right; a key
      disagreeing with a bib author that agrees with Crossref is counted
      separately as a frozen key doing its job.

      Two classes, both candidates and not verdicts: **author** (the DOI's title
      matches but its first author does not — the class the three belong to) and
      **title** (the DOI resolves to a different paper altogether, which is worse
      and is how `Wu1982Potts` nearly acquired the wrong PDF).

      Nothing scores similarity. A surname agreeing only on its last
      whitespace-separated token — `van der Waals` against `Waals` — gets its own
      heading rather than being counted as agreement, because the two errors do
      not cost the same: a false agreement hides a defect and a false mismatch
      costs a reader a second. Every unchecked record is counted and named beside
      the score, since a denominator that shrinks to fit its numerator reads as a
      perfect result and this repo has produced that twice.

      **Validated against the three cases the library session picked, not
      against a case of its own choosing:** all three land in the author class
      with their titles agreeing, and `Abrams2013Enhanced` agrees as a control.
      That is the shape confirmed mechanically — identifiers from one paper,
      authors from another.

- [ ] **Run it over the whole library.** ~2,270 of 2,739 records carry a DOI;
      the rest cannot be checked this way and the report says how many. That run
      is the library session's, and detached — it is 2,270 round trips.

**And fixing an author list does not rename anything.** `assign_citekeys` gives a
key once per document and keeps it, so `Won2001Influence` stays
`Won2001Influence` while its author field says Shan — the same way
`Abrams2013Enhanced` carries `year = 2014`. The key is a handle; the field is the
claim. Anyone who meets that record later will think it is broken, which is why
it is written down here.

**The discriminators that actually worked, worth keeping if this is ever
automated:** page-1 text of the extract against the Zotero filename, and
Crossref. `Mao2013Molecular` resolved four PNAS DOIs to four titles exactly;
`Theodorou1989Variablea` split on page ranges, 4578–4589 against 4589–4597.
**Similarity scores failed three times** and are not the route — which matches
what `zotero_migrate.py` found, where fuzzy title matching was the last resort
and settled 3 pairs out of 2,739.

### The version rule was read too narrowly — `0.14.0`

Seven commits built and then repeatedly corrected `zotero_attach.py` under an
unchanged `0.13.0`. The reasoning each time was that `__version__` says which
rules produced a *mirrored file*, and this script writes none. That is literally
true and it was the wrong reading.

It bit the same day, in the shape the rule exists to prevent. The library session
ran a dry run, two defects were fixed, it ran another — and to say which log was
which it quoted commit hashes in a message, because the tool stamped nothing.
Those logs are saved in `~/.local/state/fleet/drops/` and they are the record of
2,740 writes to a live library.

**If a run's output is kept, it is a file, whatever it is printed on.** September's
version of this was two hours of `mirror-status.md` made indistinguishable by a
layout change under a frozen version; this is the same failure arriving through a
script nobody counted as producing output. `zotero_attach.py` now prints
`offprint <version>` in its header and takes `--version`.

- [ ] The three Mendeley writing scripts have the same property and not the same
      problem — they are interactive and single-record, and nobody keeps their
      logs. Worth giving them `--version` anyway if a batch mode ever lands.

### The 132 stranded files are the library session's job, and it now has the tool

Cameron's line, 2026-10-02: resolving them belongs to `literature`, "once it
knows it has the tools it needs from you to accomplish" it. It did not, and the
gap was specific rather than general.

The 132 are two different problems and only one of them is a pairing problem.

**69 records have no archived file at all.** Nothing can be uploaded for them
because nothing exists locally. That is acquisition, not pairing, and the tools
for it already exist — `get_pdf.py`, the fetch queue, `inbox.py`. Nothing new is
needed here, and building something would have been motion.

**62 records cannot be paired by any rule**, holding the other ~125 files. The
tool could refuse, and could guess in bulk with `--pair-by-order`, and had
nothing in between. There was **no way to record a decision once a person had
made one** — so the only routes were all-or-nothing, and the bulk route is known
to be wrong for some of them.

`.mirror/pairings.tsv` closes that, beside `removed.tsv` and for the same
reason: a human decision that has to travel with the library and outlive any
one session. One `stem<TAB>attachmentKey` line, date and reason optional. A
stem named there bypasses every rule, and **partial coverage is the quickest
route** — deciding one attachment on a record often leaves the rest unique
enough for the extension rule to settle, which a test asserts directly.

The other half of the gap was that a refusal printed no way to act on it.
"2 archived against 2, no unambiguous pairing" tells a reader a decision exists
and gives them nothing to decide with. It now prints every archived file with
its size and every Zotero attachment with its **key**, filename, link mode and
content type — the key being what a `pairings.tsv` line has to name, and the
filename being what the decision actually turns on.

**This file is checked harder than anything the tool decides for itself**, and
deliberately so: it is the one place a human assertion overrides the tool's
refusal to guess. A key that is not on that record, two stems claiming one
attachment, or a line missing its key each stop the run. That is the opposite of
`removed.tsv`, where a bare stem is a complete entry and tolerating a sloppy line
*protects* a file — there, strictness would cause the harm it exists to prevent.
Same shape of file, opposite failure direction, and the reason is which way a
mistake cuts.

Once this has run, the exposure in item 1 changes shape: the PDFs stop being a
single local copy excluded from every replica, and Zotero becomes a second place
they exist. That does not retire `<out>/pdf/` — a mirror whose bytes live only
behind a service is the thing this tool exists to avoid — but it does end the
period where one disk on panacea is the only copy.

## 12. The cutover happened — `--retire`, and what is now stranded

Cameron to the library session, 2026-10-02: *"we are not using Mendeley at all
now. Zotero is now the remote."*

**So the mirror is a frozen snapshot**, as of the last refresh,
2026-09-30 16:21 UTC. Checked rather than assumed: only `mendeley_mirror.py`
writes `library.bib`, `index.md`, `folders.json`, `text/` and `annotations/`,
and nothing else in the repo can un-freeze them.

### The part that fails silently, and why the obvious fix does not fire

`mirror-status.md` reads `**ok**` and "Everything in this folder is current as
of the run above". Both were true on 09-30 and neither is now — and the
library's own `CLAUDE.md` says *"Check it before concluding that something is
absent from the library."* That instruction is now unsound and nothing in the
file says so.

Same family as the silent `not-pdf` skip and the counter that said 123, with one
difference that matters: **the line is not missing, it is confidently wrong, and
it will keep being wrong forever.** `write_status` runs only during a refresh.
When refreshes stop, the status file freezes too — so changing the code that
writes it changes nothing. The fix has to be a deliberate one-time write.

`--retire [REASON]` is that write, `0.15.0`:

- rewrites `mirror-status.md` as a standing notice naming the date, what is
  still true (`library.bib`, `text/`, `annotations/`, `pdf/` remain a valid
  snapshot — quoting and citing are as sound as ever), and the one consequence
  the library's instructions rest on: **absence here is no longer evidence of
  absence**;
- removes the false sentence rather than leaving it under the banner, where a
  skimming reader meets it first;
- leaves `.mirror/retired.json`, which makes a later refresh **refuse** rather
  than fail — writing `**FAILED**` over the notice would replace the one
  sentence this folder still needs with a transient one;
- is undone by deleting that file.

**Its limit, stated rather than discovered later: Cameron's two laptops run
older code and will not honour the marker.** If their Windows scheduled task
fires against a retired Mendeley account it will overwrite the notice with a
`FAILED` banner and sync that. Disabling those tasks is a per-machine action and
his, not something this repo can reach.

### Stranded with nowhere to write

`mendeley_edit.py` writes to Mendeley. Nothing writes Zotero *item* metadata —
`zotero_attach.py` PATCHes attachment `filename` and `contentType` only. So the
library session is holding, with no destination:

- 55 prepared author edits (7 mojibake repairs, 48 refetched author lists), dry-run clean
- 15 records with scrambled author order
- 2 merged records (`Daoulas2005Molecular`, `Hirota2000Effect`)
- and whatever `doixref.py` turns up across the other ~2,270

- [x] **`zotero_edit.py`**, built 2026-10-02. Keyed the same way as
      `mendeley_edit.py`, and **the edits-file format does NOT port unchanged** —
      this file claimed it did, and it was wrong. The library session's
      Mendeley-shaped file needed `authors` → `creators` and
      `first_name`/`last_name` → `firstName`/`lastName`.

      The unknown-field check stopped it on the first record, which is exactly
      what it is for; under a silent no-op it would have sent 55 patches, exited
      clean, and left the library unrepaired while reporting success. But a
      claim someone acts on with `--yes` has no business being wrong, so: the
      Mendeley field names are now named as Mendeley's and translated in the
      error, and Mendeley's person shape is refused rather than stored as a
      creator with empty names — which would be an edit that looks successful
      and destroys the field it meant to repair.

      All 55 went through after the translation: 0 conflicts, verified from
      Zotero rather than from the tool's own count — zero `??` in the 7 mojibake
      records, and 6 of the 48 re-read with full author lists.

      **The dangerous field moved, and that is the finding worth keeping.**
      Mendeley kept DOI, ISSN and PMID in one nested object its PATCH replaced
      wholesale, which is why `identifiers` is merged by hand there. Zotero's
      PATCH is a top-level partial merge, so those are separate fields and the
      hazard is simply gone — and it **reappears one level down**, in
      `creators`, a list that a PATCH replaces entire, editors and translators
      included. The same protection therefore sits in a different place: an edit
      replaces only the creator types it mentions and keeps the ones it does
      not. Porting the old guard literally would have protected a field that no
      longer needs it and left the one that does exposed.

      Order is used exactly as written, because order is the correction in 15 of
      this library's records rather than an incidental detail.

      A third property is new and belongs to this backend: **an unknown field
      name is an error.** Zotero returns every field valid for an item's type
      including the empty ones, so a name that is not already a key of `data` is
      a typo — and a typo that reached Zotero would be ignored server-side,
      which is a silent no-op wearing the clothes of a successful edit.
- [x] **The damage was not a Mendeley artefact.** The library session checked
      Zotero's own pre-edit records and found `Gonz??lez-Nilo`, `C??sar Augusto
      F` and a single-author `Y. Tamai` there too, so the migration carried the
      defects across intact rather than introducing or healing them. Fixing
      Zotero was the right and only target — there was never a version of this
      where correcting Mendeley would have helped.

- [ ] **A Zotero-backed `mendeley_mirror.py`** — item 1, now the live question
      rather than the contingency it was written as. **Started 2026-10-02**,
      with `zotero_source.py`: see below.

## 13. The Zotero-backed refresh — started, and verifiable before it is wired up

`zotero_source.py` translates Zotero items into the document dicts
`bib_entry`, `assign_citekeys`, `write_index` and `annotation_markdown` already
take. **The generators do not change**; what changes is where documents come
from. The whole consumed surface turned out to be 16 fields, which is what makes
an adapter the right shape rather than a rewrite.

**It was built first because it can be checked before anything is integrated.**
`library.bib` is a frozen, known-good rendering of the same 2,739 records
produced by the Mendeley path, and `citekeys.json` holds a verified bijection
between the two backends' ids. So `--compare` regenerates every entry from
Zotero and diffs it against that file. The oracle was produced by an independent
path over the same library, which is the standard this repo keeps failing to
meet by comparing a thing against a copy of itself.

A difference is either a translation defect or a real improvement, and the diff
forces someone to say which.

What the translation already has to know, each because of a failure this file
records: the venue lives in a different field for each item type (four types
were silently dropping it until 09-30); a patent has no `date` and keeps its
year in `issueDate` (all three of this library's patents, and the migration
checker lost exactly those and scored 2,735 of 2,735); a corporate creator has a
single `name` and splitting it invents an author; PMID has no Zotero field and
lives in `extra`; and an unmapped item type degrades to `generic`/`@misc`
rather than raising.

### The identifier rescue ran — 1,174 PMIDs are in Zotero

`patched 1174, conflicts 0`, verified by sampling 12 PMIDs out of the frozen bib
and finding all 12 in Zotero's `extra`. The carry-forward worked: 149 records
already had an `extra` and every one was preserved, `arXiv: cond-mat/0510639`
becoming `arXiv: cond-mat/0510639\nPMID: 16626184`.

**But the library session had to split the file, because the generator emitted
something its own consumer refuses.** `--rescue-identifiers` assigned a typed
`ISBN` to 103 records; a sample of 40 were *all* `journalArticle`, which has no
`ISBN` field. `zotero_edit.py`'s unknown-field guard stopped the run on the
first one — correctly — and 1,174 PMIDs were queued behind it in the same file.

**A producer that can emit what its own consumer refuses has a missing check,
not a typo.** The generator routed on the *value's shape* and never asked
whether the item type had anywhere to put it. Shape alone cannot decide that: an
ISBN-shaped value on a journal article is a mislabel in the source, exactly like
the 45 ISSN-shaped `isbn` values already being rerouted, and **the item type is
what says so**. Every typed field is now checked with `can_hold()` before it is
offered, and what cannot land is named under its own heading with the type that
cannot hold it.

Two other gaps the same run measured, both fixed:

- **14 of 31 arXiv ids were skipped as "already present"** on the strength of
  the word `arXiv` appearing somewhere in `extra`. The ids arrive bare,
  prefixed and versioned — `1106.1296`, `arXiv:1401.0387v1`, `cond-mat/0510639`
  — and a containment test on the literal string answered a different question
  from the one being asked. Ids are normalised now and compared as ids.
- **25 lost `url`s were not rescued at all** because the generator never looked
  at `url`. 23 are `journalArticle`, which has the field.

**And a gap that is not one.** 12 lost `volume`s and 13 lost `number`s belong to
records typed `report`, `book` or `bookSection`, which have no such field in
Zotero. The data has nowhere to live. Rescuing those needs the item types
corrected, which is a PATCH that drops every field the new type lacks — which is
why `zotero_edit.py` refuses `itemType` and should keep refusing it.

### Two things this file said with more confidence than it had

**"201 ISBNs lost" was really 9, and "TYPE_MAP damage surfacing a third time"
was overdrawn.** Both corrected 2026-10-02 by the library session.

Of 192 `isbn` values the rescue refused, **91 are already in Zotero as an ISSN**
in a different format — `Yang2009Comparison` holds `00219606` against the frozen
bib's `0021-9606` — and **101 are junk**: `1060510618`, `5143983797`,
`1493605872292`, none of them an ISSN or an ISBN, and every one of those records
already carrying a correct ISSN. Nine were real.

The message was wrong for the 91, and wrong in a way that teaches something
false: *"on a journalArticle, which holds neither"* says the **type** cannot hold
the value, when the type holds it fine and the value is already there. Those are
opposite situations calling for opposite actions.

**The first attempt at fixing that made the same mistake one level down**, and
the library session caught it by spot-checking six records against the live
library rather than reading the counts. "Already in Zotero in another format, no
action" was printed for all 192 — but only 91 are the record's own ISSN
differently punctuated (`0021-9606` against `00219606`). The other 101 are junk
digit strings that are **not that record's identifier in any format**:
`Agarwal2005Role` has `isbn = 1060510618` against an ISSN of `0002-7863`.

The *action* is identical — send nothing — and the *fact* is not. Only the
second says the frozen bib is carrying a hundred junk values, which is a finding
about the bib that nobody would ever go looking for. Counted and listed
separately now: `already_same` and `discarded_junk`.

**A label that merges two facts because they share an action is the same defect
as a label that merges two actions.** Both were in this file within an hour.

**And then it happened a third time, in the fix.** `already_same` was decided by
a normalised whole-string equality, which reported 41 same / 160 junk against
the library session's measured 91 / 101. The frozen bib's `isbn` often holds a
**pair** with PubMed's labels — `1091-6490 (Electronic)\r0027-8424 (Linking)` —
which is the journal's electronic and print ISSN, and Zotero stores one of the
two. `Yun2008Mutation` and `Yao2015Viral` carry the *identical* bib value and
Zotero kept the opposite member in each. Calling those "not this record's
identifier in any format" is simply false, and it was about to be printed as a
finding someone would act on.

The test is now whether the value **contains** an ISSN-shaped token matching the
record's, not whether the whole string equals it — and the remainder splits
again, because it is still two things: a value with ISSN structure matching
nothing is a genuine disagreement about which ISSN the journal has
(`Bajaj1987Tertiary`, `0006-3002` against `01674838`), and a value with no ISSN
structure at all (`1060510618`, `3014024724`) is the junk worth reporting.

Three rounds of the same mistake, each one caught by the library session reading
records rather than counts. The pattern underneath all three: **when an output
groups things by what the tool will do about them, it will keep merging facts
that deserve to be said separately** — the action is the obvious axis and it is
the wrong one. The six cases it was finally fixed against are fixtures in
`test_mirror.py` now, by name, so the next refactor has to keep agreeing with
the live library rather than with me.

### The fourth split was real and the right answer was to stop

`issn_differs` was also mislabelled: resolved through Crossref's `/journals`,
`Brooks2009Charmm`'s `1096-987X` and `0192-8651` are **both** the *Journal of
Computational Chemistry*, and `Bartesaghi2013Prefusion`'s pair are both *Nature
Structural & Molecular Biology*. Print and electronic ISSN, both correct, the two
sources having kept different members of a legitimate pair. Only
`Bajaj1987Tertiary` is a real mismatch — BBA general against BBA Protein
Structure.

So a fifth bucket was available. The library session's call, and it is right:
**a breakdown that needs a fifth axis before it stops implying a problem should
lead with the verdict instead.** Roughly 105 of 215 are not defects in anything,
and the other 110 harm nothing — those records already carry a correct ISSN and
the bib field holding the junk no longer feeds any output. The report now opens
with *"Nothing to send. No action is required of anyone"* and offers the
breakdown as a curiosity rather than a work list.

**Knowing when to stop refining a list is part of the craft and not a retreat
from it.** Four splits each found a true distinction; the fifth would have too;
and the reader was still going to come away thinking there was something to fix.

**The denominator question, answered from the code rather than deferred.** The
library session measured 215 where this reports 201 and suspected the gap was
`--compare`'s lost set. It is not: `rescue_identifiers` walks *every* entry in
the frozen bib, and the `isbn` branch skips any record whose Zotero item already
holds an ISBN, because there is nothing to restore for those. The 14 are the
books Zotero already has right. Benign, and now in the docstring so the next
person comparing two tallies does not have to re-derive it.

**And the no-field list is not the type inventory**, which this file claimed it
was. All 192 entries are `journalArticle` records with junk in `isbn`. A report
typed as a report has no ISBN to refuse, so a mistyped record never appears in
it at all — `Berman2000Protein` is not there. "Records that refused a field" and
"records whose type is wrong" are different sets, and treating the first as an
inventory of the second was an inference with nothing behind it.

### A stranger's filesystem reached the live library

`Alberty1958Application`'s rescued `url` was three URLs joined by a literal
`\n`: a publisher link, a `papers2://` scheme, and
`file:///Users/fingolfn/Dropbox/...` — a local path from someone else's Mac, by
way of an import years ago. It is now in Cameron's live library.

The library session named the miss as its own — it read the tail of the dry run
rather than the body — and that is right as far as it goes. But **the tool
offered the value**, and a rescue that hands a third party's filesystem path to
a live library has no business waiting for a human to catch it. `clean_url()`
now requires a single `http(s)` URL with no embedded newline and no whitespace.

It does **not** take the first of several and offer that. A concatenation is a
defect in the source, and which part is canonical is a judgement about someone's
library — the same reason the single-field author names are reported rather than
split. The value is refused and named with its parts shown, so the decision is
cheap for whoever makes it.

### The real type inventory, built by the library session

All 2,739 items paginated and cross-referenced against Crossref's own type:
**2,687 of 2,739 are `journalArticle`, and of the 2,229 with a cached Crossref
type, 2,221 agree.** Zotero's types are overwhelmingly right and the damage is
far smaller than three separate paragraphs of this file implied.

The 8 that disagree: `Flood2018Selective` and `Masaro1999Physical` (book),
`Katyal2017Expression`, `Murphy2006Csf`, `Naji2005Theoretical` and
`Wong2011Allosteric` (bookSection), `Vashishta2006Multimillion`
(conferencePaper), `Patil2026Structure` (preprint).

**The 25 reports fall outside that check because not one has a DOI**, which is
exactly why they were invisible to it. Read directly, 17 carry a journal venue —
*Nucleic Acids Research*, *Annu. Rev. Biochem*, *Journal of Virology* eleven
times — plus `Humphrey1996Vmd`, the VMD paper, which the venue flag missed
because its `extra` is empty. So roughly 18 of 25 are journal articles. The
genuine reports are `Snow1991Dilatometry`, the US EPA record and
`Bindermwoods`.

### Where the reports came from: one bad import, filed two or three times

`Frankel1998Hiv` a/b/c are three records with identical titles.
`Moore1993Immunochemical`, `Sullivan1993Effect`, `Thali1993Characterization` and
`Wyatt1992Relationship` are a/b pairs. With the unsuffixed keys that is **5
papers across 13 records**, and the PDFs are byte-identical within each group —
Moore's three all `a1a5ddc5`, Wyatt's three all `9a70d7cd`. One bad import batch,
filed repeatedly, all typed `report`. That is where the report population comes
from, and it is a library matter rather than a tool one.

Note for whoever prunes them: `citekeys.json` is append-only, so a deleted
record keeps its key reserved and a key cited in a manuscript keeps resolving.
Both halves or neither — `docs/reference.md` has the procedure.

**Closed 2026-10-02.** Cameron approved the sweep and `library` ran it. The 11
suffixed extracts were **moved, not deleted**, to
`~/.local/state/fleet/drops/orphan-text-20261002/`, and `text/` now has zero
keys missing from `library.bib`. The 5 plain-key records and their extracts
survive.

Two things that session did unprompted and that are worth naming, because both
are conventions this repo had to learn the hard way: it reported **what
survives** alongside what moved, which is `zotero_delete.py`'s whole design
principle arriving from the other side; and it moved rather than deleted, which
is what every destructive path here now does. Neither was in the handover.

### What `--compare` found, 2026-10-02 — 1,366 identical, 1,373 differ

**The finding that matters: 1,174 PMIDs are in the frozen `library.bib` and not
in Zotero.** The migration did not carry them, nothing in either the frozen bib
or the Zotero record says they were ever there, and for a library this
biomedical that is the identifier people actually search by. 31 arXiv ids are
gone the same way.

**So `<out>/library.bib` is currently the only place 1,205 identifiers exist,
and a Zotero-backed refresh that overwrites it destroys them.** That is the
whole argument for having built `--compare` before wiring the refresh up: this
would have been a silent, total loss discovered months later, and the file that
proved it is the file that would have been overwritten.

`--rescue-identifiers` is the repair rather than a note about it: it writes a
`zotero_edit.py` edits file restoring PMID and arXiv into Zotero's `extra`, and
DOI/ISSN/ISBN into their own fields. Two refusals are built in. It never appends
blindly to `extra` — the field is free text, a PATCH replaces it, and a re-run
must not double a line. And **it does not restore an `isbn` that is not shaped
like one**: 45 of the frozen bib's 201 `isbn` values are ISSNs Mendeley
mislabelled, and copying a known defect into the live library because it sits in
a file we trust is how a defect becomes permanent. Those are routed to `ISSN`
and counted as reclassified.

The other differences, with the library session's reading of them:

- **28 journal gained, 54 note lost — resolved, and it was half a fix and half
  a bug of mine.** The library session checked every one. 28 are
  `journalArticle` with a real `publicationTitle`: exactly the 28 that gained
  `journal`, the 0.10.0 venue workaround landing properly, and their notes must
  **not** be restored. The other 26 had no venue from Zotero at all, because the
  migration parked the venue of every *non*-`journalArticle` in `extra` as a
  `Publication Title:` line and `zotero_source` did not look there. 20 reports,
  3 books, a patent, a thesis. **No venue is lost anywhere** — `library.bib` is
  not load-bearing for venues, only for identifiers. Fixed; the typed field
  still wins where it exists.

  **The lesson is the library session's and it is the better half of the
  finding.** Its first answer was "these are a fix", its second was "15 of 18
  are unrecoverable losses", and both were wrong the same way: each looked only
  at the one field its hypothesis predicted. The true answer needed checking
  every venue-bearing field. That is this repo's own rule — *searching for one
  access pattern is not searching for the accesses* — arriving from the data
  side, and it is why the second, more alarming answer was no more trustworthy
  than the first.

- **20 of those 26 are `itemType: report` and are plainly journal articles** —
  `Berman2000Protein` is the PDB paper in *Nucleic Acids Research*, and six are
  *Journal of Virology*. That is the old `TYPE_MAP` damage from the Mendeley
  side, now frozen into Zotero's item types. Reading the venue gives them a
  venue; it does not make them articles. Changing an item's type is a PATCH that
  can drop every field the new type does not define, which is why
  `zotero_edit.py` refuses `itemType` and should keep refusing it.
- **201 isbn, 25 url, 13 number, 12 volume** — unexamined.
- **86 authors differ; 55 are the library session's edits and 31 were neither
  of ours. All 31 are now accounted for**, in three groups, and the biggest
  group was noise hiding the only real loss.

  **17 are Unicode normalisation and are not differences at all.** The frozen
  bib is decomposed, Zotero is precomposed: `Mullerplathe1998Microscopic` has a
  combining diaeresis on one side and a precomposed `ü` on the other. All 17 are
  equal under NFC. `--compare` now normalises both sides, because **a diff that
  reports a difference nobody can act on trains its reader to skim it** — and
  the library session nearly skipped past the patents for exactly that reason.

  **3 are real data loss: the patents.** `Abrams2016Compositions`,
  `Antczak2003Methods` and `Brinn1969Purification` are `itemType: patent` with
  `creatorType: inventor`, 11 inventors between them, and `zotero_source` emitted
  **no author at all** — Cameron's own patent generating authorless under a
  citation key that says Abrams. Not `person()`: the filter read only
  `creatorType == "author"`. Zotero's primary creator type depends on the item
  type, and these records are correctly typed; it is the reader that was wrong.
  Fixed, with `inventor`, `programmer`, `presenter` and the rest as primary —
  and **a creator type in neither set is now treated as an author and named**,
  because an extra author is visible in the bibliography and a missing one is
  not. This library has lost authors silently once; the fail-visible direction
  is the one to default to.

  **11 are brace-wrapped single-token creators, and mostly an improvement.**
  `{WHO}`, `{US Environmental Protection Agency}` and `{The Mendeley Support
  Team}` are correct BibTeX for a corporate name. Two shapes are not:

  - **`{others}` is a bug and is fixed.** `and others` *is* BibTeX's et-al.
    marker; wrapping it produces a literal author surnamed "others", in
    `Saibil1993Atp`, `Demeyts1973Insulin` and `Ullrich1985Human`.
  - **`{J. B. Ames}` is a data defect, not a rendering one, and is deliberately
    not "fixed" in the renderer.** Zotero holding a name in a single field is
    Zotero asserting that the name is unstructured, and braces are the faithful
    rendering of that — correct for `{WHO}`, wrong here, and nothing in the
    bytes distinguishes them. Guessing would be splitting "Research Council of
    Norway" into an author called Norway. `looks_like_a_person()` names
    candidates for `zotero_edit.py` and changes nothing. `Jbames1997Molecular`
    is the record whose citation key is already wrong from this name shape, and
    the key stays as it is.

- [ ] **Run `--compare`.** That is the library session's, and it is the step
      that decides whether this translation is right. Until it has run, nothing
      here is known to be correct — the fixtures only prove the translation does
      what I expected, which is the check that cannot fail.
- [x] **The refresh is wired up**, `zotero_source.py --refresh`, 2026-10-02. The
      generators are untouched: what changed is where documents come from and
      where bytes come from. An offline end-to-end test runs it against a
      stubbed Zotero with the PDF on local disk and asserts the thing that
      matters most — **no citation key is minted and none moves**, which is the
      whole purpose of namespacing the ids in September.

      It supersedes `.mirror/retired.json` rather than being blocked by it: that
      marker says the mirror is frozen *as a Mendeley mirror*, and this is
      precisely the thing that stops being true. The standing staleness notice
      is replaced by a real status line on the first successful run.

      **Three things it does not do, said out loud rather than left to be
      found.** It does not write `annotations/` — Zotero keeps annotations as
      child items of attachments and `ZoteroSource` does not read them yet; the
      cost is known and low, since the migration found 15 Mendeley annotations
      and every one was a publisher string rather than a highlight. It does not
      write `folders.json`, for the same reason. And it deletes nothing, so a
      record removed from Zotero leaves `text/` and `pdf/` orphans that
      `get_pdf.py --attachments` finds.

### And the recovery run was a no-op — `0.19.1`

`--refresh --ocr` reported **0 extracted, 2730 unchanged** and reached none of
the 110 scans it existed to recover. The destructive run had left them as
`status=no-text`, which is in `DONE_STATUSES` — and `has_artifact` is satisfied
by that status *without a file*, so every one skipped.

**A status is the verdict of the reader that produced it.** `no-text` means the
plain reader found nothing. `--ocr` is a different and stronger reader, and
treating the weaker one's verdict as final made the flag a no-op in precisely
the case it was built for. A run carrying a capability the previous run lacked
must not inherit its verdict.

`not-pdf` is deliberately exempt — OCR does not help a video file, and retrying
those every run would be motion.

This is the third consecutive defect in the same conceptual place: what the tool
is entitled to treat as settled. `failed` was unfinished and looked done; an
existing extract was evidence and the state was not; `no-text` was a verdict
that a stronger reader must overturn. Each was found by running the thing and
counting, and each one made the next visible.

### The re-extract worked, and destroyed 108 OCR'd extracts — `0.19.0`

Everything the re-extraction was for happened: control characters 1,087 → 1,
page markers intact, `pdf/` untouched, annotations and collections read. And
extracts went **2,740 → 2,632**, with `ocr: true` going **109 → 0**.

A plain re-extract of an image-only scan reads 0 characters, which is the
`no-text` branch — and that branch did `text_target.unlink(missing_ok=True)`.
108 extracts produced by a deliberate OCR pass in September were deleted by a
run that had read nothing. `text/Weeks1971Role.md` is simply gone.

**Nothing warned, and from inside the run nothing could.** That is the part
worth keeping. The state cannot help: under the new backend the prior entry is
keyed by a Zotero id and knows nothing about an OCR pass performed under
Mendeley ids. **The extract itself is the only durable evidence that it was
OCR'd — which is exactly why it carries `ocr: true`** — so the guard has to read
the file, not the state.

The rule now: **a result of nothing never deletes an extract that has
something.** If this run read less than the file already holds, the file stays,
the state records `ocr` or `ok` rather than `no-text`, and the report says the
extract was kept and that `--ocr` was not given. A header-only file is still
removed, so a genuine no-text scan is reported rather than hidden behind an
empty extract.

Same family as the 0.11.0 archive erosion, and the resemblance is exact: a run
destroying an artifact it did not create, on the assumption that its own result
is the authoritative one. That one was about bytes and this one about text, and
both were found by someone counting files afterwards.

Recovery is an `--ocr` pass, which regenerates them.

### One attachment the API would not describe stopped the whole run — `0.18.2`

`annotations_by_doc` made **one request per attachment**, 2,745 of them, and
`items/HNH4IS55/children` — the `.avi` on `Shan2011How` — answers **400**. The
exception left the refresh with 0 of 2,732 extracted and **no status written at
all**, so `mirror-status.md` still carried the word of a run that never
finished.

Three defects in one, all mine and all in code written an hour earlier:

- **The expensive irreplaceable work sat behind the cheap optional work.**
  Annotations ran before extraction. This library has roughly two annotations
  and 2,732 extracts; the ordering was exactly backwards. Extraction now runs
  first, so a failure in the second costs only the second.
- **A per-item failure aborted the run.** `harvest_attachments` has carried
  "one bad file shouldn't stop the run" since the beginning, and the new code
  did not. Annotations and collections now degrade to "could not be read" with
  the reason, and the refresh continues.
- **A crash left the previous status standing.** `mendeley_mirror.main()` has
  always had a handler that writes FAILED on an exception; `refresh()` had
  nothing equivalent, so an unexpected raise was silent in the one file whose
  job is to say what happened. It writes the failure now.

**And the design was wrong beneath the bug.** Asking `itemType=annotation` once
and grouping by `parentItem` replaces 2,745 requests with one, is three orders
of magnitude faster, and has no per-attachment failure to be killed by. The
400 that exposed this is now unreachable rather than merely survivable — which
is the better kind of fix, since the next odd attachment will not need its own
handler.

### And the fix for that one was masked by the skip — `0.18.1`

The re-run on `0.17.1` extracted nothing. The 2,732 `failed` entries the broken
run had written carried the right filehash, and `text/<key>.md` still existed
from the Mendeley era — so the skip condition matched on both and counted every
one as *unchanged*. **A failed extraction had become indistinguishable from a
finished one**, and the re-extraction silently did nothing.

It surfaced only because two other attachments failed outright and tripped the
status that `0.17.1` had just made honest. Had those two succeeded, the run
would have reported **ok**, `2732 unchanged`, with 2,732 papers never extracted
and a mirror that looked complete.

**The cause is what the skip treated as evidence.** It asked "is there a text
file?" — and a text file written by an earlier run, under a different backend,
is not evidence that *this* attachment was extracted. The stored status is the
authoritative record and the file is only a guard against a status claiming `ok`
with nothing on disk.

`DONE_STATUSES` is now an explicit list of what counts as examined — `ok`,
`ocr`, `garbled`, `no-text`, `not-pdf` — and anything else, including `failed`,
an unrecognised value, or nothing at all, is unfinished work and gets
re-examined. **Deliberately a list of what IS done rather than of what is not:**
a failure status added later should default to being retried, not to being
skipped, and that default is the whole difference between this bug and no bug.

No state editing is needed to recover: the 2,732 `failed` entries now fall
through on their own.

### The first real run failed 2,734 of 2,734 and said **ok** — `0.17.1`

Three defects, and the middle one is mine in brand-new code.

**The cause: a lazy import nobody declared.** `extract_pdf_text` imports
`pymupdf` lazily, `zotero_source.py`'s PEP 723 header declared only `requests`,
and `uv` therefore installed only `requests`. Every attachment raised
`ModuleNotFoundError` inside the per-attachment `except Exception`, was counted
as a failure, and the reason was thrown away. 2,734 times. The PDFs were read
off disk perfectly — `re-used 2729 PDFs already on disk` — so the failure was
entirely downstream of everything that had been tested.

**The dependency test passed, and its rule was the problem.** It judged
`pymupdf` by a direct `import pymupdf` alone, on the stated grounds that
mendeley_mirror imports it lazily "so it is NOT transitive". That was true while
only scripts importing it directly reached the PDF stack, and it became false
the moment `zotero_source.py` called `harvest_attachments`. **A lazy import is
still a dependency of whoever reaches it.** The rule now counts a reference to
`harvest_attachments`, `extract_pdf_text` or `ocr_pdf_text` as requiring it.

**`write_status(out, True, started)` was hardcoded**, so a run that failed
everything wrote `**ok**` and "Everything in this folder is current as of the
run above". That is the fifth instance of this family in this repo and **the
first I introduced in new code, in the same session as documenting the other
four.** Knowing a failure mode by name does not stop you writing it. The status
reflects the run now, a failed run exits non-zero, and a failed run does **not**
clear `retired.json` — it has not earned the right to declare the mirror live
again.

**And the handler discarded `exc`.** "2734 failed" with no reason for any of
them turns a one-line diagnosis into an investigation. The first five failures
are logged individually now and the sixth says that further ones are not,
because five is enough to recognise a systematic cause and 2,734 lines is not a
log.

Nothing was destroyed: `library.bib` and `index.md` were correct throughout, the
archive was untouched, and the 2,734 `failed` state entries mean a re-run
retries all of them.

- [x] **Annotations and folders from Zotero**, `0.18.0`. `--refresh` is now a
      complete replacement rather than a replacement for the parts that matter.

      **Zotero hangs an annotation off the ATTACHMENT, not off the record.** A
      run that walked each record's own children would find none and look
      exactly like a library with no annotations — which is what this library
      looks like anyway, so the empty result would have been indistinguishable
      from working. `annotations_by_doc` takes the attachment map for that
      reason.

      **Ordering comes from `annotationSortIndex`, not from the rectangles.**
      PDF coordinates increase *upward*, so sorting highlights by a raw `y`
      prints every page backwards; the sort index is `pageIndex|offset|y`,
      already in reading order, and its offset field goes in as the `y` that
      `annotation_markdown` sorts on. The printed `annotationPageLabel` is
      preferred over `pageIndex`, because a page label is what a reader cites
      and the index is zero-based and counts the cover.

      A highlight carrying a comment keeps both: the comment is the reader's
      own words and the one part of an annotation that cannot be recovered from
      the PDF.
- [ ] **Then the three decisions in item 1** become answerable with evidence
      rather than argument.
**Two numbers in this file about control characters were wrong, measured
2026-10-02 by the library session against all 2,740 extracts.** The count is
**1,087**, not 2,459. And the reason given for caring was false: these files are
*not* invisible to search. GNU grep treats a file as binary on a NUL or an
encoding error, and **not one of the 2,740 extracts contains a NUL** — `grep -rl`
finds `Barnett2002Computer` and its 10,024 control bytes without complaint.

The mechanism is real and `test_mirror.py` proves it, using a NUL. What was
never checked is whether the library's own extracts contain one. They do not, so
a test that passes has been standing behind a claim about files it does not
describe — the same shape as a fix verified only against the case that prompted
it, with the verification one step further from the thing it is trusted for.

Stripping them is a **text-quality** argument, not a findability one. Still worth
doing at zero network cost; just not for the stated reason.

- [ ] **Re-extraction is now cheap, which changes that decision.** Item 1 framed
      it as trading verification for bandwidth: re-extracting ~2,700 attachments
      meant re-downloading them. It does not any more — `<out>/pdf/` holds every
      one and `find_archived` reads it off local disk, so a full re-extraction
      costs no network at all and would regenerate every extract with current
      code, clearing the 1,087 that carry control characters. The adoption path
      was the right answer to the old question and is probably the wrong answer
      to this one.

## 14. The toolkit after the cutover — what has a Zotero counterpart and what does not

Cameron, 2026-10-02: *"your toolkit is still incomplete!"* The library session
checked the clone rather than recalling it, and was right: nothing in the repo
created a Zotero item at all.

| Mendeley tool | Zotero counterpart | state |
|---|---|---|
| `mendeley_mirror.py --refresh` | `zotero_source.py --refresh` | done |
| `mendeley_edit.py` | `zotero_edit.py` | done |
| `inbox.py` | **`zotero_inbox.py`** | done, 2026-10-02 |
| — | `zotero_attach.py` | fills existing slots only, by design |
| `mendeley_push.py` | `zotero_push.py` | done, 2026-10-02 |
| — | `zotero_delete.py` | done, 2026-10-02 |

`get_pdf.py`, `refs.py`, `finding.py`, `doixref.py` and `pdbxref.py` read the
mirror files and are backend-agnostic by construction. `get_pdf.py` still serves
from `~/.cache/mendeley-mirror/pdf/` and works unchanged.

**The blocking gap was filing a PDF fetched by hand.** Ten of the eleven papers
on the current fetch list are published versions of records that already hold a
preprint, so filing one means creating a *second* attachment on an existing
record — and `zotero_attach.py` has no way to create an attachment at all. There
was no route from a downloaded PDF into the library except dragging it onto the
item in the Zotero client.

`zotero_inbox.py` closes it, and reuses `inbox.py`'s identification pipeline
unchanged: the DOI out of the filename, the PDF metadata and the first page,
resolved through Crossref and checked against the page in front of you. That
half was always backend-agnostic; only create-and-upload was Mendeley's.

**One known consequence, stated rather than discovered.** It uploads to Zotero
and does not write to `<out>/pdf/`, because the stem an attachment is archived
under depends on its position in the record's attachment list and is not known
until the next refresh reads it back. So the archive lags by one refresh for a
newly filed paper. That matters because the archive being complete is the whole
reason it exists.

- [x] **Whether a text-mode refresh should archive bytes it fetched: answered
      by deletion, 2026-10-02.** The question assumed `<out>/pdf/` was a
      durable store worth keeping complete. Cameron's view, and his library:
      it was a **transit buffer** for the Mendeley → Zotero round trip, both
      ends of which are now done. So a refresh owes it nothing, the `zotero_inbox`
      lag is not a lag, and the right answer was to stop keeping it rather than
      to keep it better. See item 19.

## 18. `get_pdf.py` on Zotero — and the archive answers almost every call

Asked for by Cameron 2026-10-02. The script fetched from Mendeley, which is
retired, so the only thing still working was a cache hit.

**Three sources now, and the first needs nothing.** The cache; then
`<out>/pdf/` when there is one; then Zotero. **That middle source went away the
same day** — see item 19 — so on this library every call now reaches Zotero. The
branch stays because it costs a dict lookup and is right for any machine that
does hold an archive.

**The archived file is served in place.** It belongs to the mirror, so this
never copies, renames or quarantines it — the cache-hygiene branch that moves a
wrong-page-count file aside is about files this script put there, and applying
it to the archive would have made a reader's tool a writer of someone else's
directory.

The archive index is built by **listing** `pdf/` once rather than probing
candidate names, because here the question is "what is there" and one pass
answers it for every key — including whatever suffix each was archived under,
`.cif`, `.pdb` and one mangled `.-_charmm_g` among the `.pdf`.

**The Mendeley path is deleted rather than kept as a fallback.** A fallback that
always fails is worse than none: it turns "this paper is not in the archive"
into an authentication error, which is a different problem with a different
remedy.

`--attachments` took a client and now takes **rows**. It was always built around
one bulk `/files` call — asking per document is the difference between a sweep
somebody repeats and a sweep nobody runs twice — and what it actually needs is
the rows that call returns. Zotero's `itemType=attachment` is the direct
analogue, with `parentItem` for `document_id`. Decoupling it is what let the
Mendeley code go, and it simplified four test stubs into four lists.

Verified against the live library rather than the suite alone: a cached paper,
an archived one served from `pdf/`, and an unknown key reported without a
credential prompt.

## 15. `zotero_push.py` — adding a reference by DOI or arXiv id, built 2026-10-02

The last of the four gaps. `from_doi` and `from_arxiv` are reused from
`mendeley_push.py` unchanged — resolution through doi.org content negotiation
answers for DataCite DOIs that `api.crossref.org` 404s on, and none of that was
ever Mendeley-specific. Only the create half was.

`csl_year()` decides the year, as it must: CSL `issued` is the earliest date a
work appeared, so an Advance Access paper arrives a year early. CHARMM36m went
online in November 2016, appeared in the January 2017 issue, and was filed and
cited as 2016 before anyone noticed.

**Fields are checked against Zotero's own template for the item type**, fetched
from `/items/new`, and anything with nowhere to go is reported. Zotero **ignores
an unknown field silently** rather than refusing it, so a value sent to a field
the type lacks does not fail — it vanishes. A `report` has no `volume` and no
`publicationTitle`, and without the template a pushed report would quietly lose
both. This is the same hazard `zotero_edit.py` guards by requiring a field to
exist on the item being edited; here there is no item yet, so the template is
the equivalent.

Two refusals. It will not create a record the library already has, checked by
DOI and then by normalized title through the same `existing_document` the inbox
uses — a duplicate is not a tidy-up later, it is two citation keys for one paper
and `assign_citekeys` will mint the second one happily. And **it does not assign
a citation key**: the key comes from the next refresh, where `assign_citekeys`
mints it once and keeps it forever. A key invented here would be a second
authority for the one thing in this system that must have exactly one.

A dry run needs no credentials, because the client is built only at the create
step — useful for seeing what a DOI resolves to before deciding whether the
paper belongs in the library at all.

### The dependency rule, wrong three times before it was right

1. **Judging by a direct `import pymupdf` alone** missed `zotero_source.py`
   calling `harvest_attachments`, which imports it lazily. 2,734 extractions
   failed and reported as a successful run.
2. **Widening it to "anything importing from `inbox`"** caught that — and I then
   replaced it with a hand-maintained list of PDF-reaching function names, on
   the belief that `inbox.py` imported `pymupdf` lazily like `mendeley_mirror`
   does. **It does not: `inbox.py:60`, column 0.** So `zotero_push.py` shipped a
   header that could not run, and the library session hit it on the first dry
   run. *The broad rule had been right, and I narrowed it on an assumption
   nobody checked — including me, in the same session that keeps writing down
   "verify, do not recall".*
3. **The function-name list was also incomplete**, which the suite caught at
   once: `get_pdf.py` imports `pymupdf` itself, lazily, to page-count a cached
   PDF, and no name in the list appeared in it.

The rule is now **derived from the import graph** rather than from any list.
A script needs `pymupdf` if it imports it at any indentation of its own; or if
it imports a local module whose import *runs* `import pymupdf`, found by reading
that module and recursing; or if it calls one of the three functions in
`mendeley_mirror` that import it lazily, which no import graph can show.

Column 0 matters in exactly one of those three and not the others, and that
distinction is the whole content of the rule: **a module-level import in a
module you import is yours; a lazy import is the caller's.** The library
session's one-line statement of it is better than the three paragraphs it took
to get there.

## 16. `zotero_delete.py` — built 2026-10-02, from what was done by hand

The library session hand-rolled 16 DELETEs: 11 duplicate records and 5
byte-less attachment stubs. Exact keys, `If-Unmodified-Since-Version`, backed up
first, verified after — and it flagged that itself, in the right terms:
**discipline standing in for a tool, and the next person will not necessarily
bring it.** Asked what it needed, it specified from those 16 rather than from
imagination, and the first ask is the one worth keeping.

**A dry run names what SURVIVES as well as what goes.** *The dangerous deletion
is not the one you listed, it is the sibling you did not.* Deleting
`Frankel1998Hiva/b/c` is only correct if `Frankel1998Hiv` is still there and
still holds its attachment — a fact about the records you are **not** naming,
which no amount of care about the ones you are naming will tell you. So the
survivors sharing each title are printed with their attachment counts, and a
deletion leaving **no** survivor is refused unless `--allow-last-copy` says
otherwise: that is a paper leaving the library, not a duplicate being tidied,
and it should have to be spelled.

Siblings are matched by **title**, not by the shape of the key.
`assign_citekeys` appends a letter only when it must, so two records can
collide without either being suffixed, and a key-prefix rule would miss exactly
the pair that looks least like a duplicate.

The backup is automatic, item plus children, written *before* anything is sent,
to `.mirror/deleted-<timestamp>.json` — in the mirror rather than a cache so it
travels, because deletion is the one operation here with no undo.

**One correction to the spec, from a mistake this repo already made.** A
byte-less attachment cannot be identified by `md5: None`. That field says the
item records no file, which is a different claim: on 2026-10-02, 1,060
attachments reported `md5: None` and Zotero held bytes for every one of them —
the error this file had to withdraw in item 1. `--stubs` asks the **file
endpoint** and treats a 404 as the evidence, which is a read and is
authoritative.

Two deliberate non-features, both at the library session's request and both
right. It never touches `citekeys.json`: append-only by design, a deleted key
stays reserved, and a key cited in a manuscript keeps resolving. And it never
deletes from `pdf/` or `text/` — `Frankel1998Hiva/b/c` held a *different* scan
from the record that survived them, so deleting the records was right and
deleting their archived files would have destroyed a unique copy. Tidying
orphans needs the uniqueness check in `get_pdf.py --attachments` and is a
separate, deliberate step.

## 19. The archive is gone, deliberately, and the durability argument changed

Cameron deleted `<out>/pdf/` on 2026-10-02: all 2,740 files, 4.2 GB. His
reasoning, from `RENAMED-FROM-mendeley.md` in the library: **it was always a
transit buffer for the Mendeley → Zotero round trip, not a durable store**, and
both ends of that trip are done.

**Verified per record before the deletion, not by hash-set membership**: 2,725
of 2,734 attachments hold exactly the bytes their extract was made from, 4 more
were uploaded that day and not yet extracted, and 5 were byte-less stubs that
were removed. That is a stronger check than this repo asked for anywhere.

**This file had the argument the other way round and was wrong about whose it
was.** `--backfill` was built in September to make a copy that outlived a
subscription; several entries here call `<out>/pdf/` "the copy that is not
behind a service, which is the reason it exists". It served that purpose exactly
once, for the migration, and the premise expired when the papers landed in
Zotero. Durability of someone's library is their call, not the tool's, and the
tool's job was to make the copy available for as long as it was wanted.

What follows, and it is stated rather than argued:

- **The PDFs now exist only in Zotero.** That is one service, with an account
  behind it, and it is a different exposure from yesterday's. Cameron knows; the
  verification above is what makes it a decision rather than a loss.
- `find_archived` always misses on this library, so a re-extraction downloads
  every attachment from Zotero. That works and is slower.
- `get_pdf.py`'s archive branch never fires here. It stays because it costs a
  dict lookup and is right for any machine that does hold an archive — including
  this one, if `--backfill` is ever run again.
- `--backfill` still works and is still the way to rebuild one. Nothing about it
  changed; what changed is that nobody currently wants what it produces.

## 20. The refresh ignored `pairings.tsv`, and two keys served the wrong paper

Found by `library`, 2026-10-03. After the 15:47 UTC refresh,
`text/Hirota2000Effect.md` held *"Effect of Charge"* — the sibling's paper —
and `text/Daoulas2005Molecular.md` held *"Detailed Atomistic"*. **The unsuffixed
key, the one anyone cites, resolved to a different paper.** Silently, and
visible only by reading page 1.

**Two things assign a stem and only one of them was reading the decision.**
`zotero_attach.py` consulted `.mirror/pairings.tsv` when choosing which
attachment to fill — that file exists precisely because 62 records could not be
paired by any rule and a person decided them by hand. The refresh numbered
attachments by the order Zotero's children endpoint happened to return, and
never opened the file. When they disagreed, the refresh won, because the refresh
is what writes the extracts.

That is my gap, and it is the shape worth remembering: **a decision file is only
authoritative over the paths that read it**, and I built one for the writer
without asking which other code decides the same thing.

`order_by_pairings` puts a named stem at its own index and leaves everything
else in its existing relative order, so a record the file does not mention is
untouched — the negative case that must not break is every ordinary
article-plus-supplement pair. A pairing naming an attachment the record does not
have is **reported**, because a stale authority is worse than none.

### Reordering alone would have repaired nothing

This is the half that would have made the fix look like it worked. The skip
tests the filehash and whether a file exists at the target — and after
reordering, the bytes are unchanged and `text/Hirota2000Effect.md` still exists,
*holding the other paper*. Every one of those records would have been skipped as
unchanged.

So `state.json` records the **stem** each attachment was written under, and a
stem that has moved reads as unfinished work. Third member of that family now:
`failed` is not a result, `no-text` is only the plain reader's verdict, and a
verdict belongs to the **name it was written under** as well as to the reader
that produced it. An entry predating this carries no `stem` and still skips, so
nothing re-extracts for want of a field that did not exist.

`state.json` gains a key, so `__version__` moves — `0.25.0`.

## 25. The stale-rules counter counted things it did not mean — `0.28.1`

`library`, the morning after a full `--reassess` put every entry on rules 3:
the refresh printed **"22 extracts were produced under older extraction rules
(now 3)"**, and it had just filed exactly 22 new attachments, which extracted
fine. It asked whether the number was right. It was not.

A brand-new attachment has **no prior entry at all**, so `prior.get("rules", 1)`
returns the default — for a record that was never produced under any rules. The
count sat at the decision point, before the skip, so it counted two things its
sentence did not say: attachments being extracted fresh in that very run under
the current rules, and attachments about to be re-read for some other reason,
such as a moved stem.

Counted now where the attachment is actually **left alone**, so the number means
what the sentence claims: *extracts on disk, produced under older rules, that
this run did not re-read.* That is also the only version of it anyone can act
on, since `--reassess` is the action and it applies to exactly those.

**Caught by arithmetic, again** — "I had just filed exactly 22" — which is how
every counting defect in this tool has been found. Not one was found by reading
the code, including by me, including in the sessions where I was looking for
precisely this.

## 24. A stamp on every page is not a text layer — `0.28.0`

`library`'s 0.27.0 run: **status ok, exactly six extracts changed, all six the
new OCR set, no OCR extract lost.** The six are the JSTOR-class fix working. And
`Kirkpatrick1983Optimization` still was not reached.

Its per-page text lengths are `[1067, 152, 152, 152, 152, 152, 152, 152, 152,
152, 152]` — page 1 is JSTOR's cover, pages 2–11 are image scans each carrying
**only** the identical 152-character JSTOR footer. So every page "has text", the
page-fraction test saw 11 of 11, and OCR never ran.

**This is the ProQuest stamp again, and `content_chars()` was written for it in
September.** That function already drops lines repeated across most pages, and
the whole-document test beside it was already built on its result. The
page-fraction test I added in 0.26.0 was built on *raw* text — in the same
function, two lines apart from the one that knew better.

`content_per_page()` is that logic factored out per page, and the fraction is
measured on it. Kirkpatrick is then 1 page in 11.

`library` asked for the negative case to be kept and it is: a born-digital
paper whose running header repeats but whose pages carry body text must not be
OCR'd, and that scores 1.00. An earlier version of that fixture repeated one
paragraph on every page and scored 0.00 — correctly, since a document whose
every page is identical *is* boilerplate all the way down. **The test was wrong
and the detector was right**, which is worth recording because the reflex is the
other way.

### The cost of a rules bump, stated

`EXTRACT_RULES` moves to 3, so `--reassess` is again the whole library: 2,741
attachments, about 93 minutes on the last run. That is the second full pass in
two days and it is the honest price of changing what extraction decides.

`library` asked whether the last one re-reading all 2,741 was expected. It was:
entries written before 0.27.0 carry no `rules` key at all, so every one read as
older. This one is the same, and after it every entry carries `rules: 3`, so the
*next* bump can be measured rather than assumed.

### Verified, and the arc is closed

`library`, against a pre-run snapshot: status ok, **exactly four extracts
changed, all four newly OCR'd** — `Kirkpatrick1983Optimization` (2,712 → 60,215
characters), `Barresinoussi1983Isolation`, `Popovic1984Detection`,
`Hartmann2014American`. Nothing else moved and no OCR extract was lost; the
count is 120.

It also checked `Kirkpatrick`'s **page offset** against the rendered page at
both ends — +669 — which tests the `<!-- p. N -->` contract rather than merely
that text appeared. An OCR pass that produced text under the wrong page numbers
would have passed every other check in this report.

**A consequence worth drawing, and resolved the same day.**
`stamp-only-extracts.tsv` lists the papers found to carry a repeated stamp and
nothing else — exactly the class this fix reaches. `library` checked all 22
entries: **every one is still attached and now `ocr: true`, at 11k to 147k
characters.** So the content test missed nothing on the list, and the class is
clear across its whole known membership rather than only the four that moved in
the last run.

It kept the file and added a `resolved` column rather than emptying it, which is
the better call and not the one I suggested: **no tool reads that file, so it is
a historical record, and emptying it would destroy the evidence that the problem
existed and was fixed.** The same reason this roadmap keeps its wrong turns.

**What the five rounds have in common**, since the arc is worth one sentence:
each fix was correct and each was defeated by something it could not see — a
verdict's stem, a verdict's rules, a stamp counted as text. The pattern is
adding a new judgement without asking which existing judgement already answers
the same question. `content_chars()` had the answer to the last one before any
of it started.

## 23. The 0.26.0 run worked, and found two more — `0.27.0`

`library`'s diff: **Hirota and Daoulas repaired, none of the 59 had stale text,
all 110 OCR extracts survived.** The snapshot-then-diff is what made those three
statements possible, and the middle one is the answer to question B that I could
not give from here.

**`--ocr` failed 116 times instead of once.** rapidocr is deliberately not in
any header — it is a large optional stack for an occasional pass, passed in with
`uv run --with rapidocr-onnxruntime` — and the cost of that choice is that
forgetting it is easy. The failures landed inside the per-attachment handler.
**A missing dependency is a property of the run, not of an attachment**, which
is written in a comment beside the pymupdf check in `zotero_source.refresh` and
was not applied in the same file two days later. `require_ocr_stack()` now fails
once, at the start, and prints the exact command.

**And `Kirkpatrick1983Optimization` was skipped as already done**, so the JSTOR
fix never reached the one paper it was written for. It had been extracted under
the old rules, stored `ok`, and the run that *shipped* the new rules treated
that verdict as final.

This is the fourth time in the same place — `failed` is not a result, `no-text`
is the plain reader's verdict, a verdict belongs to its stem — and the
generalization it was missing: **an `ok` is a verdict reached under a particular
set of extraction rules.** `EXTRACT_RULES` is stored per entry and moves when the
extraction *decision* changes, not when the code does and not with
`__version__`.

A run **reports** how many extracts predate the current rules and does not
re-read them by default; `--reassess` acts on it. Automatic re-extraction on
every rules bump is a real cost and the wrong default, but silence was what let
this happen, so the count is printed either way.

### A notice that had never printed

Fixing that surfaced an older one. `kept` — the 0.19.0 notice saying an OCR'd
extract had been **preserved** from a run that read nothing — was nested inside
`if archived or unarchived:`, the backfill branch. **It could only ever print
during a `--backfill`, and has never appeared on an ordinary refresh.** I added
it there by matching the indentation of the line above it.

So the message written to end a silence was itself silent, for four days, in the
one case it was for. Both it and the new rules notice are now at run level where
they belong.

## 22. 0.25.0's repair was blocked by a branch 0.25.0 added — `0.26.0`

The repair refresh reported the two extracts **unchanged** and they still served
the wrong papers. The cause is a compatibility branch I wrote in the same commit
as the fix, and asserted in a test: *"an entry predating this carries no `stem`
and still skips, so nothing re-extracts for want of a field that did not
exist."*

The `Hirota2000Effect` entries were written by **0.24.0**, which recorded no
stem. So the fix could not see that the wrong attachment had written the file,
and skipped it as unchanged — on exactly the records it shipped to repair.
**The fourth test in this suite found asserting its own defect**, and the first
where I wrote the test and the defect in one sitting.

**No recorded stem means the stem cannot be vouched for**, and that is
unfinished work. The cheap alternatives do not survive contact: a record's
*current* attachment count says nothing about which attachment wrote an existing
file — after the parked duplicate was deleted, `Hirota2000Effect` had exactly
one attachment and the extract on disk was still the other one's.

**The cost is one full re-extraction**, 2,740 fetches from Zotero, once. That is
the price of having shipped `state.json` without the stem, and every entry
written afterwards carries it.

**And the extract now names the attachment that produced it.** `attachment:` in
the front matter, beside `record_id:` (renamed from `mendeley_id:`, which named
the wrong service and the wrong thing). An extract could not previously say
whether it belonged under the stem it sat at; two of them sat under the wrong
one with nothing in the file to show it. This makes the question answerable from
the library alone, forever after, without the state.

`text/<key>.md` gains a front-matter field and `state.json` a key, so both
contracts moved — announced to `library` before the working tree carried it.

### B, answered the way it was asked

`library` asked whether "59 reordered, 0 re-extracted" meant those 59 already
matched, or 59 more records silently wrong — **with a check that could come out
either way**. It is the same defect as above at scale, and there is no way to
tell from the outside: the extract did not record which attachment wrote it.
Under `0.26.0` the next refresh re-extracts every entry with no stem, which both
answers the question and repairs whichever answer it is. The new `attachment:`
field is what makes it checkable cheaply next time.

### C: a scan whose cover page carries all the text

`Kirkpatrick1983Optimization` is a JSTOR PDF: 11 pages, page 1 is JSTOR's cover
*with a text layer*, pages 2–11 are images. 2,986 characters over 11 pages clears
`80 × 11 = 880` comfortably, so OCR never ran and the article body was invisible
to search.

The volume test cannot see this, so the new test is **how many pages carry
text** — counted from the `<!-- p. N -->` markers, which already are the answer
since a page with nothing on it produces no chunk. Half is the line, and the
negative case sets it: a born-digital paper with a figure-only page or two must
not be OCR'd wholesale, and that is nine pages in eleven against this scan's
one.

It changes **when OCR runs** and not what counts as `no-text`. A document whose
few pages of text are real text is not a scan, and calling it one would delete a
usable extract.

### D: `pairings.tsv` belongs to the library session

Asked and answered: **the tool reads it and never writes it**, so it is
`library`'s to edit. The three stale entries should go. The refresh reporting
them is the file doing its job — it is meant to be the authority, so it naming
an attachment that no longer exists is worth a line rather than a silent skip.

## 21. `zotero_delete.py --attachment` — one attachment, bytes and all

Asked for by `library` the same day, and blocking: `--key` deletes records and
`--stubs` deletes 404s, and neither can remove a single attachment that holds a
file. The two parked duplicates from the pairing work are exactly that.

Same guards as the record path — a dry run naming survivors, an automatic
backup, a version precondition, a refusal to leave a record with no attachment
at all unless `--allow-last-copy` says so.

**One guard belongs only here: annotations hang off the attachment, not the
record.** They go with it, and they are the one part that re-uploading the file
cannot bring back — a reader's own highlights and comments. The dry run counts
them and the backup carries them.

## 26. What has actually been run, and what has not

Written 2026-10-04 before this session's context was compacted, because it is
the one class of thing held nowhere else: a summary keeps conclusions and drops
the question *has anyone ever done this against the real library?* Eight tools
were built in three days and they have not been exercised equally.

**Exercised against the live library, with a result reported back:**
`zotero_source.py --refresh` (many times, five rounds of defects),
`zotero_attach.py` (2,608 uploads, 30-record md5 sample),
`zotero_edit.py` (55 author edits, then 1,174 PMID restores),
`zotero_delete.py` (`--key` on 11 records; `--attachment` on 2),
`zotero_inbox.py` (22 attachments filed 10-04), `zotero_migrate.py` (the
migration itself), `get_pdf.py` (cache and archive paths).

**Built and never run against the live library** — each is a claim this repo
makes and has not tested:

- **`doixref.py`.** Built 10-02, validated on four records I chose, handed over,
  and **no full run has been reported.** The ~2,270-record audit it was built
  for has not happened, so the author-mismatch class is measured at three
  records and inferred beyond that.
- **`zotero_push.py`.** One dry run, by me, on ff14SB. **It has never created a
  record.** The `/items/new` template check, the duplicate refusal and the
  "no citation key is assigned" property are all untested outside fixtures.
- **`--pair-by-order`.** The 62 ambiguous records were done by hand instead,
  which was the right call — so this code path has never run at all.
- **`zotero_delete.py --stubs`.** The five byte-less stubs were deleted by hand
  before the tool existed. The file-endpoint test that distinguishes a stub from
  an attachment with `md5: None` has never met a real one.
- **`get_pdf.py`'s Zotero fallback.** Every call so far was answered by the
  cache or the archive. Now that `<out>/pdf/` is empty it is the only path left,
  so the first real use will exercise it.
- **An `imported_url` upload.** ROADMAP item 1 records this as UNVERIFIED and
  asks for one to be done alone with `--key` first. The bulk upload ran and a
  30-record sample verified, but **nobody confirmed an `imported_url` item was
  in that sample**, so whether Zotero accepts an upload against one is still
  not known from evidence. 24 records depend on the answer.

**A path deliberately not taken**, and the reasoning rather than the verdict:
`--reassess` re-reads the whole library on every rules bump, ~93 minutes. A
bounded version is available — read `text/*.md` locally, find the extracts whose
own page pattern suggests the new rules would change them, re-read only those.
It was not built, because four of the five extraction defects in this file came
from a clever filter that looked right and could not fire, and building a fifth
under time pressure to save 90 minutes is the wrong trade. **If someone builds
it later, that is the argument it has to beat, not the cost.**

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

**Brought forward 2026-09-24.** The Mendeley backend is being retired, so the
account's behaviour will change. An auth failure arriving as a shrug was a
general risk when this item was written; against an account nobody is
maintaining it is a specific one.

**Corrected 2026-10-01, and the correction is the point.** What was established
on 09-24 is that the membership **auto-renews** 2027-01-01 and that cancelling
was the plan. Four other passages in this file then said the account **lapses**
2027-01-01 — a near-opposite claim about the same date, since auto-renew means it
continues unless someone acts. Nobody introduced the error; it arrived by
repetition, a hedged fact restated until the hedge fell off, and this repo's own
commit messages and the 09-27 evacuation procedure carry it.

Cameron has not confirmed any lapse date: on 09-28, "what cancellation? that's on
hold", and on 10-01, "we are migrating away from mendeley altogether" with no
date. The migration is real; the date is not established and is not stated here
until he states it. The library session reports he has already downgraded to the
free tier, which if so is a change in the account's behaviour today and not on
some future date.

A deadline nobody set is worse than no deadline: it makes a thing look safe until
a date, and this one was used as the argument for several decisions.

**Two things the library session added by reading its own transcript rather than
accepting the trace above**, and both are worth more than the date:

- Its *notes* said "renews" throughout — the correct direction. So grepping its
  own records for the error came back clean and proved nothing; the drift was in
  what it had told Cameron on evacuation day ("these files survive the
  subscription lapse"), twice. **A claim can be right in the file and wrong in
  the mouth**, and auditing the file does not catch that. Nothing in this repo's
  verification habits reads what anyone actually said.
- **"Lapses" is passive, and it erased the only mechanism ever operating.** The
  attachment files were never at risk from an automatic expiry — they were at
  risk from a decision, which is precisely what occurred on 2026-10-01 when the
  account was downgraded to the free tier. An action, with no date attached to it
  in advance. A passive verb invents a deadline and hides an agent; a date then
  makes the invention look checkable. When a note says something *will happen*,
  ask what would have to do it.

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

## 17. The library directory, renamed — `~/Sync/mendeley` -> `~/Sync/library`

Cameron, 2026-10-02. The directory had outlived the service it was named after:
nothing in it is Mendeley's any more.

**Both names resolve, and that is not politeness.** There is one clone on this
machine and no pull step, so a hard switch would have broken every tool in the
window between saving `DEFAULT_OUT` and the `mv` — and the two Windows laptops
share the folder over Syncthing and will be renamed at some other time, or not
at all. `_default_out()` prefers `library`, accepts `mendeley`, and gives a
fresh install the new name. Same pattern as `zotero_config_dir()`, and the same
end: when nothing is left on the old name it becomes a deletion rather than a
migration.

Changed in sixteen places: `DEFAULT_OUT`, the README and `docs/index.md`
premise and trees, `docs/operating.md`'s table, two structural rules in
`CLAUDE.md`, a docstring in `zotero_source.py`, and six live commands in the
`file-inbox` skill.

**Two things this is NOT.** `~/.config/mendeley-mirror` and
`~/.cache/mendeley-mirror` keep their names — they are per-machine, invisible,
and moving credentials is a real cost against no benefit. New credentials
already go to `~/.config/offprint/`. And the README's claim that renaming the
directory "would break every machine's sync configuration to fix a word" is
withdrawn: it was an argument against doing it, Cameron decided otherwise, and
the tolerant default is what makes the cost smaller than that sentence claimed.

**What the repo cannot reach**, and is Cameron's: the Syncthing folder
configuration on panacea and both laptops, and the library session's working
directory — which under the 2026-09-18 split *is its identity*, so it is told
rather than left to discover it.

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
  re-authorizing is a real cost against an account being retired.
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
library would survive the Mendeley account going away. A default
refresh — `--attachments text`, which is what the timer and every scheduled entry
point run — deletes from that directory:

```python
if mode == "text" and local.exists():
    local.unlink()   # the text is the artifact; Mendeley keeps the PDF
```

That comment was true when it was written and is now false — but not because the
account is going away, which is how this was first argued and is the weaker case.
That argument has since needed retracting on its own account (see item 2), which
is the second reason not to have rested on it. The defect stands without any
expiry date: `<out>/pdf/` is an archive somebody built deliberately, and a
refresh deletes from it. A deadline only sets a deadline on noticing.

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
      2. Cameron is retiring the Mendeley backend. The library is final, and a
         refresh against an account nobody is maintaining would fail loudly and
         regenerate nothing anyone wants. **This reason still stands**, and it
         is his decision to reverse, not a defect to repair.

      The reboot on 2026-09-30 is the evidence that the disable holds: the
      archive came back byte-identical at **2,745 files / 4,476,820,874 bytes**,
      and `stamp-mendeley-mirror.timer` was still 2026-09-29 11:08:12,
      unrewritten. Those two numbers are the baseline for any later check.

**Still open: Cameron's two laptops.** Neither session can see their units, and
the Windows scheduled task runs `refresh_quiet.bat`. The code fix protects them
without anyone editing anything, which is the argument for having done it that
way — but it only protects them once they pull.

## 10. An attachment can be discarded without anything saying so — FIXED 0.12.0

Reported by the library session 2026-10-01 and confirmed in code, not on trust.
`Vanommeslaeghe2009Charmm` — the CGenFF paper, that record's only attachment —
had no extract, and `extraction-report.md` did not mention it. The bytes in
`pdf/` are a complete 20-page PDF with an intact text layer: 20 pages, 99,318
characters, no garbling, verified against the archived file itself.

Three defects, and the first is the smallest:

- [x] **The MIME type decided what a PDF was.** `is_pdf = "pdf" in mime_type`
      was the whole test, so a backend that misreports a type removes a paper
      from every text search of the library. The bytes decide now
      (`looks_like_pdf`). The MIME type is a hint.
- [x] **The skip was silent, and that is the real defect.** Six attachments were
      examined, judged and discarded, and the judgement was written nowhere —
      no counter, no row, no section. `extraction-report.md` is the one file
      whose job is to explain a missing extract, and for this class it said
      nothing, which reads as nothing being wrong. Every skip now carries a row
      and a reason, and `not-pdf` joined the re-report list so the row survives
      the next refresh rather than appearing once. That last clause is not
      theoretical: it is exactly how 108 OCR rows emptied out of this file in
      September.
- [x] **The archive could not be read back.** `keep` and `--backfill` write the
      attachment's real extension; the reuse lookup was hardcoded to `.pdf`. So
      six archived files were unreachable and a changed attachment went to the
      network for bytes already on disk — against an account that has since been
      downgraded to the free tier. `find_archived` is the inverse of
      `archive_suffix`, and the two now have to stay inverses.

**A third test was found asserting its own defect.** `erode.n == 1` had "the
.cif only" written beside it as though re-fetching a non-PDF were expected
behaviour; it was the hardcoded `.pdf`. The two before it were the 0.11.0
erosion pair. All three were found by changing the code and watching a test go
red — none by reading the test, and each had been read several times.

**Blast radius is honest: one recoverable paper today**, plus five rows that
should always have been printed. The reason it was worth doing now is the first
Zotero refresh, which re-examines ~2,700 attachments at once against metadata
nobody has audited. Whatever that run skips, it skips into a cached verdict that
no later refresh revisits, and this file is the only instrument that would say
so.

**And the Zotero side was checked rather than assumed, same day.** Item
`Q8E9FEKN`, child `2YFEGK72`: `contentType: application/octet-stream`, filename
ending `.-_charmm_g`. **Both halves of the defect crossed the migration intact**,
because Zotero imported through the same Mendeley API that produced them. Under
pre-0.12.0 code the first Zotero refresh reproduces the identical silent skip.
So `looks_like_pdf()` is a prerequisite for the migration, not a patch to a
backend being retired. In a 12-item sample every other attachment was
`application/pdf`, so CGenFF is the anomaly on both backends and from one
source.

- [x] **The cached `not-pdf` entries cleared** by the library session, after
      this shipped and not before: the download at the head of the branch
      precedes the `continue`, so clearing first buys a re-download and the same
      verdict. Code, then state, in that order. Only
      `Vanommeslaeghe2009Charmm`'s was cleared — Cameron deleted the other five
      attachments the same day, and their entries are what keep a plain refresh
      from fetching them again.
- [x] **A deliberate deletion is recorded, not inferred — `0.13.0`.** Deleting
      those five from `<out>/pdf/` was not enough on its own. `--backfill` tests
      only whether a file is on disk, so it read each gap as evacuation it had
      not finished and would have fetched all five back. The one command written
      to be safe to repeat was the one that undid the deletion, and the state
      entries do not stop it: the backfill block sits inside the skip branch and
      never reads the stored status.

      `.mirror/removed.tsv` is the difference between "never evacuated" and
      "evacuated, then removed on purpose". It is keyed by stem rather than
      attachment id, because the citation key is the handle that survives a
      change of backend. It **withholds a fetch and never causes a deletion** —
      a list of filenames the tool consults is exactly how a third
      archive-eroding path would arrive, and the test for that asserts a listed
      file that is present stays present.

      The library session's point, kept because it bounds the problem: the
      window closes on its own. Zotero holds no attachment bytes, so once the
      mirror is Zotero-backed there is no backfill source at all, and the
      deletion can only be undone while Mendeley still serves files. A permanent
      marker is still the right answer — Cameron's, 2026-10-01 — but the
      alternative was "do not run `--backfill` before the switch", which is a
      smaller question than it first looked.

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

### The display rule the spec now carries, which came out of verifying it

Verifying turned up a defect in the *reporting* rather than the detection: the
`and others` rows printed a fixed-width **tail** of the author field, so
`De Meyts, P. and Roth, J. and Neville, D.M. and others` displayed as
`nd Roth, J. and Neville, D.M. and others` — a mangled author name the report
had invented, and precisely the line a reader would then re-report as a defect.

**Fixed in the spec, and it was in two further places** the library session
found by looking rather than by patching what was named: titles were sliced at
60 characters mid-word with the BibTeX brace still attached, and the report
printed `hits[:40]` *silently*, so a section longer than 40 would have
under-reported itself with no sign that it had. The rule is now explicit —
**never a blind slice**: clip on a word boundary with a visible ellipsis, and a
truncated section says `... and N more NOT SHOWN`.

Re-verified here after the fix: 26 fixtures pass both directions, and the output
is byte-identical to the regenerated baseline against the live library.

Worth carrying across if this is built, because it generalises past this file: a
generated report that manufactures a finding is worse than no report, and a
silent `[:40]` is the same failure as a denominator that shrinks to fit its
numerator.

### The part worth preserving if it is built

The library session is deliberately **not** handing over every probe, because
what made this work was disjoint blind spots — this repo's comma probe could not
see `Jbames` (zero commas, not two); its front-matter probe could not see
`Abrams2002Biphasic` (no extract). If every probe moves into the tool and both
sessions read the same generated report, the cross-check dies and two copies of
one opinion remain. Known classes go in; the hunt for unknown ones stays out.


## 27. Filing a new paper, 0.29.x — what was measured and what was guessed

Asked for by `library` on 2026-10-05, at Cameron's request, after he asked why
every inbox drop needs a refresh. Four steps and roughly 26 minutes of refresh
per new paper. Three things came out of it and they are not equally solid.

**MEASURED, by `library`:** a refresh that extracted two changed attachments
took 13.3 minutes. That is the only timing in this item taken from a real run.

**FOUND BY READING:** `ZoteroSource.files_by_doc` asked `items/<key>/children`
once per record — 2,739 serial requests. At a round trip plus the 0.05s paging
sleep that accounts for roughly 11 of the 13.3 minutes. It is now **one**
`items?itemType=attachment` query. `annotations_by_doc`, the method directly
below it, had been converted to a bulk query a week earlier and its docstring
explains the reasoning at length; nothing applied it to its neighbour. A lesson
written down next to the code that needs it is not the same as a lesson applied.

**MEASURED 2026-10-05 on 0.29.2, by `library`: 203 s, down from 798 s.** I had
predicted "about a minute" and said so to a peer who then went and checked it.

The prediction is worth dissecting, because the arithmetic in it was *right*.
595 seconds disappeared; 2,739 requests at the ~0.22 s each I assumed accounts
for 594 of them. What was wrong was not the estimate of the part I removed but
the **assumption that everything else was near zero**. I never estimated the
remainder at all, and the remainder is now the whole cost.

That is a general failure mode in optimisation and it is worth naming: *the
confident number was about the part I understood, and the error lived entirely
in the part I had not looked at.* The same shape as item 26 — what has actually
been run versus what is merely believed.

0.29.3 therefore instruments the refresh by phase rather than guessing again.
Roughly 60 HTTP requests remain (28 pages of records, 28 of attachments, one
for annotations, one per collection) and they cannot account for 203 s either,
so the next number has to come from a run, not from me.

### The phase report, and what `--only` turned out to be for

`library` ran both on 0.29.3, 2026-10-05:

```
    69.3s  fetch records (items/top)
    53.5s  fetch attachments (one bulk query)
     0.4s  write library.bib
     0.1s  extract text (0 read, 2780 skipped)
     0.1s  fetch annotations
    79.7s  fetch collections (5)
   203.1s  total          --only Woo1994Phase: 199.4s
```

**`--only` saves 3.7 seconds and was built to save minutes.** Extraction is a
tenth of a second, because skipping an attachment whose filehash is unchanged
costs nothing — the premise that a targeted run avoids expensive work was
simply false, and nothing checked it before the flag existed.

It is kept rather than retired, for a reason it was not built for: it bounds an
**expensive** re-read. `--reassess --only KEY` re-reads one record under new
extraction rules instead of 2,780, which is the bounded `--reassess` recorded
further up this file as deliberately not built. It arrived as a side effect of
building the wrong thing, which is not a vindication — the measurement is now
written beside the flag in three places precisely so the next reader does not
reach for it to speed up a refresh.

**The collections row was the real finding, and my own label hid it.** The
phase printed "(5, one request each)". It is not five requests; it is five
*paged walks* over collections holding most of the library, and at 79.7 s it
was 39% of the refresh. Every item already carries the collections it belongs
to, so folder membership now comes off the records already in hand, for no
requests at all. **A label I wrote asserting a cost model is what made a 79.7 s
line look reasonable** — `library` flagged it as "looks wrong" from the
arithmetic, which is the second time in two days that someone doing division on
my output found something I had not.

What is left is two paged fetches, 123 s, and they are latency: ~2 s per page
of 100 against the Zotero API. Reducing that means fewer round trips or
`since=` incremental sync, neither of which anyone has measured. **Do not
assume that estimate either.**

### The order problem the speedup created

Attachment position decides the archive stem (`<key>.pdf`, `<key>-2.pdf`) and
therefore which attachment `text/<key>.md` is the extract OF. The per-record
walk preserved that order by accident — the same endpoint answered the same way
twice. A bulk query has no per-record order at all.

The tempting fix is to sort the bulk result the way Zotero sorts `children`.
That was rejected: it is a guess about an API's default that would be right
until the day it changed, and the failure mode is two papers' extracts swapping
names silently, under names other sessions already quote page numbers out of.
`order_by_recorded_stems` instead takes the order from the `stem` recorded in
`state.json` since 0.26.0 — the only authority that cannot drift, because it is
what the files on disk were actually named for. New attachments append. Two
attachments claiming one position is reported, never resolved.

**VERIFIED against the live library, 2026-10-05, and this is the strongest
check anything in this repo has had.** `library` ran the first full 0.29.2
refresh and body-hashed all 2,784 extracts against a pre-run snapshot: **3
added (the three newly filed papers), 0 changed, 0 removed.** Zero "claim
position(s) twice" lines, zero records reordered, no sync-conflict files.

That is the real test, not the fixtures. Every multi-attachment record in the
library was a chance for the bulk query to hand back a different order and
rename two papers' extracts onto each other; 2,781 unchanged hashes say it did
not. Note what made the check possible: `library` keeps pre-run body-hash
snapshots in `~/.local/state/library/pre-*`. Without them the only available
evidence would have been "the run finished", which is what every one of this
repo's silent failures also looked like.

### What `library` reported and what it actually was

`library` reported `10.1002_(SICI)1097-4628(19970404)64.pdf` refused with
"found DOIs but none matched the text on page 1", diagnosed as the title
matcher losing a hyphen at a line break.

The title matched at 16/16 overlap with the four-word phrase found.
`stem_conflict` refused it: the stem was compared against the DOI's *suffix*,
on the assumption that a stem is suffix-shaped the way a person names a file
(`387527a0`, `science.1116480`). A browser names it after the whole DOI, so
`10.1002sici...` was searched for inside a string beginning `sici...` and could
never be found. All three files in the inbox were named that way.

Two things worth keeping from it. First, **the refusal named the function's
summary rather than the test that failed** — "none matched the text on page 1"
is what `identify` says when its loop runs out, whatever stopped each
candidate. It now names the stage, and says outright when page 1 was *not* the
problem, because that is the thing a reader otherwise goes and investigates.
Second, the diagnosis came from running the file and printing every
intermediate, not from reading the code. Consistent with item 26: this repo's
defects are found by execution, and its tests encode what their author already
believed.

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
