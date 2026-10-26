# Claims

What has actually been concluded from this library, what each conclusion rests on, and what breaks if a paper turns out to be wrong.

## The question no citation graph answers

`citemap.py` records which papers cite which. That is a graph of ~13,600 internal
edges, and it cannot tell you what anyone *concluded*. In-degree measures
popularity among the papers that happen to have been collected: a methods paper
everything cites may carry nothing substantive, while a paper cited once can be
the sole support for three conclusions.

`whatbreaks.py` reads a ledger of conclusions instead.

```
uv run --script whatbreaks.py Snow1991Dilatometry
uv run --script whatbreaks.py --thin
uv run --script whatbreaks.py --orphans
uv run --script whatbreaks.py --check --titles
uv run --script whatbreaks.py --render
```

The ledger is `<out>/claims.json`, beside `library.bib` and `findings/`. Each
entry is a statement, a status, and a list of sources — each source carrying the
**role** it plays:

| role | meaning |
|---|---|
| `sole` | the only source; if it is wrong, the claim is gone |
| `primary` | carries the claim, with others corroborating |
| `corroborating` | agrees, and the claim would survive it weakened |
| `contradicting` | disagrees with the claim as stated |
| `method` | supplies a technique, not a result |
| `context` | background; supports nothing on its own |

Roles are the part that has to be read off a paper by someone who read it. That
is why the ledger is not generated and never will be.

## The tool is here; the data is not

`claims.json` lives in the mirror and belongs to whoever maintains the library.
It changes when a paper changes someone's mind, not when this repo cuts a
release. The same split as `findings/`: the tool is code, the content is
judgment, and a release must never carry one as if it were the other.

**Role weights live in the ledger too**, in its own `weights` object. How much a
`primary` carries relative to a `corroborating` is a judgment about one
library's literature, and leaving it in the tool would mean a release to change
it. A run that falls back to the tool's own table says so in its output, and
`--render` marks it in the rendered file — scoring with a table the ledger did
not declare is exactly the quiet substitution the rest of this page is about.

## A count says what it is a count of

Three of this tool's modes print a number that is smaller than the truth unless
it is made to say otherwise, and one of them shipped that way.

- **`--orphans` reported 2 when 4 claims rested on nothing.** It listed claims
  with an empty `sources` list. Two more carried a single `context` cite apiece,
  which is resting on nothing in exactly the same way, and they were silently
  absent. The mode now reports both groups, separately and both.
- **Ranked listings print a head.** `--load` shows the top 25 of however many
  papers there are, and says so; `--all` prints every one. `--render` says the
  same in the rendered table.
- **"What would be left: NOTHING"** means nothing *evidential* is left. Where a
  `method` or `context` link survives, the line counts it, because a reader who
  opens the ledger and finds links the report did not mention has to work out
  which of the two was lying.

## What the tool can check, and what it cannot

`--check` proves every citekey in the ledger resolves in `library.bib`. **It
does not prove the key is the paper meant** — a typo that lands on another real
key passes. `--titles` prints each linked key with its title and year, which is
how the first 91 were verified, by a person reading them. `--link` prints the
title at write time for the same reason, when the person still remembers which
paper they meant.

Everything else is structural, and it runs on *every* mode rather than only
under `--check`: statuses and roles must be ones the ledger itself documents,
ids must be unique, every source must carry `k`/`role`/`why`. Hand-editing the
JSON is a supported way to add a claim, so drift is the normal case and not an
exceptional one — which is why a drifted ledger is refused with every problem
listed at once, not the first.

## Adding a claim

```
uv run --script whatbreaks.py --add ce-tg-ranking --status open \
    --statement "..." --note dicyanate-tg-by-bridge
uv run --script whatbreaks.py --link ce-tg-ranking --key FANG1995Polycyanate \
    --role primary --why "Fig 22 p.95, the only single source covering most bridges"
```

A write is atomic, preserves the file's mode, and re-serializes in the ledger's
own formatting, so a one-field edit is a one-field diff. Editing the JSON by
hand remains fine.

**A statement is never rewritten in place.** The sources on a claim vouch for
the words that were there when they were linked, so changing the words under a
fixed id silently moves what they vouched for. The ledger's own status
vocabulary names the right move — `superseded`, "replaced by a later claim" — so
restating is `--set-status` plus a fresh `--add`. The tool will not do it in one
step and call it an edit.
