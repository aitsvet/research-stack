# Windows host — the stack without Linux containers

`SETUP.md` describes the reference deployment: one Linux host, everything in a
`network_mode: host` Docker container with a Selkies-streamed Zotero desktop and
an in-container Chromium. This document is the other case — a **Windows
workstation where Docker/WSL2 is not installed (or not wanted)**. It sets up,
natively:

- the **Zotero desktop** with its local HTTP API and the MCP plugin, so the
  library is reachable at `127.0.0.1:23119` (local API) and `127.0.0.1:23120`
  (MCP);
- a **Chromium/Chrome you already use** exposed over CDP on `127.0.0.1:9222`,
  so the assistant drives the same session the user sees;
- the **Python pipeline** in `scripts/` — discovery, acquisition, text
  extraction, citation/quote checking, Markdown→docx/pdf export;
- the MCP servers wired into an AI client (OpenCode, Claude Code, Codex).

What natively **does not** come across is the container-only machinery: the
Selkies web UI, Ollama, Open WebUI/SearXNG/mcpo, JupyterLab, the LibreOffice
`docconv` and PlantUML containers, and the peer library sync. See *What still
needs Linux* at the end.

> Run every command from the repository root unless told otherwise. PowerShell
> is assumed; `bash` means **Git Bash** (`C:\Program Files\Git\bin\bash.exe`),
> not WSL.

## 1. Base tools

Install the ones you are missing. `winget` is the shortest route when it works;
the per-user installers need no administrator rights.

### Git for Windows — `git`, `bash`, `ssh`, `curl`

```powershell
winget install --id Git.Git -e
```

Git for Windows ships Git Bash, OpenSSH, `curl` and `ssh-keygen`. Windows also
ships its own `ssh`; either works.

### Python 3.11+ and the pipeline dependencies

```powershell
winget install --id Python.Python.3.12 -e
```

Or install from python.org with *"Add python.exe to PATH"* checked. Then:

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

`requirements.txt` pulls `pymupdf`/`pymupdf4llm` (PDF text layer), `beautifulsoup4`,
`Pillow`, `websocket-client` (raw CDP), `python-pptx` and `markdown` (export and
deck tooling). The pipeline itself is otherwise standard-library only —
`zotero_mcp.py` talks to the MCP over `urllib`, so it is fully cross-platform.

Optional: **Poppler** (`pdftotext`) on `PATH`. `extract_texts.py` prefers it and
falls back to `pymupdf` when it is absent, so this is a speed/quality choice,
not a requirement.

### Node.js 20+ — for the browser MCP servers

```powershell
winget install --id OpenJS.NodeJS.LTS -e
```

Without admin rights, download the Windows **ZIP** to a per-user directory and
add it to the user `PATH` (the archive contains `node.exe`, `npm`, `npx`).
Then either install the MCP servers once:

```powershell
npm install -g @playwright/mcp chrome-devtools-mcp
```

or let `npx -y <package>@latest` fetch them on first use (see §5).

### PuTTY — only if your Git key is a `.ppk`

A PuTTY-format private key cannot be read by OpenSSH or Git. Convert it (§2).

```powershell
winget install --id PuTTY.PuTTY -e
```

## 2. Git identity, SSH key, and cloning

### Key material

If you already have an OpenSSH key pair at `%USERPROFILE%\.ssh\id_ed25519`
(plus `id_ed25519.pub`), use it. If your only key is a PuTTY `.ppk`, export it
to OpenSSH format:

- **PuTTYgen GUI**: *Conversions → Export OpenSSH key* → save to
  `%USERPROFILE%\.ssh\id_ed25519_rs`. (Some PuTTY builds do not implement the
  `-O private-openssh` CLI flags and just open the GUI — use the GUI in that
  case.)
- **Equivalent CLI, when supported**:

  ```powershell
  puttygen "$env:USERPROFILE\key.ppk" -O private-openssh -o "$env:USERPROFILE\.ssh\id_ed25519_rs"
  ```

### Windows file permissions

OpenSSH refuses a private key that other accounts can read. Restrict it to you:

```powershell
icacls "$env:USERPROFILE\.ssh\id_ed25519_rs" /inheritance:r /grant:r "$($env:USERNAME):(R,W)"
```

### Point SSH (and therefore Git) at the key

```powershell
ssh -T git@github.com          # expect: "Hi <user>! You've successfully authenticated..."
```

If that fails because the key is not the default one, either add it to
`~/.ssh/config`:

```
Host github.com
    IdentityFile ~/.ssh/id_ed25519_rs
    IdentitiesOnly yes
```

or set it per machine for Git:

```powershell
git config --global core.sshCommand 'ssh -i "C:/Users/<you>/.ssh/id_ed25519_rs" -o IdentitiesOnly=yes'
```

Use **forward slashes and quotes** in `core.sshCommand`; the shell Git spawns
eats backslashes, and a mangled path silently degrades to "Permission denied
(publickey)".

### Clone

The repository is public, so HTTPS needs no credentials:

```powershell
git clone https://github.com/aitsvet/research-stack.git
cd research-stack
```

Use the SSH URL instead if you will push:

```powershell
git clone ssh://git@github.com/aitsvet/research-stack.git
```

Set a commit identity if Git has none yet:

```powershell
git config user.name  "<Your Name>"
git config user.email "<you>@example.org"
```

## 3. Zotero desktop, its local API, and the MCP plugin

1. Install **Zotero for Windows** from zotero.org (the installer needs
   elevation; the profile and data directories are per-user).

2. Enable the local API: *Edit → Settings → Advanced* → check
   **"Allow other applications on this computer to communicate with Zotero"**.
   Zotero then serves a read-only REST view of the library on
   `127.0.0.1:23119` (this is what `scripts/library_manifest.sh` reads).

3. Install the MCP plugin. Download the `.xpi` once and install it from
   *Tools → Add-ons → ⚙ → Install Add-on From File*, then restart Zotero:

   ```powershell
   curl.exe -L -o zotero-mcp.xpi https://github.com/lricher7329/zotero-mcp-claude-code/releases/latest/download/zotero-mcp-for-claude-code.xpi
   ```

4. Apply the repository's `user.js`. The container helper
   `scripts/apply_config.sh` is Docker-only; on Windows copy the file into the
   **active profile** by hand. Find the profile via *Help → Troubleshooting
   Information → Profile Directory* (typically
   `%APPDATA%\Zotero\Zotero\Profiles\<random>.default\`):

   ```powershell
   Copy-Item .\user.js "$env:APPDATA\Zotero\Zotero\Profiles\<profile>.default\user.js"
   ```

   `user.js` pins the MCP server to loopback (`allowRemote=false`), turns
   authentication on, enables all write scopes, and enables the local API.
   Restart Zotero so the prefs take effect.

5. Read the bearer token the plugin generated out of `prefs.js` in the same
   profile and store it as a secret (put it in `.env`, which is gitignored):

   ```powershell
   Select-String -Path "$env:APPDATA\Zotero\Zotero\Profiles\<profile>.default\prefs.js" `
     -Pattern 'mcp.server.authToken'
   ```

6. Verify both surfaces:

   ```powershell
   curl.exe -s http://127.0.0.1:23119/connector/ping          # "Zotero is running"
   curl.exe -s -H "Zotero-API-Version: 3" "http://127.0.0.1:23119/api/users/0/items?limit=1"
   ```

   > Use `curl.exe`, not PowerShell's `Invoke-WebRequest`: the connector answers
   > over HTTP/1.0 without a `Content-Length`, and `Invoke-WebRequest` fails on
   > that response even though the server is healthy.

Zotero's **data directory** (the SQLite DB and PDF storage) defaults to
`%USERPROFILE%\Zotero` — `zotero.sqlite` plus `storage\`. That is the value the
pipeline expects in `ZOTERO_STORAGE` (§6).

### Zotero Connector in Chrome (optional)

The Connector saves the page you are viewing into Zotero. Install it from the
Chrome Web Store. To have Chrome install it without clicks, add an
external-extension key under your own hive (this survives Chrome
reinstalls but is a workaround, not a supported flow):

```powershell
$id = 'ekhagklcjbdpajgpjgmbionohlpdbjgc'     # Zotero Connector
New-Item -Path "HKCU:\Software\Google\Chrome\Extensions\$id" -Force | Out-Null
Set-ItemProperty -Path "HKCU:\Software\Google\Chrome\Extensions\$id" `
  -Name update_url `
  -Value "https://clients2.google.com/service/update2/crx?response=update&x=id%3D$id%26uc"
```

Restart Chrome; the extension is fetched at startup. The Connector talks to the
running Zotero through `127.0.0.1:23119` — no native-messaging host is involved
in the current Connector.

## 4. Chrome with CDP on `:9222`

A CDP port is what both browser MCPs attach to. **Branded Chrome 136+ silently
ignores `--remote-debugging-port` when launched on the default profile** (a
security change), so a plain `chrome.exe --remote-debugging-port=9222` yields no
port. Choose one of:

**A. A separate debugging profile (simplest).** An empty profile — none of your
logins or extensions:

```powershell
Start-Process "C:\Program Files\Google\Chrome\Application\chrome.exe" -ArgumentList `
  "--user-data-dir=$env:LOCALAPPDATA\ChromeCDP", "--remote-debugging-port=9222"
```

**B. Your real profile through a directory junction.** Chrome sees a different
path while reading the same data, so the port opens and your session is intact.
With Chrome fully closed, create the junction once:

```powershell
mklink /J "$env:LOCALAPPDATA\ChromeCDP-UserData" "$env:LOCALAPPDATA\Google\Chrome\User Data"
```

then launch (and close any other instance first):

```powershell
Stop-Process -Name chrome -Force -ErrorAction SilentlyContinue
Start-Process "C:\Program Files\Google\Chrome\Application\chrome.exe" -ArgumentList `
  "--user-data-dir=$env:LOCALAPPDATA\ChromeCDP-UserData",
  "--remote-debugging-port=9222", "--restore-last-session"
```

Verify in both cases:

```powershell
curl.exe -s http://127.0.0.1:9222/json/version
```

Do **not** add `--remote-allow-origins=*`: CDP here is loopback-only, and the
flag only widens the guard. If `:9222` is taken, pick another free port and use
it everywhere.

## 5. MCP servers in the AI client

### OpenCode (V2)

Servers live under `mcp.servers` — in the project's `opencode.jsonc`, or in
`~/.config/opencode/opencode.jsonc` for every project. `opencode mcp add` writes
this for you; by hand it looks like:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "playwright": {
        "type": "local",
        "command": ["npx", "-y", "@playwright/mcp@latest", "--cdp-endpoint", "http://127.0.0.1:9222"]
      },
      "chrome-devtools": {
        "type": "local",
        "command": ["npx", "-y", "chrome-devtools-mcp@latest", "--browser-url", "http://127.0.0.1:9222"]
      },
      "zotero": {
        "type": "remote",
        "url": "http://127.0.0.1:23120/mcp",
        "headers": { "Authorization": "Bearer {env:ZOTERO_MCP_TOKEN}" }
      }
    }
  }
}
```

- If `npx` is not resolved by the background service, use the absolute
  executable and the package entry point, e.g.
  `"command": ["C:\\Users\\<you>\\AppData\\Local\\Programs\\nodejs\\node.exe", "C:\\Users\\<you>\\AppData\\Local\\Programs\\nodejs\\node_modules\\@playwright\\mcp\\cli.js", "--cdp-endpoint", "http://127.0.0.1:9222"]`.
  A per-user Node install added to `PATH` after the service started will not be
  seen by that long-lived process.
- `{env:ZOTERO_MCP_TOKEN}` is substituted from the environment of whatever
  starts OpenCode. Launch it from a shell where the token is set (see §6), or
  set it in the user environment.
- Check with `opencode mcp list`, or `/mcps` inside the TUI.

### Claude Code and Codex

The committed `.mcp.json` configures the two **secret-free** browser servers
(Claude Code reads it at project scope). Add the Zotero entry once with the
token, so it never enters the repository:

```bash
set -a; source .env; set +a
claude mcp add zotero http://127.0.0.1:23120/mcp -t http \
  --header "Authorization: Bearer $ZOTERO_MCP_TOKEN"
```

For Codex, append the equivalent `[mcp_servers.*]` blocks to
`~/.codex/config.toml` (see `SETUP.md` for the shapes).

## 6. Environment variables and the Python pipeline

Set these before running any pipeline script (Git Bash `source .env`, or
PowerShell `$env:...`, or the user environment):

| Variable | Windows value / default | Used by |
|---|---|---|
| `ZOTERO_MCP_TOKEN` | the plugin's bearer token | every script importing `zotero_mcp` |
| `ZOTERO_MCP_URL` | `http://127.0.0.1:23120/mcp` | `zotero_mcp.py` |
| `ZOTERO_STORAGE` | `%USERPROFILE%\Zotero\storage` | `extract_texts.py` (PDF location) |
| `ZOTERO_API` | `http://localhost:23119/api/users/0` | `library_manifest.sh` |
| `DISCOVERY_OUT` | e.g. `<project>\discovery\<slug>` | `discover.py`, `dump_abstracts.py`, `extract_texts.py` |
| `OPENALEX_EMAIL`, `UNPAYWALL_EMAIL` | a real contact address | `discover.py`, `retry_unpaywall.py`, `verify_refs.py` |
| `CDP_URL` | `http://127.0.0.1:9222` | `cdp_eval.py` |

```powershell
$env:ZOTERO_MCP_TOKEN = '<token>'
$env:ZOTERO_STORAGE   = "$env:USERPROFILE\Zotero\storage"
$env:DISCOVERY_OUT    = "$HOME\research-stack\.discovery"
```

The pipeline is the same as on Linux — see `SCRIPTS.md` for the catalogue. The
two-pass rule holds everywhere: `discover.py` creates candidates and never
attaches PDFs; `acquire.py` fetches full text only for rows you picked.

```powershell
python scripts\discover.py topics.json
python scripts\dump_abstracts.py <collectionKey>
python scripts\extract_texts.py <collectionKey>
python scripts\extract_texts.py check .\discovery\<slug>\text
python scripts\verify_refs.py draft.md --per-line
python scripts\check_citations.py draft.md
python scripts\prose_lint.py draft.md --config prose_lint.json
python scripts\md_docx.py paper.md --template template.docx --out out.docx --check
```

### Shell helpers

The `*.sh` wrappers run under **Git Bash**:

```powershell
& "C:\Program Files\Git\bin\bash.exe" -lc "cd /c/path/to/research-stack && ./scripts/library_manifest.sh out.md"
```

`library_manifest.sh` needs `curl` and `jq` (Git Bash has `curl`; install `jq`).
`zotero_attach_text.sh` invokes `python3`, which may need a `python3` alias to
`python` in Git Bash. The wrappers that drive the **container** Chromium or
LibreOffice — `fetch_pdf.sh`, `md_pdf.sh`, `export_paper.sh`,
`launch_chromium.sh`, `apply_config.sh`, `unpack_archives.sh` — need the Docker
stack and are out of scope for a containerless Windows host.

## 7. Verification checklist

| Check | Command | Expected |
|---|---|---|
| Python + deps | `python -c "import fitz, bs4, websocket, pptx; print('ok')"` | `ok` |
| Node | `node --version` | `v20` or newer |
| Git/bash | `& "C:\Program Files\Git\bin\bash.exe" --version` | `bash ...` |
| SSH to GitHub | `ssh -T git@github.com` | `Hi <user>! You've successfully authenticated` |
| Zotero running | `curl.exe -s http://127.0.0.1:23119/connector/ping` | `Zotero is running` |
| Zotero local API | `curl.exe -s -H "Zotero-API-Version: 3" "http://127.0.0.1:23119/api/users/0/items?limit=1"` | JSON array |
| Zotero MCP | `curl.exe -s -H "Authorization: Bearer <token>" http://127.0.0.1:23120/mcp` | an MCP/JSON-RPC response, not `401` |
| Chrome CDP | `curl.exe -s http://127.0.0.1:9222/json/version` | `{"Browser": "Chrome/..."}` |
| MCP wiring | `opencode mcp list` | `playwright`, `chrome-devtools`, `zotero` connected |

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Permission denied (publickey)` when `ssh -T git@github.com` works | Git's `core.sshCommand` path has backslashes or wrong quoting. Use forward slashes and quotes (§2). |
| `WARNING: UNPROTECTED PRIVATE KEY FILE` | Key readable by others. `icacls ... /inheritance:r /grant:r "$($env:USERNAME):(R,W)"`. |
| No CDP port after launching Chrome | Chrome 136+ ignores `--remote-debugging-port` on the default profile. Use a dedicated `--user-data-dir` or a junction (§4). |
| CDP port occupied | Another Chrome/tunnel holds `:9222`. `Get-NetTCPConnection -LocalPort 9222` to find the owner, or launch on another port. |
| Chromium dies on relaunch | Singleton lock in the reused data dir (`SingletonLock`/`Cookie`/`Socket`). Delete them, or close all instances first. |
| `Invoke-WebRequest` fails against Zotero | HTTP/1.0 response without `Content-Length`. Use `curl.exe`. |
| `where`/`Get-Command` cannot find a tool a service needs | The background service inherited a stale `PATH`. Use absolute executable paths in MCP `command` arrays. |
| `npx` not found by the MCP server | Same PATH issue, or Node not installed. Prefer the absolute `node.exe` + `cli.js` form (§5). |
| `extract_texts.py` reports "NO PDF FOUND" for everything | `ZOTERO_STORAGE` points at the container path. Set it to `%USERPROFILE%\Zotero\storage`. |
| Ports `8888`/`8082`/`11434` unused | Expected: those belong to the Docker stack, not the Windows path. |

## 9. What still needs Linux (the Docker stack)

These are provided only by `docker-compose.yml` and are intentionally absent
from the Windows-native path:

| Facility | Container service | Note |
|---|---|---|
| Zotero web UI (Selkies/WebRTC stream) | `zotero` | Windows uses the Zotero desktop directly |
| Ollama local models | `ollama` | native Windows builds of Ollama exist, but the compose wiring does not |
| Open WebUI + SearXNG + mcpo + open-terminal | `extra` profile | Linux containers |
| JupyterLab + `jupyter-mcp-server` | `jupyter/` | Linux notebook stack |
| LibreOffice document→Markdown (`docconv`) | `tools` profile | native LibreOffice + `SOFFICE_BIN` would substitute, but `export_paper.sh`/`unpack_archives.sh` drive the container |
| PlantUML rendering | `tools` profile | needs a JRE + Graphviz locally to replace it |
| Peer Zotero library sync | `scripts/sync_library.sh` | needs `rsync`, `ssh`, bash sockets, and the `zotero` container |

Everything else — Zotero, its local API and MCP plugin, the CDP browser, the
browser MCPs, and the Python pipeline — runs on Windows without a container.
