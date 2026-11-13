# Getting started

Getting a key, the first run, and doing the same on a second machine.
Credentials never leave the machine they were entered on.

## One-time setup

**1. Make a Zotero API key.** Go to <https://www.zotero.org/settings/keys> and
create one. Read access is all a refresh needs; `zotero_edit.py` and
`zotero_attach.py` need write access, so grant it only if you intend to use
them.

**2. Save it.** `~/.config/offprint/zotero.json`, or `%LOCALAPPDATA%\offprint`
on Windows:

```json
{ "api_key": "...", "user_id": 1234567 }
```

The `user_id` is the **number** shown on that page, not your username. The API
addresses libraries by numeric id and rejects the name, which is a confusing
failure if you guess.

**3. Refresh.** `uv run --script zotero_source.py --refresh --dry-run` first —
it reads and reports and writes nothing. Then drop `--dry-run`.

## Using it from more than one machine

Clone this repo on each machine that needs to *run* the tool: it is plain
cross-platform Python, `run_mirror.sh` is the Linux/macOS launcher, `run_mirror.bat`
the Windows one, and uv handles the dependencies identically on both. If the
mirror directory is synced (Syncthing, Dropbox, whatever), the other machines get
the text, the .bib, and the annotations for free — they need no setup at all, and
no clone, to *read* the mirror.

To *refresh* it from another machine you need a key there: put the same
`zotero.json` in that machine's config directory. Credentials are per-machine on
purpose, so a synced folder never carries them.

Citation keys and extraction state live in `.mirror/` **inside the mirror**, not
in this repo, so they travel with the library rather than with the code. That is what keeps `Muller2020Yield` meaning the same paper
on every machine, and what stops the Linux box from re-downloading two thousand
PDFs to rebuild text that has already synced to it.

Don't run the refresh on two machines at once — you'll get Syncthing conflict
files in `.mirror/`. Keep the scheduled task on one machine and refresh the
others by hand.


## Where your credentials live

Neither in this repo nor in the mirror. The API key and user id go in
`%LOCALAPPDATA%\offprint\zotero.json` (`~/.config/offprint/zotero.json` on Linux
and macOS), so a public clone carries no secrets and a synced mirror does not
hand your credentials to every machine.

`citekeys.json` — in the mirror's `.mirror/`, not with the credentials — is what
keeps citation keys stable: once `Muller2020Yield` points at a document, it keeps
pointing at it, even as the library grows. Don't delete it if you have already
cited these keys somewhere.


## "Zotero refused the key (403)"

The key is wrong, revoked, or lacks the access the command needs. A refresh
needs read; `zotero_edit.py`, `zotero_attach.py`, `zotero_inbox.py`,
`zotero_push.py` and `zotero_delete.py` need write. Issue a new key at
<https://www.zotero.org/settings/keys> rather than editing the old one, and
check that `user_id` is the **number** from that page.

A 403 is reported and the run stops. It is never retried: a rejected key does
not clear by itself, and a tool that kept trying would write `ok` over a
`mirror-status.md` that should say authentication failed.
