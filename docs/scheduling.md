# Keeping it fresh

Running the refresh on a schedule, and the one rule that matters: never two at once.

```{warning}
**The scheduled refresh is retired as of 2026-10-02.** Everything on this page
drives `mendeley_mirror.py` against a Mendeley account that is no longer in use.
The systemd timer is **disabled**, not broken, and re-enabling it as a repair is
the mistake this note exists to prevent — a disabled hourly timer looks like
something somebody forgot.

`zotero_source.py --refresh` has no scheduled form yet and is run by hand. A
mirror that has stopped refreshing can be made to say so: `mendeley_mirror.py
--retire "<reason>"` replaces `mirror-status.md` with a standing notice, because
otherwise the file keeps repeating the last successful run's **ok** for as long
as anyone cares to read it.
```

## Keeping it fresh automatically

Mendeley has no webhooks, so this is polling — but a run with nothing new to do
costs about thirty API calls and no downloads, so polling is cheap.

```
install_schedule.bat            register an hourly refresh
install_schedule.bat remove     unregister it
```

Run it from the clone — `%~dp0` is baked into the registered command, so running
a copy from somewhere else registers that somewhere else. It ends in `pause` for
the double-click case, which means it blocks forever if you script it; redirect
stdin from `NUL` when you do.

That registers a Task Scheduler job running `refresh_quiet.vbs`, which runs the
refresh with `--quiet` in a hidden window: no console flashing over your work, no
prompts, output to `.mirror\mirror.log`. It runs as you, while you are logged on.
Run it on **one machine only** — a second machine polling the same synced folder
produces Syncthing conflict files in `.mirror/`.

Two things make unattended running safe to trust:

- **One run at a time.** A lock file in `.mirror/` stops the hourly job from
  starting on top of a long manual run (or vice versa). A lock older than 12
  hours is assumed dead and ignored.
- **Failures are visible, and say which kind they are.** `mirror-status.md` is
  rewritten on every attempt, success or failure, with the last attempt, the
  last *success*, and the error. A failed run leaves the rest of the folder
  untouched, which would otherwise make a mirror that quietly died three weeks
  ago look perfectly current. If the refresh token is ever revoked, the
  scheduled run exits immediately with an explanation instead of hanging on a
  prompt nobody will answer.

  The status line names the failure: `FAILED: authentication` and
  `FAILED: network` mean opposite things. A dropped connection usually clears by
  itself; a rejected login never does, and the status file says so and points at
  the migration plan rather than leaving it to be waited out. Failing to *reach*
  the token endpoint is a network failure, not a dead login — the two are easy to
  confuse and are deliberately told apart.

  A run that fails is also counted. `.mirror/health.json` holds the current
  streak and the time it began, and the status file prints it, so "failed once"
  and "has not worked in nine days" cannot read the same. One success clears it.

Because the poll is hourly, a paper you add and immediately want to discuss may
not be here yet — Mendeley has to sync it to its own servers first, then the
mirror has to pick it up. Just run `run_mirror.bat` when you know you've added
something.


## Running it on a schedule (when you want it)

```
schtasks /create /tn "Mendeley mirror" /tr "\"%USERPROFILE%\Git\offprint\run_mirror.bat\"" /sc daily /st 06:30
```

Drop `pause` from the .bat first, or the window will sit there waiting for a
keypress after each scheduled run.
