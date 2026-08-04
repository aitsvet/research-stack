# Research stack — daily cheat-sheet

Architecture, troubleshooting, and rationale live in `SETUP.md`. Read that if anything below doesn't work.
Как читать длинные первоисточники, не разнося контекст и не приписывая источнику лишнего, —
`READING.md`: окна, плотные копии, таблицы в JSON, различающие проверки, аудит отброшенного.

## Start the stack

```bash
docker compose up -d
./scripts/launch_chromium.sh
```

If `claude mcp list` doesn't show `zotero` connected:
```bash
set -a; source .env; set +a
claude mcp add zotero http://127.0.0.1:23120/mcp -t http \
  --header "Authorization: Bearer $ZOTERO_MCP_TOKEN"
```

Library sync between peers (one **origin** merges, any number of replicas;
independent of which AI front-end runs where): `scripts/sync_library.sh` on
the origin — design and operations in `SETUP.md`. A replica's zotero container
stops briefly during its sync window. To verify a sync, any peer can dump its
whole library (collections + items, keys, counts) as diff-friendly markdown:
`scripts/library_manifest.sh [out.md]`.

## Available MCPs

- **`zotero`** — search/read library, add by identifier, annotations, collections, semantic search. All write scopes on.
- **`playwright`** — drive the Chromium the user sees at `https://localhost:8888/`: navigate, click, fill, snapshot.
- **`chrome-devtools`** — inspect network, console, performance for the same Chromium.

## Don't waste cycles

- **Compose the existing toolkit before writing anything new.** The script table below is the catalog — read it first. Reusable logic belongs here (domain-agnostic); only project DATA (manifests, state files) goes in the project repo, never numbered one-off scripts. If a genuinely missing primitive comes up, generalize it into this repo instead of burying it in a bespoke script. Same rule for installed skills: a skill's own helpers ARE the toolkit — hand-rolling a shell equivalent right after installing it is the same failure.
- **Keep this stack domain-agnostic.** Scripts and docs here must be parameterised and universal — no corpus/project-specific data (file paths, site names like a particular journal/registry, subject terminology, item keys, one-shot ingest scripts). That belongs in the *project* repo (e.g. `<project>/scripts/`), which may import `zotero_mcp.py` from here. If you find domain specifics leaking in, move them out.
- Secrets stay in `.env` + `~/.claude.json`, never committed (HTTP-header `${VAR}` substitution is broken in Claude Code).
- Don't ever launch `/usr/bin/chromium` — the wrapper breaks input. `launch_chromium.sh` already calls the real binary correctly.

## Russian-paper lookup playbook

When a paper isn't found by DOI on the obvious site, work outward:

- **Identify the publisher first.** Hit `https://api.crossref.org/prefixes/<doi-prefix>` for the registrant name, then `https://api.crossref.org/works?filter=prefix:<prefix>,from-pub-date:<year>` to list recent DOIs from that prefix. Often reveals the journal's own site.
- **WordPress-backed journal sites** expose full content via `/wp-json/wp/v2/issues?slug=<slug>` (or `?search=<term>`). DOI, abstract, authors, references all in one JSON. Faster than rendering a JS-heavy article page.
- **Custom-rendered journal sites** (e.g. tables with `data-artid` and JS click handlers) — author indexes are usually cheaper to scrape than issue pages.
- **`elibrary.ru` (РИНЦ, the official RU papers DB).** Disable fulltext and abstract; search title-only with year-bounded filter — fulltext search is too noisy to find a known paper. The form is a `<form name="search">` with hidden checkboxes and a JS wrapper `check_search()`; set fields in JS and call the wrapper instead of fighting the UI. The form posts to `/query_results.asp`.
- **PDF behind elibrary login.** A direct POST to `/file_article.asp` 302s to `/defaultx.asp` even with the right cookies — there's a server-side single-use token. Just `playwright_click` the "Полный текст" link; the new tab's URL is the signed `/download/...pdf` URL, and `import_attachment_url` against that URL works.
- **DOI prefix didn't resolve on doi.org?** It's not registered yet — common for papers published in the current month. Find the article via the publisher's site instead and treat the DOI as informational metadata.
- **SSRN papers (`10.2139/ssrn.<id>`) are behind a Cloudflare challenge.** `import_attachment_url` returns 403 every time; `retry_unpaywall.py` won't help because Unpaywall's `oa_locations` for SSRN just point back at the gated landing page. Move the item to `needs-manual-access`, navigate to `https://papers.ssrn.com/sol3/papers.cfm?abstract_id=<id>` via playwright in the user-visible Chromium (it has the persistent CF cookie), click "Download This Paper", and `import_attachment_url` the resulting signed delivery URL. Same playbook as the elibrary case above.
- **SocArXiv / OSF files are often `.docx`, not PDF.** `https://osf.io/download/<5-char-id>/` 302s to `files.osf.io` and the file may have `contentType: application/octet-stream` with a `.docx` payload. `import_attachment_url` accepts and stores it, but `extract_texts.py`'s `pdftotext` path skips it (no PDF). Workaround: extract with stdlib (`zipfile` over `word/document.xml`, then iterate `{ns}p`/`{ns}t` elements), write to `discovery/<slug>/text/<itemKey>.txt`, and `extract_texts.py` will leave that file alone on subsequent runs.

## Attaching files to Zotero items

- **PDFs by URL** → use the MCP `import_attachment_url` tool. Pass `contentType: "application/pdf"` to skip the SingleFile snapshot path. Works for any HTTPS URL, including signed-token download URLs from logged-in browser sessions.
- **Local text/markdown/HTML files** → the MCP rejects `file://`, `127.0.0.1`, and RFC-1918 addresses, and binding a temp HTTP server to the public interface needs firewall coordination. Use `./scripts/zotero_attach_text.sh <itemKey> <file> [title] [tag1,tag2,...]` instead — it streams the file straight into `add_note` over the local MCP HTTP socket, so the contents never enter the model's context. The note ends up as a child of `itemKey`, wrapped in `<pre>` so whitespace renders.
- **Local PDFs (binary)** → `import_local_files(mcp, jobs, public_ip)` in `zotero_mcp.py` — wraps the public-IP serve-and-import work-around (import_attachment_url rejects loopback/RFC-1918): copies each to a throwaway dir, serves it on a routable host IP (`hostname -I`, **not** 10./172./192.168./127.) via system `python3 -m http.server`, imports by URL, tears down. `jobs` = `[(local_path, parentItemKey, title)]`.
- **Sanity-check downloads.** Zotero stores attachments at `config/Zotero/storage/<attachmentKey>/`. `file storage/<key>/*` quickly confirms whether you got a real PDF or a sign-in HTML page.

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
| `flow_counts.py [--discovery <dir>] [<top_key> [<selected_key> [<needs_key>]]]` | Print PRISMA-style flow numbers (candidates → unique-selected → with-text → needs-manual-access) computed from `triage.json` + text manifest + Zotero collection sizes. Run before any paper that claims a "screened/included/depth-read" count. |
| `zotero_attach_text.sh <item> <file>` | Stream a local text/markdown file into a Zotero child note without round-tripping through the model. |
| `extract_texts.py md <pdf\|dir> [--out D]` | Pure-python PDF→Markdown via pymupdf4llm (no GPU/OCR), marker-compatible `{N}`+48-dash pagination. Reads the embedded text layer, so it's a clean reference for diffing/patching marker output (catches OCR homoglyphs marker invents). Needs `pip install -r requirements.txt` (pymupdf4llm). |
| `patch_marker.py <marker.md> <ref.txt> --inplace` | Reconcile marker OCR errors against a pymupdf4llm reference, structure-preserving: Latin↔Cyrillic homoglyph repair (mixed-script→trust ref; pure-script flips restore Latin with a Roman-numeral guard), folded-ratio OCR fixes; skips digit/ID tokens, case-only diffs, ref-side fi/fl-ligature loss. Inserts/heading-demotion opt-in (noisy). |
| `chandra_ocr.py <pdf> [--out F] [--concurrency N]` | Full-document OCR of a scanned PDF via a vLLM-hosted Chandra model → paginated Markdown (`{N}`+48 dashes). For PDFs with no text layer, where `extract_texts.py md` yields nothing. Needs the endpoint up (`CHANDRA_URL`, default `http://localhost:8000/v1`). A page is decode-bound (~minutes) while vLLM batches happily, so `--concurrency 6` turns a multi-hour scan into a manageable one; leave it at 1 on an endpoint someone else is using. **If the model is reached over an SSH tunnel, give the address explicitly** — `ssh -L` binds `[::1]` only, Python's `getaddrinfo` returns `127.0.0.1` first, and whatever else holds that IPv4 port answers instead (a bare 404 that looks like a bad route). |
| `convert_office.py <file\|dir> [--dry-run] [--force]` | `.rtf/.doc/.docx/.odt` → Markdown via LibreOffice headless (HTML export → `html_md`), so tables and headings survive where a plain-text export would flatten them. **Runs in the `docconv` compose service** — LibreOffice is deliberately not installed on the host: `docker compose --profile tools run --rm docconv /corpus/<subdir>`. Skips documents that already have a `.md` unless `--force`. |
| `extract_tables.py md\|pdf <path> [--out F] [--pages A-B] [--min-rows N] [--grep RE]` | Pull tables out of a corpus into JSON so matrices stay data instead of prose. `md` reads pipe tables (survive only from Word sources) and anchors each to its nearest heading and `{N}` page mark; `pdf` uses PyMuPDF's geometry-based table finder — **use it whenever the markdown lost the structure**, because a PDF table degrades into flowing text where values drift away from their rows and get mis-paired on sight. Run both and compare when in doubt. Watch two artefacts: a footnote marker glues onto its value (`13` + note `46` → `1346`), and a row split by a page break arrives with an empty first cell. Method: `READING.md`. |
| `html_md.py` | HTML→Markdown used by both `chandra_ocr.py` and `convert_office.py`. Pure bs4 (no PyMuPDF) so it imports inside the LibreOffice container. Collapses HTML source wrapping while keeping real `<br>` breaks, and merges touching emphasis runs that word processors emit. |
| `fetch_pdf.sh <url> <outdir> <base>` | Render a URL→PDF via the container chromium (images off) → md. Captures SSL-broken / anti-bot pages WebFetch can't; constraints in the browser playbook below. |
| `ingest_sources.py <manifest.json> --collection K --pdfdir D --notesdir D --state F [--serve-ip IP] [--only ID...]` | Ingest an explicit MIXED list of *known* sources into a collection + emit one paginated-md note per item. Kinds: `arxiv` (id→Atom metadata+pdf), `pdfurl`, `web` (via `fetch_pdf.sh`), `localpdf` (via `import_local_files`, needs `--serve-ip`), `meta` (metadata-only). Create-or-update via `item_key`, `replace_notes`, note 413→md-only fallback, resumable via state. **This is the reusable form of per-project one-shot ingest — don't write a new bespoke script.** Use when you already know the exact sources; use `discover.py` instead for search-based discovery / DOI bootstrap. Manifest (the project-specific data) lives in the *project* repo. |
| `zotero_mcp.py` | Canonical MCP JSON-RPC client (session envelope, throttle, 429 back-off) + `result_json` / `item_key` / `add_note_file` / `import_file` / `import_local_files` helpers. **Import this** in new scripts instead of re-rolling the client — every script here does. |

## Corpus → grounded draft (synthesis)

Once `extract_texts.py` has produced the per-item `.txt`, drive synthesis off those, never the PDFs. Fan out **one reader-subagent per section** over just that section's `text/<itemKey>.txt`; each returns a short digest — citeable points tagged with the `itemKey`, the key caveats, and an explicit list of claims it could **not** find in the text (so nothing gets invented to fill a gap). The orchestrator writes the draft from the digests, keeping its own context lean. Build the reference list from Crossref (pull first-author / year / venue per DOI) rather than from model memory — that removes the hallucinated-author failure mode at the source — then gate on `verify_refs.py --per-line` before declaring done.

## Network routes to state and paywalled sources

- **Probe the route before spending browser timeouts.** `</dev/tcp/host/443` answers in a second whether the host is reachable at all; a 60-second Chromium timeout tells you the same thing sixty times slower.
- **Russian state portals** (`sozd.duma.gov.ru`, `duma.gov.ru`, `publication.pravo.gov.ru`) TCP-block both the host network and server-side WebFetch. A **SOCKS5 proxy on `localhost:3333`** (ask the user to bring it up) clears them for `curl --socks5-hostname` and, via `CHROMIUM_PROXY=socks5://localhost:3333 ./scripts/launch_chromium.sh`, for the container browser too. `publication.pravo.gov.ru/api/Documents?name=<number>` then serves official-publication metadata, and `…/file/pdf?eoNumber=<eo>` the signed PDF.
- **`cbr.ru`** is TCP-open and answers plain `curl` as long as a browser `User-Agent` is set; no proxy needed. Its site search resolves a bare act number to a file id (`/ref/analytics/na_vr/file/<id>`), which downloads from `/Queries/UniDbQuery/File/90134/<id>`.
- **`tc26.ru`** rejects datacenter addresses outright — it needs the proxy on both curl and Chromium.
- **Consolidated legal texts** (an act *with* its amendments) come from the free web version of a legal database, split one page per chapter and reassembled; the official portal publishes only the signed original, which is a scan and predates every amendment. Verify the document id by fetching it and reading the title before trusting it — a guessed id silently returns a different act.

## Full text of a commercial book

- Mirrors advertising «читать бесплатно полную версию» almost always serve the vendor's trial fragment relabeled. Detect **before** converting: the tail carries the vendor's boilerplate, the table of contents has ~3 sections, and the text is several times smaller than the page count implies (~2K characters per printed page is the sane ratio).
- Completeness is verified after download (section count, volume, tail of the text) — until then the file is not acquired.

## Acquiring bot-walled OA sources (browser playbook)

`acquire.py` / `retry_unpaywall.py` fetch server-side and fail on anti-bot/JS walls; their "OK/attached" is unreliable (a stored `.zotero-ft-cache` can be a cached interstitial, not the paper). Recovery ladder, cheapest first:

- **Chromium-first rule (applies to ANY gated fetch, not just papers).** curl/WebFetch are for trivially open URLs only. The moment a JS challenge (DDoS-Guard, Cloudflare, «проверка браузера»), download button, or SPA appears — don't mirror-hop with curl, go straight to the container Chromium: `launch_chromium.sh` (auto-detects the container's live X display; rerun after every container restart) → drive via playwright/chrome-devtools MCP or `cdp_eval.py`. `cdp_eval.py` needs the repo venv — if `.venv` is missing: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`. Web *discovery* stays on WebSearch (find candidate URLs); Chromium *fetches* them.
- **File downloads through CDP:** nav to the site's landing page once (challenge cookie sets), then click the download link *in-page* (`cdp_eval.py eval '...find("a[href*=get]").click()...'` — direct nav to the API URL loses the referer and bounces). The file lands in `/config/Downloads/` → `docker cp zotero:/config/Downloads/<f> <dest>`.

- **Locate a real OA copy first.** Unpaywall `best_oa_location`; Europe PMC (`/webservices/rest/search?query=DOI:"<doi>"`) or NCBI idconv for a PMCID. Prefer a repository / **PMC HTML full-text page** over a publisher `.pdf` URL.
- **`fetch_pdf.sh <html-url> <dir> <base>`** renders via a *fresh, cookieless* container Chromium: beats UA-only blocks (PMC, institutional repos, MDPI, bronze-OA publisher pages) but **not** Cloudflare/interactive walls, and it **cannot render a direct `.pdf` URL in headless** — always aim it at the HTML article page (PMC most reliable).
- **For Cloudflare / "Human Verification" walls use the user-visible Chromium** (playwright, persistent cookies). A "Just a moment" JS challenge often auto-clears on a second navigation a few seconds later (clearance cookie issued); managed / "Human Verification" walls need a person — open the tab(s) and ask the user to pass them.
- **Extract without bloating context:** `browser_evaluate` with the **`filename` param** writes the result straight to a file. HTML full text → return `document.body.innerText` (or the article-body selector). A gated **same-origin** PDF (host already cleared) → in-page `await fetch(url,{credentials:'include'})` → blob → base64 data-URL → save → decode to `.pdf`. A truly subscription-only PDF still 403s after clearance — keep abstract-level text and cite conservatively.
- Attach recovered local PDFs with `import_local_files` (public-IP serve), then re-run `extract_texts.py` to pick them up.

## Gotchas worth one more cycle

- **Zotero MCP body cap.** Requests over ~3700 bytes return `-32700 Parse error` (misleading). `discover.py`'s `cap_abstract` shrinks `abstractNote` until the payload fits, with `ensure_ascii=False` to keep non-Latin text compact — reuse it for any call that carries long text.
- **MCP protocol plumbing lives in `zotero_mcp.MCP`** — session envelope (`Mcp-Session-Id` + `notifications/initialized`, one session reusable across hundreds of calls), per-call throttle (~150 ms floor; faster bursts trip rate limits), and 429 back-off honouring `Retry-After` (≥1.3 s between bulk-import calls, exponential to ~40 s). Don't re-roll any of it.
- **`get_collection_items` overflows the tool-result token limit at >~30 items.** The MCP auto-saves the full JSON to a file and tells you the path — `grep -oE '"key": "[A-Z0-9]+"'` that file instead of asking the model to read it whole.
- **OpenAlex query mechanics.** Always pass `mailto=$OPENALEX_EMAIL` plus a `User-Agent: app/x.y (mailto:…)` header. Use `select=id,doi,title,publication_year,authorships,primary_location,abstract_inverted_index,open_access,type,cited_by_count,relevance_score` to shrink the payload ~5×. Reconstruct `abstract_inverted_index` by inverting the dict to position→word, then `" ".join(...)`. `relevance_score` is unbounded and not comparable across queries — min-max rescale per topic before ranking.
- **OpenAlex `oa_url` is unreliable** for hybrid/bronze OA — it often points to a publisher landing page rather than a PDF. Always have `retry_unpaywall.py` as a second pass; Unpaywall's repository-hosted `oa_locations` are higher-yield for automated fetch.
- **arXiv = Atom XML, not JSON.** Parse with `xml.etree.ElementTree` and namespace `{http://www.w3.org/2005/Atom}`. Field prefixes are `ti:`, `abs:`, `all:`. DOI is usually absent on preprints — dedupe by arXiv ID, and dedupe against OpenAlex hits both ways.
- **Bot-protection lands as HTML, not PDF — and survives into the text corpus.** Anubis / Cloudflare interstitials let Zotero "succeed" but store a tiny HTML page, and its `.zotero-ft-cache` then extracts as an interstitial. `file config/Zotero/storage/<key>/*` after a batch (anything not `PDF document` needs re-acquisition), and after `extract_texts.py` grep the `text/` corpus for `checking your browser|just a moment|human verification|enable javascript` plus flag suspiciously short files — re-acquire those via the browser playbook above.
- **`extract_texts.py` storage path + `pdftotext`.** The default `ZOTERO_STORAGE` is `~/research-stack/config/Zotero/storage`; set the env var explicitly for any other layout — a wrong path yields "NO PDF FOUND" for every item. `pdftotext` (poppler) is optional: the script falls back to pymupdf (already a requirement) when the binary is absent.
- **OpenAlex / Unpaywall politeness.** Both expect an email (`OPENALEX_EMAIL` / `UNPAYWALL_EMAIL`). Unpaywall hard-requires it.
- **Refining a noisy topic is a two-pass norm.** Scan the top 10 hits; if noisy, grep itemKeys from the `get_collection_items` dump, `batch_trash` them (≤100 per call), then re-run `discover.py` with a tightened query. Cheaper than over-engineering the query upfront.
- **AI-generated bibliographies hallucinate authors at correctly-formed DOIs.** A significant fraction of LLM-generated citations resolve to a real paper at the cited DOI but with completely different authors / venue / title. The DOI looks valid because it *is* valid — just for a different paper than the prose claims. Always run `verify_refs.py` before submission; treat any AUTHORS / TITLE / VENUE finding as blocking. For a formatted numbered reference list use `--per-line`, format each entry `Authors. Title // *Full Journal Name* doi:...` (ISO-690 ` // ` splits title from venue; use the unabbreviated journal name), and note that a shortened title (subtitle dropped) shows as a low-overlap TITLE flag — complete it from the Crossref title rather than assume a wrong-paper.
- **PRISMA-flow numbers in a draft must match the artefacts.** Triage gross-pick counts and unique-included counts routinely diverge by the slice-overlap factor; a "deep-read" claim that's not recorded as a Zotero tag or a manifest field is almost certainly drift. Run `flow_counts.py --discovery discovery/<paper>` and copy its output verbatim into the paper's PRISMA section.
- **`import_attachment_url` forbids loopback/private IPs** (SSRF guard: `127.0.0.1`, `10/172.16/192.168` all rejected) — a *local* file needs a public URL. `import_local_files` in `zotero_mcp.py` wraps the whole work-around (staging dir served on the public IP, import, teardown); never serve the repo root (it holds unpublished drafts). For *text* (`.md`/`.txt`), skip all this — `zotero_attach_text.sh` attaches it as a child note (no server).
- **`ifExists` on `import_attachment_url`:** use `add` for many files → one parent (e.g. all standards onto one book record); use `skip` for one-file-per-item dedup. `skip` checks *any* same-content-type child, so it wrongly blocks the 2nd+ file on a multi-attachment parent.
- **`add_note` size cap.** Notes over the cap return HTTP 413 (Content Too Large) — attach the `.txt` summary instead of a huge `.md`.
- **`search_library` with a collection-only filter throws `TypeError: value.includes`.** Use `get_collection_items` and grep its overflow file.
- **Verify Zotero writes via the SQLite DB, not the MCP.** `config/Zotero/zotero.sqlite` is host-visible; open read-only (`file:…?mode=ro&immutable=1`) and count `itemAttachments`/`itemNotes` by `parentItemID`. Far cheaper than `get_item_details` (which dumps every attached note body — millions of chars). MCP tool envelopes always contain `isError`, so a naive `"error" in response` success-check yields false negatives — check the DB.
- **marker (GPU) vs text-layer (pymupdf4llm).** marker OCR invents Latin↔Cyrillic homoglyphs (a Latin acronym rendered as its Cyrillic look-alike, e.g. `ABC→АВС`) and over-tags headings; the text layer is right for those but loses fi/fl ligatures (`finance→nance`) and garbles multi-line/column titles. So neither dominates — `patch_marker.py` trusts the reference only for safe, verifiable corrections. For web sources, `fetch_pdf.sh` (container chromium) → pymupdf4llm gives clean pdf+md in the same `{N}` format, no patching needed.

## Separate ability: deck pipeline (not part of the research stack)

`scripts/deck/` — self-contained slide-making toolkit (pptx profiling + lint, browser-measured layout through the container Chromium). It shares only the venv and Chromium with the stack above; nothing else in this file applies to it. Before ANY pptx work read `scripts/deck/SKILL.md` and follow its observe → measure → lint loop.
