# Self-hosted Zotero + Browser research stack with MCP

A self-hosted research environment:

- **Zotero 7** desktop streamed to the browser at `https://<host>:8888/` (Selkies/WebRTC).
- **Zotero MCP** plugin so AI assistants can search, read, annotate, and (optionally) write to the library.
- **Chromium** inside the same container, exposed over CDP on `:9222` so the assistant attaches to the same tab you see.
- **`@playwright/mcp`** (Microsoft) for browser *action* — navigate, click, fill, scrape.
- **`chrome-devtools-mcp`** (Google) for browser *observation* — network, console, performance, heap.

## Architecture

```
┌───────────── host VM ─────────────────────────────────────────────────┐
│                                                                       │
│  Browser ──https──> :8888 ──┐ (Selkies HTML5 stream)                  │
│                             ▼                                         │
│         ┌─── Docker container (network_mode: host) ───┐               │
│         │  • Zotero 7 desktop  (UI on :8888 nginx)    │               │
│         │  • Zotero MCP plugin -> :23120  (HTTP/JSON) │               │
│         │  • Chromium -> :9222  (CDP)                 │               │
│         └─────────────────────────────────────────────┘               │
│                                                                       │
│  Claude/Codex/OpenCode ──stdio──> @playwright/mcp ──CDP──> :9222      │
│                       ──stdio──> chrome-devtools-mcp ──CDP──> :9222   │
│                       ──http───> :23120/mcp  (Zotero MCP, bearer auth)│
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘
```

Container uses **`network_mode: host`** — every port the container opens is directly on the host. Side effect: Zotero's web UI (:8888) and the MCP plugin (:23120) bind to whatever interface the in-container service is configured to. Keep both on `127.0.0.1` (or behind a firewall) — see *Security* below.

## Files in this directory

| File | Tracked? | Purpose |
|---|---|---|
| `docker-compose.yml` | yes | Container definition |
| `.mcp.json` | yes | Project-scoped MCP config (no secrets) — Claude Code & OpenCode |
| `.env.example` | yes | Template for environment values |
| `SETUP.md` | yes | This document |
| `scripts/launch_chromium.sh` | yes | Idempotent launcher for the in-container Chromium with CDP |
| `scripts/apply_config.sh` | yes | Copies Zotero `user.js` into the active profile |
| `scripts/sync_library.sh` | yes | Two-way library sync between peers — run on the origin (section below) |
| `scripts/zotero-sync/SKILL.md` | yes | Required operating skill for peer sync, key recovery, manifests, and literature reconciliation |
| `scripts/snapshot_db.sh` / `place_snapshot.sh` / `merge_replica.py` | yes | Sync building blocks: crash-consistent DB snapshot, verified DB swap, three-way fast-forward classifier |
| `scripts/library_manifest.sh` | yes | Whole-library manifest (collections + items) as markdown — post-sync verification (section below) |
| `scripts/` (the rest) | yes | Research discovery/acquisition/extraction pipeline — catalogued in `SCRIPTS.md` |
| `docker/docconv/` | yes | Image for the `docconv` service: LibreOffice headless for office-document → Markdown conversion, kept off the host. Batch, not a daemon — `docker compose --profile tools run --rm docconv <dir>`; point `CORPUS` at the tree to convert (default `../literature`). |
| `requirements.txt` | yes | Python deps for the pipeline scripts (`.venv/bin/pip install -r`) |
| `open-webui/` / `opencode/` / `jupyter/` | yes | Optional appliances: AI front-ends (Open WebUI, OpenCode) and JupyterLab+MCP, each with its own README. `opencode/` and `jupyter/` bring their own compose; Open WebUI runs from the root compose instead — it moved there together with SearXNG, and the appliance copy that stayed behind only clashed over `container_name`. |
| `user.js` | yes | Zotero MCP plugin prefs — `requireAuth=true`, all write scopes on |
| `.env` | **no** | Real secrets: web-UI password, Zotero MCP token |
| `config/` | **no** | Zotero profile, library, attachments, installed `.xpi` plugins |

## Prerequisites

- Docker (your user must be in the `docker` group).
- Node 18+ (`npx`) for the browser MCPs.
- One of `claude` / `codex` / `opencode` CLIs.

## First-time setup

1. **Bring up the container.**
   ```bash
   cp .env.example .env
   nano .env                       # set ZOTERO_PASSWORD, leave ZOTERO_MCP_TOKEN blank for now
   docker compose up -d
   ```

2. **Open the Zotero UI** from your laptop:
   ```bash
   ssh -L 8888:localhost:8888 <user>@<vm-host>
   ```
   Open `https://localhost:8888/`, accept the self-signed cert, log in with the basic-auth credentials from `.env`.

3. **Enable Zotero's local API**: in Zotero, *Edit → Settings → Advanced* → check **"Allow other applications on this computer to communicate with Zotero"**.

4. **Install the MCP plugin.** Download once:
   ```bash
   curl -L -o config/zotero-mcp-for-claude-code.xpi \
     https://github.com/lricher7329/zotero-mcp-claude-code/releases/latest/download/zotero-mcp-for-claude-code.xpi
   ```
   Then in Zotero: *Tools → Add-ons → ⚙ → Install Add-on From File* → pick `/config/zotero-mcp-for-claude-code.xpi`. Restart Zotero when prompted.

5. **Apply the plugin prefs from the repo** — gives you `requireAuth=true`, all write scopes on, and the AI/content shaping defaults. The auto-generated bearer token in `prefs.js` is preserved (it isn't in the overlay).
   ```bash
   ./scripts/apply_config.sh           # copies user.js -> profile/user.js
   docker compose restart              # so Zotero picks up the new user.js
   ```

6. **Put the bearer token in `.env`** — grab it from prefs and stash it:
   ```bash
   TOKEN=$(docker exec zotero grep mcp.server.authToken \
     /config/.zotero/zotero/*.default/prefs.js | grep -oP 'zmcp_[a-z0-9]+')
   echo "ZOTERO_MCP_TOKEN=$TOKEN" >> .env
   ```

7. **Launch Chromium with CDP** (idempotent — rerun any time the container is restarted):
   ```bash
   ./scripts/launch_chromium.sh
   ```
   Verify: `curl -s http://127.0.0.1:9222/json/version | jq .Browser`.

8. **Register the MCP servers with your AI tool of choice** (one-time per tool — see next section).

## Registering MCP servers in your AI tool

### Claude Code

The committed `.mcp.json` already configures **playwright** and **chrome-devtools** at project scope. Register the Zotero entry once (token comes from `.env`, never enters the repo):

```bash
cd ~/research-stack
set -a; source .env; set +a
claude mcp add zotero http://127.0.0.1:23120/mcp -t http \
  --header "Authorization: Bearer $ZOTERO_MCP_TOKEN"
claude mcp list
```

Then start a new Claude Code session in this directory. All three MCPs should be green.

### OpenCode

OpenCode reads `opencode.json` (project) or `~/.config/opencode/config.json` (global). It uses the same JSON shape as Claude. Mirror `.mcp.json` and append zotero:

```json
{
  "mcp": {
    "playwright":      { "command": "npx", "args": ["-y", "@playwright/mcp@latest", "--cdp-endpoint", "http://127.0.0.1:9222"] },
    "chrome-devtools": { "command": "npx", "args": ["-y", "chrome-devtools-mcp@latest", "--browser-url", "http://127.0.0.1:9222"] },
    "zotero":          { "type": "http", "url": "http://127.0.0.1:23120/mcp", "headers": { "Authorization": "Bearer ${ZOTERO_MCP_TOKEN}" } }
  }
}
```

Run OpenCode from a shell where `.env` is sourced.

### OpenAI Codex CLI

Codex uses TOML in `~/.codex/config.toml`. Append:

```toml
[mcp_servers.playwright]
command = "npx"
args    = ["-y", "@playwright/mcp@latest", "--cdp-endpoint", "http://127.0.0.1:9222"]

[mcp_servers.chrome-devtools]
command = "npx"
args    = ["-y", "chrome-devtools-mcp@latest", "--browser-url", "http://127.0.0.1:9222"]

[mcp_servers.zotero]
command = "bash"
args = ["-lc", "exec \"<path-to-launcher>\" zotero"]
```

The launcher entry point loads `ZOTERO_MCP_TOKEN` from this repo's `.env` and bypasses proxies for localhost.
Its pinned bridge requires Node >=20.18.1; use a supported Node LTS release.

## Daily operation

| Action | Command |
|---|---|
| Start stack | `docker compose up -d && ./scripts/launch_chromium.sh` |
| Stop stack | `docker compose down` (Chromium dies with the container) |
| Restart Chromium only | `./scripts/launch_chromium.sh` |
| Tail Zotero logs | `docker logs -f zotero` |
| Tail Chromium logs | `docker exec zotero tail -f /tmp/chromium.log` |
| Web UI tunnel | `ssh -L 8888:localhost:8888 <vm>` |

## Library sync between peers

Read `scripts/zotero-sync/SKILL.md` before operating or recovering the sync;
this section records the architecture and configuration rationale.

Any number of hosts can run this stack, each with its own AI front-end
(Claude Code, OpenCode, Open WebUI, …) — the sync neither knows nor cares
which. Like fast-forward-only git with a hub: one host is the **origin**,
every other is a **replica**; peers exchange the Zotero library itself over
ssh+rsync, no zotero.org account involved.

All peers may write, but a Zotero object key is durable identity: item, note,
attachment and collection keys must never be regenerated during a sync.
`scripts/sync_library.sh` therefore keeps the last common snapshot in
`.sync/base/zotero.sqlite` and permits whole-snapshot fast-forwards only. Run
it ON the origin, once per replica:

1. stops zotero on the replica (its writes pause safely; the replica's MCP is
   down for the duration) and fetches its DB; if the origin has no DB yet, it
   transfers the replica's library wholesale and stops there (first fill);
2. takes a crash-consistent snapshot of its own DB — `snapshot_db.sh`,
   sub-second `docker pause` (why not the sqlite backup API — see its header);
3. `merge_replica.py` compares origin and replica against the last common
   snapshot. If only one peer changed—or one peer demonstrably contains every
   change made by the other—that exact snapshot wins. Independent changes stop
   before mutation with a report in `.sync/plan.json`;
4. if the replica wins, pulls its storage and installs its exact DB on the
   origin; then pushes the winning snapshot back with compressed,
   partial-transfer-preserving `rsync --delete` (storage, styles,
   translators + the `config/.zotero/` profile carrying the MCP plugin);
   `place_snapshot.sh` on the replica swaps the DB, checks integrity and item
   count, and the replica's container starts again — or is left stopped if
   the check fails.

No objects are replayed through MCP and no key map is created. A conflict is
resolved outside this script (manual choice or Zotero native sync), followed
by another run once the peers agree. `place_snapshot.sh` retains the replaced
live DB as `zotero.sqlite.prev`; the origin also retains its pre-fast-forward
snapshot in `.sync/pre-fast-forward/origin.sqlite`.

```bash
SYNC_REPLICA=user@peer ./scripts/sync_library.sh --dry-run   # plan + volumes
SYNC_REPLICA=user@peer ./scripts/sync_library.sh
```

Access is the ordinary ssh key the origin already uses to reach the peer
(override with `SYNC_SSH_KEY`; prefer a jump host with `SYNC_SSH_JUMP`). If the
preferred jump is unavailable, the default fallback retries through the peer's
ordinary `~/.ssh/config` route; set `SYNC_SSH_FALLBACK_JUMP` to a second jump,
or to an empty value to disable fallback. These non-secret route preferences
may live in the gitignored `.env` or `.sync/config.env`. The sync selects a route once, keeps one SSH
connection alive across its rsync calls, retries the initial connection, and
resumes an interrupted SQLite download through rsync's checksum-verified delta
algorithm; override the default 15-minute control lifetime with
`SYNC_SSH_PERSIST`. Several replicas — one run per peer;
overlapping runs are serialized by flock. Cron on the origin, one line per
peer (daily at 04:17; the replica's zotero is down during its window):

```
17 4 * * * SYNC_REPLICA=user@peer $HOME/research-stack/scripts/sync_library.sh >>$HOME/research-stack/sync.log 2>&1
```

The first fill transfers the whole storage and establishes the three-way base;
later runs are deltas. Bring a
new peer's zotero container up only AFTER its first fill — a freshly created
empty DB against a full peer aborts the sync (the script explains what to
remove).

Verifying a sync: `scripts/library_manifest.sh [out.md]` (any peer; default
stdout; `ZOTERO_API` overrides `http://localhost:23119/api/users/0` — it reads
Zotero's local API on :23119, not the MCP plugin) dumps the entire library as
deterministic, diff-friendly markdown: every collection with its path and
count, every top-level item with key, type, creators, year and child
attachment/note tallies, plus unfiled and trash. Generate it on one peer,
ship the file (or keep it in a synced repo), regenerate on the other peer
after its sync window — a clean `diff` means the libraries match
key-for-key.

## What each MCP is good for

| MCP | Use it when you want me to… |
|---|---|
| `zotero` | search/read items, fetch metadata, get full text, extract PDF annotations, list/manage collections, add by DOI, generate bibliography, semantic search |
| `playwright` | navigate to a page, click, fill a form, scrape, take a screenshot, drive a multi-step browser flow |
| `chrome-devtools` | inspect network requests/responses, capture console errors, record a performance trace, analyze Core Web Vitals, snapshot the heap |
| (no MCP, just bash) | run any Python or shell in the container via `docker exec zotero ...` |

`playwright` and `chrome-devtools` attach to the **same** Chromium you see in the Selkies stream — open the URL in your browser, then ask me to interact with it.

## Security notes

Standing rule for the host: **nothing binds `0.0.0.0` except sshd (`:22`) and the system nginx (`:80`/`:443`)**. Every stack service binds `127.0.0.1` and is reached over an SSH tunnel — or, when a front-end is deliberately published, through the system nginx reverse proxy (TLS + auth) proxying from loopback. A default-deny firewall (`ufw`) stays on as the backstop, not the primary control; note that Docker *bridge*-published ports bypass ufw entirely, so any `ports:` mapping must be written `127.0.0.1:host:container`.

- **`network_mode: host`** removes the container/host network isolation — every in-container bind lands directly on a host interface. How each service is pinned to loopback:
  - Zotero web UI (`:8888`, http `:3000`): `docker/zotero/default.conf` mounted over `/defaults/default.conf` (the image's init regenerates the live nginx conf from it on every start), plus `DISABLE_IPV6=true` to drop the `[::]` listens.
  - Zotero MCP plugin (`:23120`): `allowRemote=false` in `user.js` — under host networking the container loopback *is* the host loopback, so the host still reaches it.
  - Open WebUI (`:4096`): `HOST=127.0.0.1`. searxng (`:9090`): `GRANIAN_HOST=127.0.0.1`. mcpo (`:9320`): `--host 127.0.0.1`. open-terminal: `run --host 127.0.0.1`. jupyter-mcp (`:4040`): bind sed-patched after install (upstream hardcodes 0.0.0.0). Chromium CDP (`:9222`) binds loopback by default.
  - Selkies data websocket (`:8082`): upstream hardcodes `0.0.0.0` with no CLI/env knob, so `docker/zotero/selkies-loopback.sh` (mounted into `/custom-cont-init.d`) seds the package source to loopback on every container start. The stream keeps working — the image's nginx proxies `/websocket` to `127.0.0.1:8082`. Do **not** try to disable the selkies service instead: it leads the desktop session that Zotero (and thus the MCP) and Chromium run in.
- The web UI on `:8888` is only reached via SSH tunnel; never expose it directly.
- **Secrets**: `.env` is gitignored. The token also ends up in `~/.claude.json` once you run `claude mcp add` — that file is per-user, not in the repo, but treat it like a secret store (don't commit it elsewhere, don't share screenshots of it).
- **Plugin write scopes** default to off. Re-enable selectively if you want me to modify your library.
- **`--remote-allow-origins=*` is intentionally *not* set** on Chromium — CDP is only reachable on `127.0.0.1:9222` which on host networking means localhost only.

## Gotchas worth knowing

**Chromium runs under XWayland, not native Wayland.** Chromium's Ozone-Wayland implementation has unreliable input handling under wlroots-based compositors like labwc — the symptom is a visible-but-unclickable window (popups appear, clicks fall through, title bar may be missing). `scripts/launch_chromium.sh` sets `--ozone-platform=x11` and `DISPLAY=:0` to route through the Xwayland server that labwc already runs. The CSD/SSD path under XWayland is much better-supported. The image's labwc defaults have `<windowRule identifier="chromium" serverDecoration="no" />` entries meant to disable server-side frames for native-Wayland chromium; under XWayland the WM_CLASS class is `Chromium` (capital) which doesn't match the lowercase `identifier`, so those rules silently no-op and we get the wildcard `*=yes` default for free. No labwc patching required as long as we stay on XWayland.

**The image regenerates labwc's rc.xml on every container start** from `/defaults/labwc.xml`, then applies a few env-driven sed tweaks (`NO_FULL=true` is set by default and strips the wildcard maximize rule). Editing `config/.config/labwc/rc.xml` directly is *not* persistent across container recreations. If you ever need to customize labwc here, bind-mount a replacement over `/defaults/labwc.xml:ro` in compose so the init `cp` picks up your version.

**Live reload of labwc**: after editing rc.xml at runtime, `docker exec zotero pkill -HUP labwc` reloads the config. Already-mapped windows keep their existing rules — rules are reapplied only when a window is newly created, so kill the affected app and relaunch it for changes to bite.

## Writing to Zotero over the MCP

How to attach a file, and the quirks the MCP layer carries. Script reference is `SCRIPTS.md`.

- **PDFs by URL** → use the MCP `import_attachment_url` tool. Pass `contentType: "application/pdf"` to skip the SingleFile snapshot path. Works for any HTTPS URL, including signed-token download URLs from logged-in browser sessions.
- **Local text/markdown/HTML files** → the MCP rejects `file://`, `127.0.0.1`, and RFC-1918 addresses, and binding a temp HTTP server to the public interface needs firewall coordination. Use `./scripts/zotero_attach_text.sh <itemKey> <file> [title] [tag1,tag2,...]` instead — it streams the file straight into `add_note` over the local MCP HTTP socket, so the contents never enter the model's context. The note ends up as a child of `itemKey`, wrapped in `<pre>` so whitespace renders.
- **Local PDFs (binary)** → `import_local_files(mcp, jobs, host_ip)` in `zotero_mcp.py` — copies each to a throwaway dir, serves it only for the import, and tears down. Literal RFC1918 URLs are rejected by the plugin, so private IPv4 hosts use `<ip>.nip.io` as a DNS alias back to the same machine; no file is sent to nip.io. `jobs` = `[(local_path, parentItemKey, title)]`.
- **Sanity-check downloads.** Zotero stores attachments at `config/Zotero/storage/<attachmentKey>/`. `file storage/<key>/*` quickly confirms whether you got a real PDF or a sign-in HTML page.

- **Zotero MCP body cap.** Requests over ~3700 bytes return `-32700 Parse error` (misleading). `discover.py`'s `cap_abstract` shrinks `abstractNote` until the payload fits, with `ensure_ascii=False` to keep non-Latin text compact — reuse it for any call that carries long text.
- **`get_collection_items` overflows the tool-result token limit at >~30 items.** The MCP auto-saves the full JSON to a file and tells you the path — `grep -oE '"key": "[A-Z0-9]+"'` that file instead of asking the model to read it whole.
- **`import_attachment_url` forbids literal loopback/private-IP URLs** (SSRF guard: `127.0.0.1`, `10/172.16/192.168` all rejected). `import_local_files` in `zotero_mcp.py` wraps the work-around: it stages only the requested files, serves them on a host address reachable from Zotero, and expresses a private IPv4 address as `<ip>.nip.io` so the bytes still travel directly between the two hosts; never serve the repo root (it holds unpublished drafts). For *text* (`.md`/`.txt`), skip all this — `zotero_attach_text.sh` attaches it as a child note (no server).
- **`ifExists` on `import_attachment_url`:** use `add` for many files → one parent (e.g. all standards onto one book record); use `skip` for one-file-per-item dedup. `skip` checks *any* same-content-type child, so it wrongly blocks the 2nd+ file on a multi-attachment parent.
- **`add_note` size cap.** Notes over the cap return HTTP 413 (Content Too Large) — attach the `.txt` summary instead of a huge `.md`.
- **`add_note` also 400s with `-32700 Parse error` on some inputs**, and it is *not* a size limit — 130 KB notes go through while a 5 KB one fails, and bisecting the same file gives a boundary that drifts with unrelated edits. Don't chase it: restructure the file (fold a small extract into a larger document that already attaches, or split into two notes) and move on. Budget one retry, not an investigation.
- **Cyrillic filenames break `import_attachment_url`** with `Invalid attachment URL` — the URL string is validated as ASCII. `import_local_files` now sanitises staging names, so the failure only reappears if you build the URL yourself. The attachment is titled from the `title` argument anyway, so the staged name never matters.
- **`search_library` with a collection-only filter throws `TypeError: value.includes`.** Use `get_collection_items` and grep its overflow file.
- **Verify Zotero writes via the SQLite DB, not the MCP.** `config/Zotero/zotero.sqlite` is host-visible; open read-only (`file:…?mode=ro&immutable=1`) and count `itemAttachments`/`itemNotes` by `parentItemID`. Far cheaper than `get_item_details` (which dumps every attached note body — millions of chars). MCP tool envelopes always contain `isError`, so a naive `"error" in response` success-check yields false negatives — check the DB.

## Troubleshooting

| Symptom | Check |
|---|---|
| `claude mcp list` shows zotero as ✗ | `docker exec zotero ss -tlnp \| grep 23120` — server up? Token correct? `curl -H "Authorization: Bearer $ZOTERO_MCP_TOKEN" http://127.0.0.1:23120/mcp -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}' -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream'` |
| Web UI is black / disconnects | Browser needs HTTPS for WebCodecs; accept the self-signed cert. `docker logs zotero \| grep -i error`. |
| Chromium MCPs can't connect | Run `./scripts/launch_chromium.sh` after every container restart. Verify `curl http://127.0.0.1:9222/json/version`. |
| Container won't start: "port already in use" | Something on the host is on 8888, 9222, 23120, or 23119. With host networking, the container can't relocate. Find and stop the culprit. |
| `docker exec zotero` runs but Chromium silently dies | Singleton lock. The launch script clears it; if you hand-launch, remove `config/.chromium-debug/Singleton*`. |
