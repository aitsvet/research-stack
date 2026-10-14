# Research stack — daily cheat-sheet

| Doc | Read it when |
|---|---|
| `SCRIPTS.md` | choosing a tool — catalogue, pipeline, corpus → draft synthesis, per-tool traps |
| `ROUTES.md` | a source won't come out — host routes, bot-walls, lookup playbooks |
| `SETUP.md` | architecture, troubleshooting, rationale, writing to Zotero over the MCP |
| `WINDOWS.md` | the stack on a Windows workstation without Docker |
| `READING.md` | как читать длинные первоисточники, не приписывая источнику лишнего |
| `EXTRACT.md` | корпус как зеркало Zotero: извлечение текста, проверка полноты, сверка |
| `CLEARANCE.md` | clearing the tree and reachable history for publication |

## ⛔ Publication gate — every commit

This repo is public; every commit publishes. A leak is *any* value naming a person, a private
repo, your filesystem or one private body of work — no small ones, no "I'll check before I push".

1. Read `git diff HEAD` and the message as content: comments, defaults, example paths, mounts, fixtures.
2. Grep both; explain every hit, never delete mechanically:
   ```bash
   { git diff HEAD; printf '%s\n' "$MSG"; } | grep -nEi '[0-9a-f]{16,}|(ssh|https?)://[^/ ]*:[^/@ ]*@|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|Bearer [A-Za-z0-9._-]{16,}|BEGIN [A-Z ]*PRIVATE KEY|/home/[a-z]|~/[a-z]|\.\./[a-z]'
   ```
3. A clean grep is not clearance: every proper noun, odd path, host, model tag and narrow feature
   answers Q1–5 in `CLEARANCE.md`; Q4/Q5 yes → parameterize or move it to the project repo.
4. Never point at another repo's docs, even a public one — inline the substance.
5. State the rule, drop the incident: no sizes, tunnels, personal conventions, war stories.
6. Fixing the tip does not unpublish history (`CLEARANCE.md` §*Rewriting findings safely*);
   a pushed leak is reported at once.

## Start the stack

```bash
mkdir -p ~/zotero                # the library lives here (ZOTERO_HOME), never in the checkout
docker compose up -d             # zotero only; the rest sits behind the `extra` and `tools` profiles
./scripts/launch_chromium.sh     # CDP does not come up on its own — re-run after every restart
```

Loopback-only ports: **8888** Zotero UI (Selkies), **23120** Zotero MCP, **9222** Chromium CDP,
**8082** selkies websocket (`network_mode: host`; pinning details in `SETUP.md`). Never disable
selkies — it leads the desktop session that Zotero, the MCP and Chromium live in. Never launch
`/usr/bin/chromium`; the wrapper breaks input.

`zotero` missing from `claude mcp list`: `set -a; source .env; set +a; claude mcp add zotero
http://127.0.0.1:23120/mcp -t http --header "Authorization: Bearer $ZOTERO_MCP_TOKEN"`.
Other MCPs: `playwright` (drive that Chromium), `chrome-devtools` (inspect it).

Peer library sync: `scripts/sync_library.sh` on the origin — read `scripts/zotero-sync/SKILL.md`
first. Verify a peer with `scripts/library_manifest.sh [out.md]`.

## Don't waste cycles

- Compose the toolkit in `SCRIPTS.md` before writing anything; a missing primitive is generalized
  here, never a numbered one-off script.
- Domain-agnostic only: project data, paths, site names and terminology live in the project repo.
- Secrets stay in `.env` and `~/.claude.json`; `.mcp.json` holds only the secret-free entries.
- Discovery is two-pass: `discover.py` never attaches PDFs; `acquire.py` fetches only the rows picked.
- `scripts/deck/` is a separate slide toolkit — read its `SKILL.md` before any pptx work.
