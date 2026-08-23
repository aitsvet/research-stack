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

Library sync between peers (one **origin**, any number of replicas;
independent of which AI front-end runs where): `scripts/sync_library.sh` on
the origin — read `scripts/zotero-sync/SKILL.md` before every sync or recovery;
architecture is in `SETUP.md`. A replica's zotero container
stops briefly during its sync window. Object keys are immutable: the sync uses
whole-snapshot three-way fast-forwards and stops on independent changes. To verify a sync, any peer can dump its
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
- **`elibrary.ru` (РИНЦ, the official RU papers DB).** Needs a logged-in session; anonymously `querybox.asp` renders but exposes no usable form. The search form is `<form name="results">` (**not** `search`) and there is **no** `check_search()` wrapper — drive it directly: set `f.ftext.value`, tick the `where_*` checkboxes (`where_name`, `where_abstract`, `where_keywords`, `where_fulltext`, `where_affiliation`), set `f.changed.value='1'`, then `f.action='https://elibrary.ru/query_results.asp'; f.method='POST'; f.submit()`. Clicking the "ПОИСК" anchor instead just follows its `href` and loses the form. Prefer title-only (`where_name`) — abstract/keyword widens usefully but fulltext is too noisy.
  - **Results are paged**: 100 rows per page, `query_results.asp?pagenum=N`; the "В конец" link gives the last page number. Rows are `a[href*="item.asp"]`; the small integer next to each is the **citation count**, carried as `a[href*="cit_items.asp"]` — a free relevance signal, no extra fetch.
  - **Russian stemming is aggressive and silently pollutes results.** A short borrowed term matches unrelated native words (searching a 5-letter Arabic term also returned sausage, place-name and personal-name hits). Always post-filter on a domain keyword whitelist plus a noise blacklist rather than trusting rank.
- **PDF behind elibrary login.** The "Полный текст" link calls `file_article(id, filenum)`, which sets `form.action='/file_article.asp'`, fills `fileid`/`filenum`, sets `target='_blank'` and submits. The popup is **blocked** in a CDP-driven browser, so clicking it appears to do nothing. Instead submit in the same tab — set the same fields but `f.target=''` — and `location.href` becomes the signed `https://elibrary.ru/download/elibrary_<id>_<n>.pdf`. **`import_attachment_url` against that URL fails**: the MCP fetches server-side without the session. Pull it with `curl` carrying the live cookies (`Network.getAllCookies` over CDP, filter `elibrary`) plus `--socks5-hostname` if the proxy is in play, then attach with `import_local_files`. Items whose full text was never deposited land back on `file_article.asp` — treat that as "no fulltext", not a failure.
- **DOI prefix didn't resolve on doi.org?** It's not registered yet — common for papers published in the current month. Find the article via the publisher's site instead and treat the DOI as informational metadata.
- **SSRN papers (`10.2139/ssrn.<id>`) are behind a Cloudflare challenge.** `import_attachment_url` returns 403 every time; `retry_unpaywall.py` won't help because Unpaywall's `oa_locations` for SSRN just point back at the gated landing page. Move the item to `needs-manual-access`, navigate to `https://papers.ssrn.com/sol3/papers.cfm?abstract_id=<id>` via playwright in the user-visible Chromium (it has the persistent CF cookie), click "Download This Paper", and `import_attachment_url` the resulting signed delivery URL. Same playbook as the elibrary case above.
- **SocArXiv / OSF files are often `.docx`, not PDF.** `https://osf.io/download/<5-char-id>/` 302s to `files.osf.io` and the file may have `contentType: application/octet-stream` with a `.docx` payload. `import_attachment_url` accepts and stores it, but `extract_texts.py`'s `pdftotext` path skips it (no PDF). Workaround: extract with stdlib (`zipfile` over `word/document.xml`, then iterate `{ns}p`/`{ns}t` elements), write to `discovery/<slug>/text/<itemKey>.txt`, and `extract_texts.py` will leave that file alone on subsequent runs.

## Attaching files to Zotero items

- **PDFs by URL** → use the MCP `import_attachment_url` tool. Pass `contentType: "application/pdf"` to skip the SingleFile snapshot path. Works for any HTTPS URL, including signed-token download URLs from logged-in browser sessions.
- **Local text/markdown/HTML files** → the MCP rejects `file://`, `127.0.0.1`, and RFC-1918 addresses, and binding a temp HTTP server to the public interface needs firewall coordination. Use `./scripts/zotero_attach_text.sh <itemKey> <file> [title] [tag1,tag2,...]` instead — it streams the file straight into `add_note` over the local MCP HTTP socket, so the contents never enter the model's context. The note ends up as a child of `itemKey`, wrapped in `<pre>` so whitespace renders.
- **Local PDFs (binary)** → `import_local_files(mcp, jobs, host_ip)` in `zotero_mcp.py` — copies each to a throwaway dir, serves it only for the import, and tears down. Literal RFC1918 URLs are rejected by the plugin, so private IPv4 hosts use `<ip>.nip.io` as a DNS alias back to the same machine; no file is sent to nip.io. `jobs` = `[(local_path, parentItemKey, title)]`.
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
| `fetch_pdf.sh <url> <outdir> <base>` | Render a URL→PDF via the container chromium (images off by default) → md. Captures SSL-broken / anti-bot pages WebFetch can't; constraints in the browser playbook below. For a visual document that must keep its images (e.g. a local notes page), set `FETCH_PDF_IMAGES=1 FETCH_PDF_NO_MD=1` to skip the image-strip and the md-extraction follow-up. `url` may be `file://` or a local `http://127.0.0.1:<port>/...` (container shares host network) as well as a public URL. |
| `md_pdf.sh <file.md\|dir> [out.pdf]` | Markdown (with relative image links) → PDF in one call, printed by the container chromium — nothing installed on the host. A dir concatenates its `*.md` (sorted) into one PDF, page break between documents. Wraps `fetch_pdf.sh` (images on, no md follow-up), serving the md's directory on `127.0.0.1:${MD_PDF_PORT:-8377}` for the print. |
| `ingest_sources.py <manifest.json> --collection K --pdfdir D --notesdir D --state F [--serve-ip IP] [--only ID...]` | Ingest an explicit MIXED list of *known* sources into a collection + emit one paginated-md note per item. Kinds: `arxiv` (id→Atom metadata+pdf), `pdfurl`, `web` (via `fetch_pdf.sh`), `localpdf` (via `import_local_files`, needs `--serve-ip`), `meta` (metadata-only). Create-or-update via `item_key`, `replace_notes`, note 413→md-only fallback, resumable via state. **This is the reusable form of per-project one-shot ingest — don't write a new bespoke script.** Use when you already know the exact sources; use `discover.py` instead for search-based discovery / DOI bootstrap. Manifest (the project-specific data) lives in the *project* repo. |
| `zotero_mcp.py` | Canonical MCP JSON-RPC client (session envelope, throttle, 429 back-off) + `result_json` / `item_key` / `add_note_file` / `import_file` / `import_local_files` helpers. **Import this** in new scripts instead of re-rolling the client — every script here does. |

## Corpus → grounded draft (synthesis)

Once `extract_texts.py` has produced the per-item `.txt`, drive synthesis off those, never the PDFs. Fan out **one reader-subagent per section** over just that section's `text/<itemKey>.txt`; each returns a short digest — citeable points tagged with the `itemKey`, the key caveats, and an explicit list of claims it could **not** find in the text (so nothing gets invented to fill a gap). The orchestrator writes the draft from the digests, keeping its own context lean. Build the reference list from Crossref (pull first-author / year / venue per DOI) rather than from model memory — that removes the hallucinated-author failure mode at the source — then gate on `verify_refs.py --per-line` before declaring done.

## Network routes to state and paywalled sources

- **Probe the route before spending browser timeouts.** `</dev/tcp/host/443` answers in a second whether the host is reachable at all; a 60-second Chromium timeout tells you the same thing sixty times slower.
- **Russian state portals** (`sozd.duma.gov.ru`, `duma.gov.ru`, `publication.pravo.gov.ru`) TCP-block both the host network and server-side WebFetch. A **SOCKS5 proxy on `localhost:3333`** (ask the user to bring it up) clears them for `curl --socks5-hostname` and, via `CHROMIUM_PROXY=socks5://localhost:3333 ./scripts/launch_chromium.sh`, for the container browser too. `publication.pravo.gov.ru/api/Documents?name=<number>` then serves official-publication metadata, and `…/file/pdf?eoNumber=<eo>` the signed PDF.
- **`cbr.ru`** is TCP-open and answers plain `curl` as long as a browser `User-Agent` is set; no proxy needed. Its site search resolves a bare act number to a file id (`/ref/analytics/na_vr/file/<id>`), which downloads from `/Queries/UniDbQuery/File/90134/<id>`.
- **`tc26.ru`** rejects datacenter addresses outright — it needs the proxy on both curl and Chromium.
- **Russian state TLS.** Sites like `fstec.ru` serve certificates from the Ministry of Digital Development CA, which no default trust store carries: curl dies with `ssl_verify_result: 20` (`unable to get local issuer certificate`) and Chromium shows "Privacy error". **Do not paper over it with `curl -k`** — install the CA. Certificates come from `https://gu-st.ru/content/lending/`: `russian_trusted_root_ca_pem.crt`, `russian_trusted_sub_ca_pem.crt` and **`russian_trusted_sub_ca_2024_pem.crt`**. The 2024 sub CA is the one actually signing current certificates — installing only the 2022 pair leaves verification failing exactly as before, which reads like the install didn't work. Diagnose by comparing the server certificate's Authority Key Identifier against each sub CA's Subject Key Identifier; they must match.
  Verify the certificates before trusting them (`openssl x509 -noout -subject -issuer -dates -fingerprint -sha256`), then install into the system store on both host and container, and additionally into Chromium's own NSS database, which ignores the system store:
  ```bash
  sudo install -m 644 russian_trusted_*.crt /usr/local/share/ca-certificates/ && sudo update-ca-certificates
  docker exec <container> bash -c 'install -m 644 /tmp/rtr_*.crt /usr/local/share/ca-certificates/ && update-ca-certificates'
  docker exec <container> certutil -A -n "Russian Trusted Sub CA 2024" -t "C,," -i /tmp/rtr_sub2024.crt -d sql:/config/.pki/nssdb
  ```
  Checking `grep 'Russian Trusted' /etc/ssl/certs/ca-certificates.crt` proves nothing — the bundle is base64 without subject comments. Verify by extracting each block and reading its subject, or simply re-run the request and require `ssl_verify_result=0`.
- **Consolidated legal texts** (an act *with* its amendments) come from the free web version of a legal database, split one page per chapter and reassembled; the official portal publishes only the signed original, which is a scan and predates every amendment. Verify the document id by fetching it and reading the title before trusting it — a guessed id silently returns a different act. The free tier of one such database gates *some* documents to evenings and weekends Moscow time while serving others freely; a "document unavailable" page for one act is not a verdict on the database.
- **Standards mirrors** (`meganorm.ru` and its siblings) carry two representations of a national standard: the document page under `/mega_doc/…/<dir>/<slug>.html` is the **full text and is always there**, while the scanned official edition under `/Data2/<a>/<b>.pdf` is linked only from some pages. Fetch the page first and take the PDF if it happens to be attached — the reverse order finds nothing. The `<dir>` segment is not derivable from the document number, so walk the plausible range and stop at the first page that answers with the right title. The PDF request needs a `Referer` or it 403s, and the site's own `/search?text=` is a 404 — resolve slugs with a web search instead.

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
- **A managed challenge can be unsolvable even with a person clicking.** Two causes, both invisible from the page. (1) *An attached CDP client.* Turnstile detects a debugger, and while `playwright-mcp` / `chrome-devtools-mcp` hold a connection the checkbox never resolves no matter who clicks — `ss -tnp | grep :9222` lists them; relaunch without `--remote-debugging-port` and without `--test-type` for the human step. (2) *Datacenter egress.* Cloudflare scores hosted ranges (OVH, Hetzner…) harshly, so a box that fails here passes first try from a residential address. A proxy, a fresh profile, and a new window fix neither. Before blaming a proxy, prove the path instead of guessing: no `--proxy-server` flag, no proxy env var / policy file / profile key, and compare the browser's own `fetch('https://api.ipify.org')` against a direct `curl` — identical IPs mean no proxy is in play, whatever the symptom looks like. When both causes hold, stop tuning flags and change channel: pass the URL or file to the user over a user-controlled out-of-band channel, they open it from their own network and send the result back.
- **Extract without bloating context:** `browser_evaluate` with the **`filename` param** writes the result straight to a file. HTML full text → return `document.body.innerText` (or the article-body selector). A gated **same-origin** PDF (host already cleared) → in-page `await fetch(url,{credentials:'include'})` → blob → base64 data-URL → save → decode to `.pdf`. A truly subscription-only PDF still 403s after clearance — keep abstract-level text and cite conservatively.
- Attach recovered local PDFs with `import_local_files` (temporary same-host serve; private IPs use the nip.io alias described above), then re-run `extract_texts.py` to pick them up.

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
- **`import_attachment_url` forbids literal loopback/private-IP URLs** (SSRF guard: `127.0.0.1`, `10/172.16/192.168` all rejected). `import_local_files` in `zotero_mcp.py` wraps the work-around: it stages only the requested files, serves them on a host address reachable from Zotero, and expresses a private IPv4 address as `<ip>.nip.io` so the bytes still travel directly between the two hosts; never serve the repo root (it holds unpublished drafts). For *text* (`.md`/`.txt`), skip all this — `zotero_attach_text.sh` attaches it as a child note (no server).
- **`ifExists` on `import_attachment_url`:** use `add` for many files → one parent (e.g. all standards onto one book record); use `skip` for one-file-per-item dedup. `skip` checks *any* same-content-type child, so it wrongly blocks the 2nd+ file on a multi-attachment parent.
- **`add_note` size cap.** Notes over the cap return HTTP 413 (Content Too Large) — attach the `.txt` summary instead of a huge `.md`.
- **`add_note` also 400s with `-32700 Parse error` on some inputs**, and it is *not* a size limit — 130 KB notes go through while a 5 KB one fails, and bisecting the same file gives a boundary that drifts with unrelated edits. Don't chase it: restructure the file (fold a small extract into a larger document that already attaches, or split into two notes) and move on. Budget one retry, not an investigation.
- **Cyrillic filenames break `import_attachment_url`** with `Invalid attachment URL` — the URL string is validated as ASCII. `import_local_files` now sanitises staging names, so the failure only reappears if you build the URL yourself. The attachment is titled from the `title` argument anyway, so the staged name never matters.
- **`search_library` with a collection-only filter throws `TypeError: value.includes`.** Use `get_collection_items` and grep its overflow file.
- **Verify Zotero writes via the SQLite DB, not the MCP.** `config/Zotero/zotero.sqlite` is host-visible; open read-only (`file:…?mode=ro&immutable=1`) and count `itemAttachments`/`itemNotes` by `parentItemID`. Far cheaper than `get_item_details` (which dumps every attached note body — millions of chars). MCP tool envelopes always contain `isError`, so a naive `"error" in response` success-check yields false negatives — check the DB.
- **PlantUML: pin the version, or every figure silently redraws.** Debian's packaged plantuml is 1.2020.02; rendering an *untouched* source with it returned a PNG 18 % smaller with different spacing, so regenerating one edited diagram would restyle every other figure in the same commit — a change nobody made and nobody reviewed. The image pins 1.2024.7, the version the existing corpus was rendered with. Proof that a renderer matches the corpus is not "it looks right": re-render an unchanged source and compare. Byte-equality is too strict (PNG carries a compressed metadata blob and antialiasing drifts with the font stack) — compare IDAT pixels instead. In a synthetic reproduction the pinned image matched a reference `architecture.png` at identical dimensions with 8 differing pixels out of 738 779 (max channel delta 7); the Debian build failed that test outright. Bump the pin only deliberately, and re-render every diagram in one pass.
- **marker (GPU) vs text-layer (pymupdf4llm).** marker OCR invents Latin↔Cyrillic homoglyphs (a Latin acronym rendered as its Cyrillic look-alike, e.g. `ABC→АВС`) and over-tags headings; the text layer is right for those but loses fi/fl ligatures (`finance→nance`) and garbles multi-line/column titles. So neither dominates — `patch_marker.py` trusts the reference only for safe, verifiable corrections. For web sources, `fetch_pdf.sh` (container chromium) → pymupdf4llm gives clean pdf+md in the same `{N}` format, no patching needed.

## Separate ability: deck pipeline (not part of the research stack)

`scripts/deck/` — self-contained slide-making toolkit (pptx profiling + lint, browser-measured layout through the container Chromium). It shares only the venv and Chromium with the stack above; nothing else in this file applies to it. Before ANY pptx work read `scripts/deck/SKILL.md` and follow its observe → measure → lint loop.
