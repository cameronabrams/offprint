# Findings

A record of what was asked of a paper and where the answer sat — the one hand-written directory in an otherwise generated mirror.

## Findings: what was asked, and where the answer was

The mirror preserves what a paper *says*. `finding.py` preserves what was *asked* of
it, so a question answered once is not re-derived from nothing six months later.

```
uv run --script finding.py --key Jo2007Automated \
    --question "does it optimize the protein's embedding?" \
    --page 2 --asked-by pestifer-manuscript \
    --quote "one should align it in a local machine and then upload it"
```

Records land in `findings/<citekey>.md`, one file per paper, **append-only**. A
finding later found wrong gets a `RETRACTED` record pointing at it and the original
stays, so how long a wrong claim stood is visible rather than tidied away. A
`CHECKED` record is likewise appended, carrying who checked and when — which makes
"what fraction of these has anyone verified?" a question with an answer.

Two properties do the real work:

- **The quote is verified before the record is written.** It must actually appear in
  `text/<citekey>.md` under the marker page claimed for it, after Unicode and
  whitespace normalization. A quote that cannot be found is refused, not filed with a
  warning. If it turns up on a different page, the error says which.
- **The locator is the string the journal prints.** Not every journal paginates by
  page: Phys. Rev. X prints `021039-2` on the second page of article 021039, and a
  topical review in J. Phys. Condens. Matter prints `R863`. `2` and `863` are not
  places a reader can look up, so `--journal-page` takes the folio whole, the
  derivation carries the printed form into the record, and a bare number supplied
  for a journal that prints a folio is completed to it — out loud. An unrecognized
  form is refused by name rather than stripped down to its digits, which would read
  the page range `12-15` as page 15.
- **The record stores the extract's characters, not the submitted ones.** The quote
  check normalizes en dashes, typographic quotes and ligatures on both sides, so a
  quote can pass it and still differ from the page. What goes into the append-only
  file is the extract's own wording, and the run says when it substituted. Where the
  PDF broke the line is not preserved: that is a fact about the PDF, not the sentence.
- **The page offset is derived, never trusted.** `finding.py` reads the recurring
  page number out of each page's running head or footer and takes the offset a
  majority of pages agree on. On fourteen papers whose offsets had been worked out by
  hand it got thirteen right and refused the fourteenth — a two-page note with no
  usable footer. It has never returned a wrong offset, which is the property that
  matters: a refusal costs a minute, a wrong locator ships.

## Re-checking what is already written

```
uv run --script finding.py --replay              # every record, every file
uv run --script finding.py --replay --key Lin1999Effect
uv run --script finding.py --replay --all        # including wording-only differences
```

`--replay` re-runs every record in `findings/` against its extract and **writes
nothing**. It passes each record's own stored folio back to the tool, which is what
makes a refusal mean something: a record and the tool disagreeing about the printed
page is a claim one of them is wrong, and over the first real corpus that found five
durable records carrying a wrong journal page. Two of those needed no tool at all —
a sibling record in the same file, on the same marker page, already held the right
answer.

It exits 1 when anything disagrees or cannot be read, so a sweep can be scripted.
Records the library has already retracted or checked by hand are marked and still
reported, because the tool disagreeing with a person is more interesting rather than
less. **Nothing is repaired:** these files are append-only, so the fix is a
`RETRACTED` record written by whoever owns the library.

**A record locates a passage; it never substitutes for one.** Consulted instead of
the extract it becomes a paraphrase indistinguishable from the source, which is what
deterministic extraction exists to prevent. Every file says so in its own header.
