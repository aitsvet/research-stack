# QA over this corpus — build the index, answer from it

You are on a machine that received `texts/` only. There is no database and no
tooling here: build the index yourself, then answer questions. This file is the
whole contract — the chunking numbers in it are not stylistic, they were measured
on this corpus and changing them changes the hit order.

## What is in `texts/`

{docs} markdown files, {chars} characters, one per book:

| file | role |
|---|---|
| `{primary}_*.md` | **the base title** — {title} |
| the other three | support only: they may widen context, never carry the answer |

Each file is paginated: a page starts with a line `{N}` followed by 48 dashes,
N counting PDF pages from 0. Every citation you produce is a page from here.
Filenames start with the catalogue key, so a hit's key is
`basename.split("_")[0]`.

## Chunking (the only part worth being fussy about)

Chunk structurally, by paragraph, never by characters:

1. Split the file on the page mark — a line matching `^\{\d+\}-{48}$` — into pages, keeping N.
2. On each page, strip Arabic diacritics and tatweel:
   `re.sub("[\u0640\u064b-\u065f\u0670\u06d6-\u06ed]", "", body)`.
   A vocalised page otherwise contributes hundreds of one-letter tokens that
   drown the Russian text of a bilingual edition.
3. Split the page body on blank lines (`\n\s*\n`) into paragraphs; inside a
   paragraph collapse runs of spaces.
4. A paragraph shorter than **60** characters is a heading, a caption or a folio
   — do not drop it: hold it and prepend it to the next paragraph, so chapter
   titles stay searchable ("Отношение Пророка к злословию" is the heading of the
   chapter that answers it).
5. Merge consecutive paragraphs while the chunk stays **≤ 900** characters.
   A paragraph longer than that becomes a chunk of its own.
6. **Never merge across a page boundary.** The page is the citation unit; a chunk
   spanning two pages cannot be cited honestly.
7. Store `(path, page, seq, chars, text)` per chunk. On this corpus that yields
   ≈ {chunks} chunks; if your count is far from that, the paragraph rules
   above are not being followed (usually: splitting on single newlines, or
   dropping short paragraphs).

## Index

SQLite, stdlib only:

```sql
create table chunks(id integer primary key, path text, page int, seq int,
                    chars int, text text);
create virtual table chunks_fts using fts5(
  text, path unindexed, page unindexed, cid unindexed,
  tokenize = "unicode61 remove_diacritics 2");
insert into chunks_fts(rowid, text, path, page, cid)
  values (?, ?, ?, ?, ?);   -- rowid = chunks.id
```

FTS5 is trivial; what makes it work is the chunking above plus the query rules
below. Rebuild when a file changes: key documents by sha256, re-chunk only what
differs.

## Query

1. Extract word tokens from the question (`\w+`, Unicode). Quote each and join
   with **OR** — never with the implicit AND: "что сказано о терпении" AND-matches
   nothing. Poor man's stemming for Russian: for tokens of 6+ characters use a
   prefix query, `"милос"*`.
2. Retrieve a **wide pool**: `max(6 * k, 120)` candidates by `bm25(chunks_fts)`
   (lower is better; negate for readability).
3. **Separate the base title before slicing.** This is the failure mode that
   costs the most: globally, long thematic paragraphs from the other books fill
   the top of the list, so filtering to the base title *after* taking the top-k
   silently leaves it empty. Split the pool by key, take up to `k - support` from
   the base title, then at most `support = 2` chunks from the others.
   If the base title contributed nothing, say so and answer from support only —
   do not present another book as the source.
4. **Deduplicate by `(path, page)`** — neighbouring paragraphs of the same page
   otherwise occupy the whole answer.
5. Optional precision, if the machine can afford it: re-score the top ~20
   candidates with a stronger embedding model (`paraphrase-multilingual-mpnet-base-v2`,
   `intfloat/multilingual-e5-large`). Measured on 2 CPU cores: mpnet ≈ 1.2
   chunks/s, e5-large ≈ 0.2 chunks/s, so 10–20 candidates is 10–100 s per
   question. Rebuild-only alternative: embed everything with MiniLM
   (≈ 13 chunks/s) — a one-off ~9 min for these four books.

## Answer

- Cite as `path#page` in the prose: *{title}, с. N* — N is the `{N}` page of the
  markdown, not a printed folio; check the book's front matter before equating
  them.
- Quote verbatim, mark elisions `[…]`, one short paragraph of context.
- If the chunks do not contain the answer, say so and offer the nearest passages.
  Never complete from memory, never invent a page.

## The question arrives as a screenshot

1. Capture the region (Wayland: `grim -g "X,Y WxH" out.png`; X11: `maim -g
   WxH+X+Y` or `scrot` + crop; any box: ImageMagick `import -window root`).
2. OCR with **tesseract**: `tesseract out.png stdout -l rus+eng --psm 6`.
   About a second, local, and exact on clean on-screen text.
3. Feed the OCR text as the query, verbatim (collapse whitespace, cap ~400 chars).
4. Show the answer, not the raw hit list: the chunk, the citation, one line of
   context.

Use a VLM OCR (chandra, `CHANDRA_URL`) only when the region is a photo of a page
or Arabic script and tesseract visibly garbles it — a VLM sometimes substitutes a
look-alike word, and here the OCR text *is* the question, so that silently
changes what is being asked.

## Acceptance tests

Run these before answering anything:

| check | expected |
|---|---|
| page marks per file | {pagecounts}; fewer marks than that means pages were lost |
| `{selftest_query}` | a hit in `{primary}` around page {selftest_page}; if the base title yields nothing and a support book answers, the ranking split is wrong |
| a nonsense query ("квантовый тостер") | the "base title has no chunk" notice, not a confident support answer |
| any answer | every paragraph carries a `path#page` you could open in `texts/` |

## Pitfalls this file exists for

- AND instead of OR in FTS → empty results on natural questions.
- Taking top-k globally, then filtering to the base title → base title silently
  absent; widen the pool first.
- Character-window chunking instead of paragraph chunking → citations point at
  arbitrary text, headings disappear, answers read like fragments.
- Chunks spanning pages → every citation is off by one.
- Keeping Arabic harakat → token noise, the Russian text never ranks.
- No page dedupe → five near-identical paragraphs from one page.
- Trusting OCR of a photo without checking it against the retrieved chunk.
