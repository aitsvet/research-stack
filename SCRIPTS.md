# Research stack — script catalogue

Which script does what, and the traps each one carries. The standing rules and how to start the
stack are in `AGENTS.md`; getting a source out of a hostile host is `ROUTES.md`; architecture and
Zotero/MCP troubleshooting are in `SETUP.md`.

## Worldwide discovery + acquisition pipeline

Run from the repo root with `.env` sourced. Each script takes Zotero `collectionKey`s as positional args; defaults to writing under `${DISCOVERY_OUT:-~/research-stack/.discovery}`.

The pipeline is **two-pass on purpose**: pass 1 (`discover.py`) creates lightweight candidates with abstracts and OA status but **never attaches PDFs**; the user picks rows; pass 2 (`acquire.py` + `retry_unpaywall.py`) fetches OA full text only for selected items. Bulk-acquiring during discovery wastes bandwidth and drags in HTML/paywall noise that has to be re-cleaned.

```
                   discover.py topics.json
                            │
                ┌───────────┴───────────┐
                ▼                       ▼
        dump_abstracts.py        (skim tables to pick rows)
                                        │
                                        ▼
                                 promote_rows.py   target  source  table.md  rows
                                        │
                                        ▼
                                  acquire.py target   →   retry_unpaywall.py log.json
                                        │
                                        ▼
                                 extract_texts.py target
                                        │
                                        ▼
                              zotero_attach_text.sh per-paper notes
```

| Script | Purpose |
|---|---|
| `discover.py <topics.json>` | OpenAlex + arXiv search per topic; creates lightweight Zotero items + per-topic candidate table. |
| `discover.py --dois <dois.json> <selected_key>` | DOI-list ingestion: pulls Crossref+OpenAlex metadata for each DOI, creates the item, adds to `selected`, attaches OA PDF if available. Use when you already know which papers to cite (e.g. bootstrapping a collection from a draft's bibliography). |
| `dump_abstracts.py <key>...` | Dump itemKey+title+abstract for every item in a collection to one markdown file per key. |
| `promote_rows.py <tgt> <src> <table.md> <rows>` | Map rows of a discovery table → Zotero itemKeys → add to target collection. |
| `add_to_collection.py <key> <itemKey>...` | Idempotent batch add of explicit itemKeys to a collection. Accepts `--stdin`. |
| `acquire.py <key>` | Fetch PDFs via `import_attachment_url` for items whose `extra` has `OA URL:`; writes acquire log. |
| `retry_unpaywall.py <log.json>` | For failed-acquire rows, walk Unpaywall's `oa_locations` until one returns a real PDF. |
| `extract_texts.py <key>` | `pdftotext` (or `.zotero-ft-cache` fallback) every attached PDF in a collection → `.txt` per item. Preserves any pre-existing `<key>.txt` when no new content was produced (e.g. you ran an out-of-band `.docx` extractor first). |
| `verify_refs.py <draft.md> [--per-line]` | Scan a draft for DOIs and compare each against Crossref: flag AUTHORS / TITLE / VENUE / NOTFOUND mismatches. Catches the "right DOI, wrong author" failure mode that LLM-generated bibliographies exhibit routinely. Author matching is diacritic-folded. Default mode = char-window around each DOI (DOIs in prose); pass `--per-line` for a formatted numbered reference list (one ref per line, each ending in its own DOI) — the window bleeds across neighbours otherwise. |
| `check_citations.py <draft.md> [...]` | Gate numbered bibliographies mechanically: dangling/uncited numbers, gaps, duplicates, and numeric citation ranges. |
| `resolve_dois.py wanted.json --out-dir DIR` | Resolve a title/author/year wanted list through Crossref with explicit overlap, author, and year gates; uncertain candidates stay unresolved. |
| `flow_counts.py [--discovery <dir>] [<top_key> [<selected_key> [<needs_key>]]]` | Print PRISMA-style flow numbers (candidates → unique-selected → with-text → needs-manual-access) computed from `triage.json` + text manifest + Zotero collection sizes. Run before any paper that claims a "screened/included/depth-read" count. |
| `zotero_attach_text.sh <item> <file>` | Stream a local text/markdown file into a Zotero child note without round-tripping through the model. |
| `extract_texts.py md <pdf\|dir> [--out D]` | Pure-python PDF→Markdown via pymupdf4llm (no GPU/OCR), marker-compatible `{N}`+48-dash pagination. Reads the embedded text layer, so it's a clean reference for diffing/patching marker output (catches OCR homoglyphs marker invents). Needs `pip install -r requirements.txt` (pymupdf4llm). |
| `patch_marker.py <marker.md> <ref.txt> --inplace` | Reconcile marker OCR errors against a pymupdf4llm reference, structure-preserving: Latin↔Cyrillic homoglyph repair (mixed-script→trust ref; pure-script flips restore Latin with a Roman-numeral guard), folded-ratio OCR fixes; skips digit/ID tokens, case-only diffs, ref-side fi/fl-ligature loss. Inserts/heading-demotion opt-in (noisy). |
| `chandra_ocr.py <pdf> [--out F] [--concurrency N]` | Full-document OCR of a scanned PDF via a vLLM-hosted Chandra model → paginated Markdown (`{N}`+48 dashes). For PDFs with no text layer, where `extract_texts.py md` yields nothing. Needs the endpoint up (`CHANDRA_URL`, default `http://localhost:8000/v1`). A page is decode-bound (~minutes) while vLLM batches happily, so `--concurrency 6` turns a multi-hour scan into a manageable one; leave it at 1 on an endpoint someone else is using. **If the model is reached over an SSH tunnel, give the address explicitly** — `ssh -L` binds `[::1]` only, Python's `getaddrinfo` returns `127.0.0.1` first, and whatever else holds that IPv4 port answers instead (a bare 404 that looks like a bad route). **A long run can lose a contiguous tail** — the endpoint drops the connection and the last pages come back as `*[OCR FAILED FOR THIS PAGE]*` while the run still exits 0. Grep for the marker, extract just those pages into a temp PDF (`fitz.Document.insert_pdf(src, from_page=, to_page=)`), re-run on it and splice the result back by `{N}` marker; a full re-run costs hours for nothing. |
| `plantuml` (compose service) | `.puml` → PNG/SVG for paper figures. Java + Graphviz stay off the host: `docker compose --profile tools run --rm plantuml -tpng <path>.puml`. `WORK` points at the tree holding the diagrams (default `../diagrams`); output lands beside the source owned by the host user, and `-charset UTF-8` is baked into the entrypoint. **The version is pinned to 1.2024.7 on purpose** — see the rendering gotcha below. |
| `convert_office.py <file\|dir> [--dry-run] [--force]` | `.rtf/.doc/.docx/.odt` → Markdown via LibreOffice headless (HTML export → `html_md`), so tables and headings survive where a plain-text export would flatten them. **Runs in the `docconv` compose service** — LibreOffice is deliberately not installed on the host: `docker compose --profile tools run --rm docconv /corpus/<subdir>`. Skips documents that already have a `.md` unless `--force`. |
| `extract_tables.py md\|pdf <path> [--out F] [--pages A-B] [--min-rows N] [--grep RE]` | Pull tables out of a corpus into JSON so matrices stay data instead of prose. `md` reads pipe tables (survive only from Word sources) and anchors each to its nearest heading and `{N}` page mark; `pdf` uses PyMuPDF's geometry-based table finder — **use it whenever the markdown lost the structure**, because a PDF table degrades into flowing text where values drift away from their rows and get mis-paired on sight. Run both and compare when in doubt. Watch two artefacts: a footnote marker glues onto its value (`13` + note `46` → `1346`), and a row split by a page break arrives with an empty first cell. Method: `READING.md`. |
| `html_md.py` | HTML→Markdown used by both `chandra_ocr.py` and `convert_office.py`. Pure bs4 (no PyMuPDF) so it imports inside the LibreOffice container. Collapses HTML source wrapping while keeping real `<br>` breaks, and merges touching emphasis runs that word processors emit. |
| `fetch_meganorm.py <slug> <out-basename>` | Fetch a national standard from a standards mirror: writes `<out>.md` from the document page (always present) and `<out>.pdf` if the scanned official edition is linked. Walks the unguessable directory segment; sends a `Referer` so the PDF doesn't 403. Resolve the slug with a web search — the site's own search 404s. |
| `fetch_pdf.sh <url> <outdir> <base>` | Render a URL→PDF via the container chromium (images off by default) → md. Captures SSL-broken / anti-bot pages WebFetch can't; constraints in `ROUTES.md`. For a visual document that must keep its images (e.g. a local notes page), set `FETCH_PDF_IMAGES=1 FETCH_PDF_NO_MD=1` to skip the image-strip and the md-extraction follow-up. `url` may be `file://` or a local `http://127.0.0.1:<port>/...` (container shares host network) as well as a public URL. |
| `md_pdf.sh <file.md\|dir> [out.pdf]` | Markdown (with relative image links) → PDF in one call, printed by the container chromium — nothing installed on the host. A dir concatenates its `*.md` (sorted) into one PDF, page break between documents. Wraps `fetch_pdf.sh` (images on, no md follow-up), serving the md's directory on `127.0.0.1:${MD_PDF_PORT:-8377}` for the print. |
| `ingest_sources.py <manifest.json> --collection K --pdfdir D --notesdir D --state F [--serve-ip IP] [--only ID...]` | Ingest an explicit MIXED list of *known* sources into a collection + emit one paginated-md note per item. Kinds: `arxiv` (id→Atom metadata+pdf), `pdfurl`, `web` (via `fetch_pdf.sh`), `localpdf` (via `import_local_files`, needs `--serve-ip`), `meta` (metadata-only). Create-or-update via `item_key`, `replace_notes`, note 413→md-only fallback, resumable via state. **This is the reusable form of per-project one-shot ingest — don't write a new bespoke script.** Use when you already know the exact sources; use `discover.py` instead for search-based discovery / DOI bootstrap. Manifest (the project-specific data) lives in the *project* repo. |
| `zotero_mcp.py` | Canonical MCP JSON-RPC client (session envelope, throttle, 429 back-off) + `result_json` / `item_key` / `add_note_file` / `import_file` / `import_local_files` helpers. **Import this** in new scripts instead of re-rolling the client — every script here does. |

## Paper ↔ discovery folder convention

Every English-language paper draft pairs with **one Zotero top collection** and **one `discovery/<slug>/` folder** in the parent repo. The Zotero top has two subcollections, `selected/` and `needs-manual-access/`. The discovery folder mirrors them:

```
<repo>/discovery/<paper-slug>/
├── triage.json                          # output of the manual triage step
├── coverage.md                          # narrative pre-write referencing itemKeys
├── topic_<id>.md                        # one per discover.py slice (search mode only)
├── text/<itemKey>.txt                   # one extracted text per selected item
└── text_manifest_<selected_key>.json    # extract_texts.py output
```

`discover.py`, `extract_texts.py`, and friends all honour `DISCOVERY_OUT=<repo>/discovery/<paper-slug>` and write into the right tree. New papers: create the Zotero collection + the discovery folder together; bootstrap from a DOI list with `discover.py --dois`.

## Per-tool gotchas

- **MCP protocol plumbing lives in `zotero_mcp.MCP`** — session envelope (`Mcp-Session-Id` + `notifications/initialized`, one session reusable across hundreds of calls), per-call throttle (~150 ms floor; faster bursts trip rate limits), and 429 back-off honouring `Retry-After` (≥1.3 s between bulk-import calls, exponential to ~40 s). Don't re-roll any of it.
- **`extract_texts.py` storage path + `pdftotext`.** The default `ZOTERO_STORAGE` is `~/research-stack/config/Zotero/storage`; set the env var explicitly for any other layout — a wrong path yields "NO PDF FOUND" for every item. `pdftotext` (poppler) is optional: the script falls back to pymupdf (already a requirement) when the binary is absent.
- **Refining a noisy topic is a two-pass norm.** Scan the top 10 hits; if noisy, grep itemKeys from the `get_collection_items` dump, `batch_trash` them (≤100 per call), then re-run `discover.py` with a tightened query. Cheaper than over-engineering the query upfront.
- **AI-generated bibliographies hallucinate authors at correctly-formed DOIs.** A significant fraction of LLM-generated citations resolve to a real paper at the cited DOI but with completely different authors / venue / title. The DOI looks valid because it *is* valid — just for a different paper than the prose claims. Always run `verify_refs.py` before submission; treat any AUTHORS / TITLE / VENUE finding as blocking. For a formatted numbered reference list use `--per-line`, format each entry `Authors. Title // *Full Journal Name* doi:...` (ISO-690 ` // ` splits title from venue; use the unabbreviated journal name), and note that a shortened title (subtitle dropped) shows as a low-overlap TITLE flag — complete it from the Crossref title rather than assume a wrong-paper.
- **PRISMA-flow numbers in a draft must match the artefacts.** Triage gross-pick counts and unique-included counts routinely diverge by the slice-overlap factor; a "deep-read" claim that's not recorded as a Zotero tag or a manifest field is almost certainly drift. Run `flow_counts.py --discovery discovery/<paper>` and copy its output verbatim into the paper's PRISMA section.
- **PlantUML: pin the version, or every figure silently redraws.** Debian's packaged plantuml is 1.2020.02; rendering an *untouched* source with it returned a PNG 18 % smaller with different spacing, so regenerating one edited diagram would restyle every other figure in the same commit — a change nobody made and nobody reviewed. The image pins 1.2024.7, the version the existing corpus was rendered with. Proof that a renderer matches the corpus is not "it looks right": re-render an unchanged source and compare. Byte-equality is too strict (PNG carries a compressed metadata blob and antialiasing drifts with the font stack) — compare IDAT pixels instead. In a synthetic reproduction the pinned image matched a reference `architecture.png` at identical dimensions with 8 differing pixels out of 738 779 (max channel delta 7); the Debian build failed that test outright. Bump the pin only deliberately, and re-render every diagram in one pass.
- **marker (GPU) vs text-layer (pymupdf4llm).** marker OCR invents Latin↔Cyrillic homoglyphs (a Latin acronym rendered as its Cyrillic look-alike, e.g. `ABC→АВС`) and over-tags headings; the text layer is right for those but loses fi/fl ligatures (`finance→nance`) and garbles multi-line/column titles. So neither dominates — `patch_marker.py` trusts the reference only for safe, verifiable corrections. For web sources, `fetch_pdf.sh` (container chromium) → pymupdf4llm gives clean pdf+md in the same `{N}` format, no patching needed.
