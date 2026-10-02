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
      the sole supply. `<out>/pdf/` is still the copy that is not behind a
      service, which is the reason it exists — but that is an argument about
      durability, not about availability, and the two were run together here.
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

**So `~/Sync/mendeley` is a frozen snapshot**, as of the last refresh,
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

- [ ] **`zotero_edit.py`** — the direct analogue of `mendeley_edit.py`, keyed by
      citation key through `citekeys.json`, PATCHing `creators` and fields on a
      Zotero item. The groundwork exists: `ZoteroWriter.patch_item` already does
      a version-preconditioned partial merge, and Zotero's PATCH leaves unnamed
      fields alone, which is the property `mendeley_edit.py` had to work for.
      Smaller than item 1 and it unblocks 72 prepared edits today.
- [ ] **A Zotero-backed `mendeley_mirror.py`** — item 1, now the live question
      rather than the contingency it was written as.

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
