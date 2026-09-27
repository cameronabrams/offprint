# How it is run

The tool and the library it writes are two directories with different lifetimes,
and almost every operational rule here follows from that one fact.

## The clone is replaceable; the library is not

```
~/Git/offprint     the tool      replaceable — `git pull` is the update mechanism
~/Sync/mendeley           the library   generated, but expensive to regenerate
```

Delete the clone and a fresh one restores the tool with nothing lost. Delete the
library's `.mirror/` and 2,700 PDFs have to be downloaded and re-extracted, and
every citation key is reassigned — which is why the state that decides what a key
means travels *with the library*, not with the code.

| where | what | why there |
|---|---|---|
| the clone | all code | replaceable; nothing generated lives here |
| `<out>/.mirror/` | `citekeys.json`, `state.json`, `mirror.log` | travels with the library, so every machine agrees what a citation key means and nobody re-extracts everything |
| `~/.config/mendeley-mirror` (`%LOCALAPPDATA%` on Windows) | application ID, secret, tokens | per-machine on purpose: a public repo and a synced library both stay free of credentials |
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

## One direction, and three deliberate exceptions

A refresh is strictly one-way, Mendeley to disk. A bad run can lose mirrored
files but cannot touch the library, which is what makes it safe to run
unattended on a schedule.

Three scripts break that on purpose, and all three are interactive by default:

| script | what it does to your account |
|---|---|
| `inbox.py` | attaches a PDF to a reference, creating the reference from its DOI if it is new |
| `mendeley_push.py` | adds one reference, from an arXiv ID or a DOI |
| `mendeley_edit.py` | corrects fields on a reference that already exists |

`--dry-run` is the safe thing to run and the right thing to show someone before
a batch. `--yes` is for a run that has already been approved — not a way past a
prompt in a non-interactive shell.

`mendeley_edit.py` deserves the most care, because it is the only one that can
*destroy* correct metadata rather than merely add wrong metadata. See
[Correcting a reference](corrections.md) for the three properties that keep that
from happening by accident.

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
