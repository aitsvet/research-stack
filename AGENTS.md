# Research stack — daily cheat-sheet

Standing rules and how to start. Everything situational is one hop away:

| Doc | Read it when |
|---|---|
| `SCRIPTS.md` | choosing a tool — the script catalogue, the pipeline, per-tool traps |
| `ROUTES.md` | a source won't come out — host routes, bot-walls, lookup playbooks, discovery-API mechanics |
| `SETUP.md` | architecture, troubleshooting, rationale, and writing to Zotero over the MCP |
| `READING.md` | как читать длинные первоисточники, не разнося контекст и не приписывая источнику лишнего |

## Start the stack

```bash
docker compose up -d
./scripts/launch_chromium.sh     # CDP does not come up on its own
```

`up -d` starts `zotero` and nothing else — every other service sits behind a profile (`extra`, `tools`) and comes up only when asked for by name. `launch_chromium.sh` must be re-run after every container restart.

Host ports, all loopback-only (nginx template override + `DISABLE_IPV6` for the UI, `allowRemote=false` in `user.js` for the MCP, a cont-init sed patch for selkies, which hardcodes 0.0.0.0 upstream): **8888** Zotero UI (Selkies), **23120** Zotero MCP, **9222** Chromium CDP, **8082** selkies websocket. The container runs `network_mode: host` so CDP on container loopback is reachable from the host. Never disable the selkies service — it leads the desktop session Zotero and Chromium live in, and killing it takes the MCP and CDP with it.

If `claude mcp list` doesn't show `zotero` connected:
```bash
set -a; source .env; set +a
claude mcp add zotero http://127.0.0.1:23120/mcp -t http \
  --header "Authorization: Bearer $ZOTERO_MCP_TOKEN"
```

Library sync between peers (one **origin**, any number of replicas): `scripts/sync_library.sh` on the origin — read `scripts/zotero-sync/SKILL.md` before every sync or recovery, architecture in `SETUP.md`. Verify any peer with `scripts/library_manifest.sh [out.md]`.

## Available MCPs

- **`zotero`** — search/read library, add by identifier, annotations, collections, semantic search. All write scopes on.
- **`playwright`** — drive the Chromium the user sees at `https://localhost:8888/`: navigate, click, fill, snapshot.
- **`chrome-devtools`** — inspect network, console, performance for the same Chromium.

## Don't waste cycles

- **Compose the existing toolkit before writing anything new.** The catalogue in `SCRIPTS.md` is the toolkit — read it first. Reusable logic belongs here (domain-agnostic); only project DATA (manifests, state files) goes in the project repo, never numbered one-off scripts. If a genuinely missing primitive comes up, generalize it into this repo instead of burying it in a bespoke script. Same rule for installed skills: a skill's own helpers ARE the toolkit — hand-rolling a shell equivalent right after installing it is the same failure.
- **Keep this stack domain-agnostic.** Scripts and docs here must be parameterised and universal — no corpus/project-specific data (file paths, site names like a particular journal/registry, subject terminology, item keys, one-shot ingest scripts). That belongs in the *project* repo (e.g. `<project>/scripts/`), which may import `zotero_mcp.py` from here. If you find domain specifics leaking in, move them out.
- Secrets stay in `.env` + `~/.claude.json`, never committed (HTTP-header `${VAR}` substitution is broken in Claude Code). `.mcp.json` is committed and therefore holds only the secret-free entries (playwright, chrome-devtools); the Zotero entry stays out of the repo for that reason.
- **This repo is publishable — no personal information, in files or in history.** No usernames, real names, home-dir paths like `/home/<user>`, or personal item titles; the history was scrubbed of these once already, so do not reintroduce them. Use `$HOME` and placeholders in docs and examples, and scan the diff for identifiers before committing. Domain materials — standards, papers, notes — never land here either: they belong in `~/literature/<theme>/`, and this repo carries only the pipeline that processes them.
- Don't ever launch `/usr/bin/chromium` — the wrapper breaks input. `launch_chromium.sh` already calls the real binary correctly.

## The two passes

Discovery is **two-pass on purpose**: `discover.py` creates candidates and **never attaches PDFs**;
the user picks rows; only then does `acquire.py` fetch full text. Bulk-acquiring during discovery drags
in paywall/HTML noise that has to be re-cleaned. Mechanics and the full rationale: `SCRIPTS.md`.

## Corpus → grounded draft (synthesis)

Once `extract_texts.py` has produced the per-item `.txt`, drive synthesis off those, never the PDFs. Fan out **one reader-subagent per section** over just that section's `text/<itemKey>.txt`; each returns a short digest — citeable points tagged with the `itemKey`, the key caveats, and an explicit list of claims it could **not** find in the text (so nothing gets invented to fill a gap). The orchestrator writes the draft from the digests, keeping its own context lean. Build the reference list from Crossref (pull first-author / year / venue per DOI) rather than from model memory — that removes the hallucinated-author failure mode at the source — then gate on `verify_refs.py --per-line` before declaring done.

From draft to submission the loop is `export_paper.sh` → the editor marks up the exported file by hand → `merge_edits.py` folds their copy back in as a three-way paragraph merge. `prose_lint.py` counts what would otherwise be re-read by eye each round; its bands and word lists live in the project, never here. Mechanics and traps: `SCRIPTS.md`.

## Separate ability: deck pipeline (not part of the research stack)

`scripts/deck/` — self-contained slide-making toolkit (pptx profiling + lint, browser-measured layout through the container Chromium). It shares only the venv and Chromium with the stack above; nothing else in this file applies to it. Before ANY pptx work read `scripts/deck/SKILL.md` and follow its observe → measure → lint loop.
