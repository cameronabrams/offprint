# How it is run

The tool and the library it writes are two directories with different lifetimes,
and almost every operational rule here follows from that one fact.

## Which backend

**Zotero, since 2026-10-02.** `zotero_source.py --refresh` rebuilds
`library.bib`, `index.md`, `text/`, `annotations/` and `folders.json` from the
Zotero API, reading attachment bytes from `<out>/pdf/` and going to the network
only on a miss.

`mendeley_mirror.py` is the old refresh and is retired. It is **not** dead code:
it holds every generator — the BibTeX writer, citation-key assignment, text
extraction, the index — and `zotero_source.py` is a reader that feeds them. What
changed at the cutover is where documents come from and where bytes come from,
and nothing else.

The two share `citekeys.json`, which holds a `mendeley:<id>` and a
`zotero:<key>` entry for every citation key. That is why the migration moved
2,739 records without a single key changing, and it is the reason the backend is
passed as a parameter rather than read from a module constant: a Zotero run that
used the constant would namespace Zotero ids as Mendeley ones, match nothing,
and mint a fresh key for every paper.

## The clone is replaceable; the library is not

```
~/Git/offprint     the tool      replaceable — `git pull` is the update mechanism
~/Sync/library            the library   generated, but expensive to regenerate
```

Delete the clone and a fresh one restores the tool with nothing lost. Delete the
library's `.mirror/` and 2,700 PDFs have to be downloaded and re-extracted, and
every citation key is reassigned — which is why the state that decides what a key
means travels *with the library*, not with the code.

| where | what | why there |
|---|---|---|
| the clone | all code | replaceable; nothing generated lives here |
| `<out>/.mirror/` | `citekeys.json`, `state.json`, `removed.tsv`, `pairings.tsv`, `mirror.log` | travels with the library, so every machine agrees what a citation key means and nobody re-extracts everything |
| `~/.config/offprint` (`%LOCALAPPDATA%` on Windows) | the Zotero API key | per-machine on purpose: a public repo and a synced library both stay free of credentials |
| `~/.config/mendeley-mirror` | Mendeley's application ID, secret and tokens | the old location, still read; new credentials go to `offprint` |
| `~/.cache/mendeley-mirror/pdf` | fetched PDFs | outside the library, so pulling one paper's PDF does not sync it to every machine |

**Scripts never locate data relative to themselves.** `DEFAULT_OUT` is an
absolute path and every entry point takes `--out`. That is what lets the clone
sit anywhere while the library sits in a synced folder — and it is why a
`Path(__file__).parent / "text"` would work in testing and break for everyone
whose clone is not inside their library.

## Exactly one refresh at a time

The refresh is the one operation with a hard concurrency rule: **two at once make
sync-conflict files out of `.mirror/`**, and a conflicted `citekeys.json` is a
library whose keys no longer agree between machines.

- The hourly schedule belongs to one machine. Which one is a per-machine fact
  that has already changed once here, so check (`systemctl --user list-timers`,
  or Task Scheduler on Windows) rather than assuming.
- Where the schedule is a systemd timer, refresh on demand by starting its
  service — `systemctl --user start mendeley-mirror.service` — which cannot run
  twice at once. Calling `run_mirror.sh` directly can overlap it.
- A long manual run, such as an OCR pass over many scans, should stop the timer
  first and restart it from a trap, so a failure cannot leave the refresh
  switched off.

Since version `9246b05` the tool no longer *tells* you to do the unsafe thing:
`inbox.py` works out whether the systemd unit exists and prints the command that
is safe on the machine you are actually on.

## One direction, and eight deliberate exceptions

A refresh is strictly one-way, the service to disk. A bad run can lose mirrored
files but cannot touch the library, which is what makes it safe to run
unattended.

Eight scripts break that on purpose, five of them live:

| script | account | what it does |
|---|---|---|
| `zotero_source.py --refresh` | Zotero | reads only; listed because it is the one that rewrites the mirror |
| `zotero_edit.py` | Zotero | corrects fields on a record that already exists |
| `zotero_attach.py` | Zotero | uploads PDFs into attachment slots that already exist |
| `zotero_inbox.py` | Zotero | **creates an attachment**, and since 0.29.0 **the record under it** when none exists, from a PDF you downloaded |
| `zotero_push.py` | Zotero | **creates a record**, from a DOI or an arXiv id |
| `zotero_delete.py` | Zotero | **removes** a record or a byte-less attachment |
| `inbox.py`, `mendeley_push.py`, `mendeley_edit.py` | Mendeley | the same three jobs, retired with the account on 2026-10-02 |

**Two deserve the most care, for opposite reasons.** `zotero_edit.py` is the
only one that can *destroy correct metadata* rather than merely add wrong
metadata — see [Correcting a reference](corrections.md). `zotero_delete.py` is
the only one with **no undo at all**: it names what *survives* as well as what
goes, refuses to delete a record with no surviving sibling unless told to, and
backs up every item and its children before sending anything.

`zotero_inbox.py` and `zotero_push.py` are the only scripts that **create**
anything. Until 0.29.0 each created exactly one kind of thing and the inbox
would not create a record, on the reasoning that running the two together is
how a library acquires a second record for a paper it already had.

That reasoning was sound and the mechanism behind it was not. The duplicate
check read `library.bib`, which only knows what the last refresh saw — so a
record pushed an hour ago was invisible to it, and the separation did not
actually prevent the duplicate it was named for. What it did cost was two full
refreshes per new paper, one to mint the citation key and one to extract the
text, which on this library is about 26 minutes.

0.29.0 closes the hole properly by asking **Zotero itself** by DOI before
creating anything — which is what the Mendeley-era `inbox.py` always did — and
lets the inbox create the record once that check has passed. It still will not
create a record it cannot identify by DOI: a title match is enough to attach a
PDF to a record a human already made, and not enough to mint one, because the
metadata would be guesswork. `--no-create` restores the old refusal.

The three Mendeley scripts were interactive by default. `--dry-run` was the safe
thing to run and the right thing to show someone before a batch; `--yes` was for
a run already approved, not a way past a prompt in a non-interactive shell.

**Every `zotero_*` writer inverts that: a dry run is the default and performs no
write of any kind.** Asking per file across 2,740 of them is not a safeguard, it
is a way of training someone to hold down a key. `--yes` is the whole approval,
so it is
given once, deliberately, to a run whose dry run has been read. `--limit` and
`--key` exist to make a first live run small.

## When a Claude session operates it

This repository ships two files aimed at an agent rather than a person:
`CLAUDE.md` in the clone, and one in the library that a refresh never overwrites.
They are deliberately different documents, because the two jobs are different:

- **The clone's `CLAUDE.md` is about working on the tool** — the structural rule,
  which outputs are contracts that other people's files depend on, and the fact
  that a session working here does not run the writing scripts against a live
  account and does not start a refresh.
- **The library's `CLAUDE.md` is about using it** — how to search before
  concluding something is absent, how to read an extract, how to derive a page
  offset before quoting a page number, and the rule that anything written to the
  account needs the library owner's say-so.

Keeping them apart is not tidiness. When one document described both jobs, the
operator's half went stale without anyone noticing: it named a refresh schedule
that had moved three weeks earlier, and a session following it would have run
the command that can collide with the timer. **One reader per document, and one
copy of each fact** — a second copy is a second thing to keep true.

If you are running this yourself rather than through an agent, neither file is
required reading; everything a person needs is in these pages.

## Filing one new paper

Since 0.29.0 this is two commands rather than four, and one short refresh
rather than two long ones:

```
uv run --script zotero_inbox.py --yes
uv run --script zotero_source.py --refresh --only Srinivasan1997Amorphous
```

The first prints the exact second command, naming the records it filed. A
record it has just created has no citation key yet, so it prints the Zotero
item key instead; `--only` takes either.

`--only` narrows the **extraction** and nothing else. `library.bib`, `index.md`
and `folders.json` are still rebuilt in full, because they are regenerated from
the whole library every time — a "targeted" run that narrowed them would
quietly drop every record it did not name. It also leaves `mirror-status.md`
untouched, since that file records when the mirror as a whole was last
verified and a run that looked at one record cannot speak for it.

**`--only` will not make the refresh noticeably faster.** Measured on this
library: 199.4 s against 203.1 s for the full run. Extraction is a tenth of a
second of that, because an attachment whose contents have not changed is
skipped before anything is read. The time is in asking Zotero for the library,
and a targeted run still has to do that. Reach for `--only` when the re-read
itself is expensive — `--ocr`, or `--reassess` after an extraction-rules
change — not to save a minute on an ordinary refresh.

A full refresh took 13.3 minutes before 0.29.0 and takes about two now: the
attachment list is one query rather than one per record, and collection
membership is read off the records instead of walking each collection.

## Getting every attachment onto disk

`--attachments keep` writes a PDF **only for attachments it actually processes**.
On a library that is already mirrored that is almost none of them: the skip
fires for every attachment whose extract exists and whose filehash is unchanged,
and it returns before any download. A keep run over a finished mirror therefore
downloads nothing, archives nothing, and reports a successful run — which is
correct for a refresh and useless as an evacuation.

To pull the files themselves down:

```
uv run --script mendeley_mirror.py --attachments keep --backfill
```

`--backfill` fetches a skipped attachment's file when it is not already in
`<out>/pdf/`, under its own extension — a `.cif` stays a `.cif` — and changes
nothing else: no extraction, no state write, no report row. It is resumable by
construction, because a file already on disk is never fetched again, so an
interrupted run is finished by re-running it. Anything it could not fetch is
named in the log rather than counted, since a file missed during an evacuation
is a file left behind.

Expect this to take a while and to be large: roughly 1.5–2.5 MB per attachment.
If `<out>` is inside a synced folder, decide *before* running it whether the
other machines should carry the files — a Syncthing `.stignore` line naming the
`pdf/` directory keeps them on one host.
