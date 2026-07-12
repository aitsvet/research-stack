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
| `scripts/` (the rest) | yes | Research discovery/acquisition/extraction pipeline — catalogued in `AGENTS.md` |
| `requirements.txt` | yes | Python deps for the pipeline scripts (`.venv/bin/pip install -r`) |
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
cd ~/zotero-setup
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
# Codex currently has no HTTP-transport MCP support; bridge via a stdio proxy.
command = "npx"
args    = ["-y", "mcp-remote", "http://127.0.0.1:23120/mcp",
           "--header", "Authorization: Bearer ${ZOTERO_MCP_TOKEN}"]
```

Set `ZOTERO_MCP_TOKEN` in your shell before launching codex.

## Daily operation

| Action | Command |
|---|---|
| Start stack | `docker compose up -d && ./scripts/launch_chromium.sh` |
| Stop stack | `docker compose down` (Chromium dies with the container) |
| Restart Chromium only | `./scripts/launch_chromium.sh` |
| Tail Zotero logs | `docker logs -f zotero` |
| Tail Chromium logs | `docker exec zotero tail -f /tmp/chromium.log` |
| Web UI tunnel | `ssh -L 8888:localhost:8888 <vm>` |

## What each MCP is good for

| MCP | Use it when you want me to… |
|---|---|
| `zotero` | search/read items, fetch metadata, get full text, extract PDF annotations, list/manage collections, add by DOI, generate bibliography, semantic search |
| `playwright` | navigate to a page, click, fill a form, scrape, take a screenshot, drive a multi-step browser flow |
| `chrome-devtools` | inspect network requests/responses, capture console errors, record a performance trace, analyze Core Web Vitals, snapshot the heap |
| (no MCP, just bash) | run any Python or shell in the container via `docker exec zotero ...` |

`playwright` and `chrome-devtools` attach to the **same** Chromium you see in the Selkies stream — open the URL in your browser, then ask me to interact with it.

## Security notes

- **`network_mode: host`** removes the container/host network isolation. Anything the container binds to `0.0.0.0` is exposed on every host interface. Make sure:
  - The Zotero MCP plugin binds `127.0.0.1` (or auth is required *and* the host is firewalled).
  - The web UI on `:8888` is only reached via SSH tunnel; do not expose to the public Internet without a real reverse proxy + cert.
  - The host has a firewall (`ufw deny in on eth0 to any port 8888,23120,9222`) if it has a public IP.
- **Secrets**: `.env` is gitignored. The token also ends up in `~/.claude.json` once you run `claude mcp add` — that file is per-user, not in the repo, but treat it like a secret store (don't commit it elsewhere, don't share screenshots of it).
- **Plugin write scopes** default to off. Re-enable selectively if you want me to modify your library.
- **`--remote-allow-origins=*` is intentionally *not* set** on Chromium — CDP is only reachable on `127.0.0.1:9222` which on host networking means localhost only.

## Gotchas worth knowing

**Chromium runs under XWayland, not native Wayland.** Chromium's Ozone-Wayland implementation has unreliable input handling under wlroots-based compositors like labwc — the symptom is a visible-but-unclickable window (popups appear, clicks fall through, title bar may be missing). `scripts/launch_chromium.sh` sets `--ozone-platform=x11` and `DISPLAY=:0` to route through the Xwayland server that labwc already runs. The CSD/SSD path under XWayland is much better-supported. The image's labwc defaults have `<windowRule identifier="chromium" serverDecoration="no" />` entries meant to disable server-side frames for native-Wayland chromium; under XWayland the WM_CLASS class is `Chromium` (capital) which doesn't match the lowercase `identifier`, so those rules silently no-op and we get the wildcard `*=yes` default for free. No labwc patching required as long as we stay on XWayland.

**The image regenerates labwc's rc.xml on every container start** from `/defaults/labwc.xml`, then applies a few env-driven sed tweaks (`NO_FULL=true` is set by default and strips the wildcard maximize rule). Editing `config/.config/labwc/rc.xml` directly is *not* persistent across container recreations. If you ever need to customize labwc here, bind-mount a replacement over `/defaults/labwc.xml:ro` in compose so the init `cp` picks up your version.

**Live reload of labwc**: after editing rc.xml at runtime, `docker exec zotero pkill -HUP labwc` reloads the config. Already-mapped windows keep their existing rules — rules are reapplied only when a window is newly created, so kill the affected app and relaunch it for changes to bite.

## Troubleshooting

| Symptom | Check |
|---|---|
| `claude mcp list` shows zotero as ✗ | `docker exec zotero ss -tlnp \| grep 23120` — server up? Token correct? `curl -H "Authorization: Bearer $ZOTERO_MCP_TOKEN" http://127.0.0.1:23120/mcp -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}' -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream'` |
| Web UI is black / disconnects | Browser needs HTTPS for WebCodecs; accept the self-signed cert. `docker logs zotero \| grep -i error`. |
| Chromium MCPs can't connect | Run `./scripts/launch_chromium.sh` after every container restart. Verify `curl http://127.0.0.1:9222/json/version`. |
| Container won't start: "port already in use" | Something on the host is on 8888, 9222, 23120, or 23119. With host networking, the container can't relocate. Find and stop the culprit. |
| `docker exec zotero` runs but Chromium silently dies | Singleton lock. The launch script clears it; if you hand-launch, remove `config/.chromium-debug/Singleton*`. |
