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

---

## The Mendeley setup, retired

Kept because the Mendeley path still exists in the code and a mirror built
before 2026-10-02 was made this way.

**Register an API application.** Go to <https://dev.mendeley.com/myapps.html>
and sign in with your Mendeley account. Give the app any name and enter this
exact redirect URL:

```
http://localhost:8888/callback
```

Click **Generate secret** and copy it *now* — Mendeley will not show it again.
Note the application ID from the list below the form.

**2. Nothing to install.** The script carries a PEP 723 header declaring its one
dependency, so `uv run --script mendeley_mirror.py` builds the environment on the
fly and caches it. `run_mirror.bat` uses uv when it's on PATH and falls back to
the `py` launcher otherwise — it deliberately never invokes bare `python`, since
on Windows that can hit the Microsoft Store stub and hang.

**3. Run it.** Double-click `run_mirror.bat`. It asks once for the application ID
and secret, then opens a browser so you can authorize access to your own library.
After that it runs unattended — the refresh token is reused.

The first run downloads every attached PDF, so give it a few minutes. Later runs
only fetch what changed.


## Using it from more than one machine

Clone this repo on each machine that needs to *run* the tool: it is plain
cross-platform Python, `run_mirror.sh` is the Linux/macOS launcher, `run_mirror.bat`
the Windows one, and uv handles the dependencies identically on both. If the
mirror directory is synced (Syncthing, Dropbox, whatever), the other machines get
the text, the .bib, and the annotations for free — they need no setup at all, and
no clone, to *read* the mirror.

To *refresh* it from another machine you need credentials there: run it once and
enter the same application ID and secret, then authorize in the browser. Tokens
are per-machine on purpose, so a synced folder never carries them.

Citation keys and extraction state live in `.mirror/` **inside the mirror**, not
in this repo, so they travel with the library rather than with the code. That is what keeps `Muller2020Yield` meaning the same paper
on every machine, and what stops the Linux box from re-downloading two thousand
PDFs to rebuild text that has already synced to it.

Don't run the refresh on two machines at once — you'll get Syncthing conflict
files in `.mirror/`. Keep the scheduled task on one machine and refresh the
others by hand.


## Where your credentials live

Neither in this repo nor in the mirror. The app ID, secret, and tokens go in
`%LOCALAPPDATA%\mendeley-mirror\` (`~/.config/mendeley-mirror` on Linux and
macOS), so a public clone carries no secrets and a synced mirror does not hand
your Mendeley credentials to every machine.

`citekeys.json` — in the mirror's `.mirror/`, not with the credentials — is what
keeps citation keys stable: once `Muller2020Yield` points at a document, it keeps
pointing at it, even as the library grows. Don't delete it if you have already
cited these keys somewhere.


## "An invalid request was made by a third-party app / Client authentication failed"

That is Mendeley's page, shown before you ever get the authorize prompt, and its
wording covers several distinct problems. In order of likelihood:

1. **The application ID isn't the numeric one.** On the myapps page each app has
   a short number beside it — that is the `client_id`. The app *name* and the
   *secret* both get pasted here by mistake.
2. **The Redirection URL doesn't match exactly.** `http://localhost:8888/callback`
   registered vs. requested, character for character: no trailing slash, no
   `https`, same port.
3. **The registration was never completed** — the form not submitted, or no
   secret generated.

Run `run_mirror.bat --reconfigure` to re-enter all three. The script now prints
the exact ID and redirect URL it is about to use, so you can compare them against
the myapps page side by side.
