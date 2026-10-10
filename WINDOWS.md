# Windows host — the stack without Linux containers

`SETUP.md` describes the reference deployment: one Linux host, everything in a
`network_mode: host` Docker container with a Selkies-streamed Zotero desktop and
an in-container Chromium. This document is the other case — a **Windows
workstation where Docker/WSL2 is not installed (or not wanted)**. It runs
natively:

- the **Zotero desktop** with its local HTTP API (`127.0.0.1:23119`) and MCP
  plugin (`127.0.0.1:23120`), so the library is reachable to the assistant;
- a **Chrome you control** exposed over CDP on `127.0.0.1:9222`, so the
  assistant drives the same session you see;
- the **Python pipeline** in `scripts/` — discovery, acquisition, text
  extraction, citation/quote checking, Markdown→docx/pdf export;
- the **MCP servers** wired into an AI client (OpenCode, Claude Code, Codex).

What natively **does not** come across is the container-only machinery: the
Selkies web UI, Ollama, Open WebUI/SearXNG/mcpo, JupyterLab, the LibreOffice
`docconv` and PlantUML containers, and the peer library sync. See *What still
needs Linux* at the end.

**This file is a checklist.** §1 checks what you already have, §2 turns that
into a plan, §3 lists the three choices you have to make, and §4 is the install
list — do only the items §1 flagged, not all of them. Commands run from the
repository root; PowerShell is assumed, and `bash` means **Git Bash**
(`C:\Program Files\Git\bin\bash.exe`), not WSL.

> **For an AI agent handed only this repository's URL:** run the §1 block,
> present the §2 summary and the §3 choices to the user, then execute the §4
> items they picked, verifying each with its **Done when** line.

## 1. Preflight — what is already installed

Paste this whole block into PowerShell. It changes nothing; it only reports.
`present` is usable as-is, `MISSING` needs the matching §4 item.

```powershell
$ErrorActionPreference = 'SilentlyContinue'
$rows = [System.Collections.Generic.List[object]]::new()
function Add($tool, $ok, $detail) {
  $rows.Add([pscustomobject]@{ Tool = $tool; Status = $(if ($ok) { 'present' } else { 'MISSING' }); Detail = $detail })
}

$git = Get-Command git
Add 'git' $git $(if ($git) { $git.Source } else { 'install Git for Windows' })

$bash = 'C:\Program Files\Git\bin\bash.exe'
Add 'Git Bash' (Test-Path $bash) $(if (Test-Path $bash) { $bash } else { 'comes with Git for Windows' })

$py = Get-Command python
Add 'python' $py $(if ($py) { (python --version 2>&1) } else { 'install Python 3.11+' })

$deps = $false
if ($py) { python -c 'import fitz,bs4,websocket,pptx,markdown' 2>$null; $deps = ($LASTEXITCODE -eq 0) }
Add 'pipeline deps' $deps $(if ($deps) { 'ok' } else { 'pip install -r requirements.txt' })

$node = Get-Command node
if (-not $node -and (Test-Path "$env:LOCALAPPDATA\Programs\nodejs\node.exe")) { $node = "$env:LOCALAPPDATA\Programs\nodejs\node.exe" }
Add 'node' $node $(if ($node) { & $node --version } else { 'install Node 20+' })

Add 'jq' (Get-Command jq) $(if (Get-Command jq) { 'ok' } else { 'optional (library_manifest.sh)' })
Add 'pdftotext' (Get-Command pdftotext) $(if (Get-Command pdftotext) { 'ok' } else { 'optional (pymupdf fallback)' })
Add 'LibreOffice' (Test-Path 'C:\Program Files\LibreOffice\program\soffice.exe') 'optional (convert_office.py)'

Add 'Zotero' (Test-Path 'C:\Program Files\Zotero\zotero.exe') 'C:\Program Files\Zotero\zotero.exe'
$ping = & curl.exe -s --max-time 2 http://127.0.0.1:23119/connector/ping 2>$null
Add 'Zotero local API' ($ping -match 'Zotero is running') $(if ($ping -match 'Zotero is running') { 'up on :23119' } else { 'start Zotero / enable local API' })
$mcp = & curl.exe -s -o NUL -w '%{http_code}' --max-time 2 http://127.0.0.1:23120/mcp 2>$null
Add 'Zotero MCP' ($mcp -and $mcp -ne '000') $(if ($mcp -and $mcp -ne '000') { "HTTP $mcp on :23120" } else { 'install/enable the MCP plugin' })

$chrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
if (-not (Test-Path $chrome)) { $chrome = "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe" }
Add 'Chrome' (Test-Path $chrome) $(if (Test-Path $chrome) { $chrome } else { 'install Chrome' })
$cdp = & curl.exe -s --max-time 2 http://127.0.0.1:9222/json/version 2>$null
Add 'Chrome CDP' ($cdp -match '"Browser"') $(if ($cdp -match '"Browser"') { 'up on :9222' } else { 'launch Chrome with --remote-debugging-port' })

Add 'Docker' (Get-Command docker) $(if (Get-Command docker) { 'present' } else { 'not needed for Windows' })

$rows | Format-Table -AutoSize
```

## 2. Turn the table into a plan

1. Every `MISSING` row maps to one item in §4 — do those, skip the `present`
   rows. `jq`, `pdftotext` and LibreOffice are optional: install them only if
   you need the feature named in the last column.
2. Two rows reflect **runtime state, not files**, so the preflight cannot make
   them green — you switch them on once:
   - `Zotero local API` / `Zotero MCP` → done in §4.5.
   - `Chrome CDP` → done in §4.6 (the port is up only while Chrome runs).
3. MCP **registration** (the last step) is per AI client and is not something
   the preflight can see — §4.8.
4. Nothing here needs Docker. The `Docker` row is informational only.

## 3. Choices

Three choices; each has a default. Everything else is install-or-skip.

| # | Decision | Options | Default |
|---|---|---|---|
| 1 | How to clone | **HTTPS** — no keys, no account · SSH — only if you will *push* | HTTPS |
| 2 | Which Chrome gets CDP | a **dedicated debug profile** — clean, none of your logins · your **real profile via a directory junction** — the session you actually use | dedicated |
| 3 | Which AI client to register the MCP servers with | OpenCode · Claude Code · Codex | whichever you already run |

If you never push to the repository, choice 1 stays HTTPS and you can **skip
§5 (SSH keys) entirely**.

## 4. Setup checklist

Each item ends with a **Done when** line — a command whose output proves it.
Work in order; later items verify earlier ones.

### 4.1 [ ] Clone the repository

```powershell
git clone https://github.com/aitsvet/research-stack.git
cd research-stack
```

**Done when** `git log --oneline -1` prints a commit. Git is public over HTTPS,
so there is nothing to authenticate.

To push later, use the SSH URL instead and do §5:
`git remote set-url origin ssh://git@github.com/aitsvet/research-stack.git`.

Set a commit identity if Git has none:

```powershell
git config user.name  "<Your Name>"
git config user.email "<you>@example.org"
```

### 4.2 [ ] Git for Windows — `git`, `bash`, `ssh`, `curl`

```powershell
winget install --id Git.Git -e
```

That ships Git Bash (`C:\Program Files\Git\bin\bash.exe`), OpenSSH, `curl.exe`
and `ssh-keygen`. **Done when** `& "C:\Program Files\Git\bin\bash.exe" --version`
prints a version.

### 4.3 [ ] Python 3.11+ and the pipeline dependencies

```powershell
winget install --id Python.Python.3.12 -e
```

Or install from python.org with *"Add python.exe to PATH"* checked. Then:

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

`requirements.txt` pulls `pymupdf`/`pymupdf4llm` (PDF text layer),
`beautifulsoup4`, `Pillow`, `websocket-client` (raw CDP), `python-pptx` and
`markdown`. The pipeline is otherwise standard-library only — `zotero_mcp.py`
talks to the MCP over `urllib`, so it is fully cross-platform.

Optional: **Poppler** (`pdftotext`) on `PATH`. `extract_texts.py` prefers it and
falls back to `pymupdf` when absent — a speed/quality choice, not a requirement.

**Done when** `python -c "import fitz, bs4, websocket, pptx; print('ok')"` prints
`ok`.

### 4.4 [ ] Node.js 20+ — only for the browser MCP servers

Skip this if you will not use `playwright`/`chrome-devtools`.

```powershell
winget install --id OpenJS.NodeJS.LTS -e
```

Without admin rights, download the Windows **ZIP** to a per-user directory and
add it to the user `PATH` (the archive contains `node.exe`, `npm`, `npx`), then
either install the servers once:

```powershell
npm install -g @playwright/mcp chrome-devtools-mcp
```

or let `npx -y <package>@latest` fetch them on first use (§7).

**Done when** `node --version` prints `v20` or newer.

### 4.5 [ ] Zotero desktop, its local API, and the MCP plugin

1. Install **Zotero for Windows** from zotero.org (the installer needs
   elevation; profile and data live per-user).
2. Enable the local API: *Edit → Settings → Advanced* → check **"Allow other
   applications on this computer to communicate with Zotero"**. Zotero then
   serves a read-only REST view on `127.0.0.1:23119`.
3. Install the MCP plugin — download the `.xpi` once and install it from
   *Tools → Add-ons → ⚙ → Install Add-on From File*, then restart Zotero:

   ```powershell
   curl.exe -L -o zotero-mcp.xpi https://github.com/lricher7329/zotero-mcp-claude-code/releases/latest/download/zotero-mcp-for-claude-code.xpi
   ```

4. Apply this repository's `user.js`. The Docker helper `scripts/apply_config.sh`
   is container-only; on Windows copy the file into the **active profile** by
   hand. Find the profile via *Help → Troubleshooting Information → Profile
   Directory* (typically `%APPDATA%\Zotero\Zotero\Profiles\<random>.default\`):

   ```powershell
   Copy-Item .\user.js "$env:APPDATA\Zotero\Zotero\Profiles\<profile>.default\user.js"
   ```

   `user.js` pins the MCP server to loopback (`allowRemote=false`), turns
   authentication on, enables all write scopes, and enables the local API.
   Restart Zotero so the prefs take effect.
5. Read the bearer token the plugin generated out of `prefs.js` in the same
   profile and store it as a secret in `.env` (gitignored):

   ```powershell
   Select-String -Path "$env:APPDATA\Zotero\Zotero\Profiles\<profile>.default\prefs.js" `
     -Pattern 'mcp.server.authToken'
   ```

**Done when** both of these answer:

```powershell
curl.exe -s http://127.0.0.1:23119/connector/ping          # "Zotero is running"
curl.exe -s -H "Zotero-API-Version: 3" "http://127.0.0.1:23119/api/users/0/items?limit=1"
```

> Use `curl.exe`, not PowerShell's `Invoke-WebRequest`: the connector answers
> over HTTP/1.0 without a `Content-Length`, and `Invoke-WebRequest` fails on
> that response even though the server is healthy.

Zotero's **data directory** (SQLite DB + PDF storage) defaults to
`%USERPROFILE%\Zotero` — `zotero.sqlite` plus `storage\`. That is the value the
pipeline expects in `ZOTERO_STORAGE` (§8).

**Optional: Zotero Connector in Chrome.** The Connector saves the page you are
viewing into Zotero; install it from the Chrome Web Store. To have Chrome fetch
it without clicks, add an external-extension key under your own hive (survives
Chrome reinstalls, but it is a workaround, not a supported flow):

```powershell
$id = 'ekhagklcjbdpajgpjgmbionohlpdbjgc'     # Zotero Connector
New-Item -Path "HKCU:\Software\Google\Chrome\Extensions\$id" -Force | Out-Null
Set-ItemProperty -Path "HKCU:\Software\Google\Chrome\Extensions\$id" `
  -Name update_url `
  -Value "https://clients2.google.com/service/update2/crx?response=update&x=id%3D$id%26uc"
```

Restart Chrome; the extension is fetched at startup. The Connector reaches the
running Zotero through `127.0.0.1:23119` — no native-messaging host is involved
in the current Connector.

### 4.6 [ ] Chrome with CDP on `:9222`

Publisher-branded **Chrome 136+ silently ignores `--remote-debugging-port` on the
default profile**, so a plain `chrome.exe --remote-debugging-port=9222` opens no
port. Pick one of the two options in §6 (choice 2 above), launch, and confirm:

```powershell
curl.exe -s http://127.0.0.1:9222/json/version
```

**Done when** that prints `{"Browser": "Chrome/..."}`. Keep Chrome running — the
port exists only while it does.

### 4.7 [ ] Give the MCP calls a token to authenticate with

Set the environment variables the pipeline and the MCP client expect (§8) — at
minimum `ZOTERO_MCP_TOKEN` from §4.5. **Done when**

```powershell
curl.exe -s -H "Authorization: Bearer $env:ZOTERO_MCP_TOKEN" http://127.0.0.1:23120/mcp
```

does not return `401`.

### 4.8 [ ] Register the MCP servers in your AI client

Add the `playwright`, `chrome-devtools` and `zotero` entries described in §7 to
your client (choice 3). **Done when** `opencode mcp list` (or the client's
equivalent) shows all three connected.

## 5. SSH keys — only if you will push

Git over HTTPS needs none of this. If you do push, GitHub must recognise a key
on your machine. Pick the sentence that matches you:

- **You already have an OpenSSH key and added its `.pub` to GitHub** — test it:

  ```powershell
  ssh -T git@github.com     # expect: "Hi <user>! You've successfully authenticated..."
  ```

  If it says that, you are done; `git push` over the SSH URL just works.
- **Your only key is a PuTTY `.ppk`** — convert it once with **PuTTYgen
  (GUI)**: *File → Load* the `.ppk`, then *Conversions → Export OpenSSH key* →
  save as `%USERPROFILE%\.ssh\id_ed25519`. (Some PuTTY builds do not implement
  the `-O private-openssh` CLI flag and just open the GUI — use the GUI.)
- **You have no key** — create one and register it:

  ```powershell
  ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\id_ed25519" -C "github"
  ```

  Add the **public** half (`id_ed25519.pub`) at GitHub → *Settings → SSH and GPG
  keys → New SSH key*.

Two Windows-specific gotchas:

- **File permissions.** OpenSSH refuses a private key other accounts can read.
  Restrict it to you once and it stays fixed:

  ```powershell
  icacls "$env:USERPROFILE\.ssh\id_ed25519" /inheritance:r /grant:r "$($env:USERNAME):(R,W)"
  ```

- **Non-default key name.** If your key is not `id_ed25519`, point SSH at it in
  `%USERPROFILE%\.ssh\config`:

  ```
  Host github.com
      IdentityFile ~/.ssh/id_ed25519
      IdentitiesOnly yes
  ```

  (Or per machine for Git:
  `git config --global core.sshCommand 'ssh -i "C:/Users/<you>/.ssh/id_ed25519" -o IdentitiesOnly=yes'`
  — use **forward slashes and quotes**; the shell Git spawns eats backslashes
  and a mangled path silently degrades to `Permission denied (publickey)`.)

Then switch the remote: `git remote set-url origin ssh://git@github.com/aitsvet/research-stack.git`.

## 6. Chrome over CDP — the two options

Both browser MCPs attach to a CDP port. Choose per §3.

**A. A dedicated debugging profile (simplest).** An empty profile — none of your
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

## 7. Registering the MCP servers in your AI client

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
  executable and package entry point, e.g.
  `"command": ["C:\\Users\\<you>\\AppData\\Local\\Programs\\nodejs\\node.exe", "C:\\Users\\<you>\\AppData\\Local\\Programs\\nodejs\\node_modules\\@playwright\\mcp\\cli.js", "--cdp-endpoint", "http://127.0.0.1:9222"]`.
  A per-user Node install added to `PATH` after the service started will not be
  seen by that long-lived process.
- `{env:ZOTERO_MCP_TOKEN}` is substituted from the environment of whatever
  starts OpenCode. Launch from a shell where the token is set (§8), or set it in
  the user environment.
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

## 8. Environment variables and the Python pipeline

Set these before running any pipeline script (Git Bash `source .env`, PowerShell
`$env:...`, or the user environment):

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

## 9. Verification checklist

| Check | Command | Expected |
|---|---|---|
| Python + deps | `python -c "import fitz, bs4, websocket, pptx; print('ok')"` | `ok` |
| Node | `node --version` | `v20` or newer |
| Git/bash | `& "C:\Program Files\Git\bin\bash.exe" --version` | `bash ...` |
| SSH to GitHub (push only) | `ssh -T git@github.com` | `Hi <user>! You've successfully authenticated` |
| Zotero running | `curl.exe -s http://127.0.0.1:23119/connector/ping` | `Zotero is running` |
| Zotero local API | `curl.exe -s -H "Zotero-API-Version: 3" "http://127.0.0.1:23119/api/users/0/items?limit=1"` | JSON array |
| Zotero MCP | `curl.exe -s -H "Authorization: Bearer <token>" http://127.0.0.1:23120/mcp` | an MCP/JSON-RPC response, not `401` |
| Chrome CDP | `curl.exe -s http://127.0.0.1:9222/json/version` | `{"Browser": "Chrome/..."}` |
| MCP wiring | `opencode mcp list` | `playwright`, `chrome-devtools`, `zotero` connected |

## 10. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Permission denied (publickey)` when `ssh -T git@github.com` works | Git's `core.sshCommand` path has backslashes or wrong quoting. Use forward slashes and quotes (§5). |
| `WARNING: UNPROTECTED PRIVATE KEY FILE` | Key readable by others. `icacls ... /inheritance:r /grant:r "$($env:USERNAME):(R,W)"`. |
| No CDP port after launching Chrome | Chrome 136+ ignores `--remote-debugging-port` on the default profile. Use a dedicated `--user-data-dir` or a junction (§6). |
| CDP port occupied | Another Chrome/tunnel holds `:9222`. `Get-NetTCPConnection -LocalPort 9222` to find the owner, or launch on another port. |
| Chromium dies on relaunch | Singleton lock in the reused data dir (`SingletonLock`/`Cookie`/`Socket`). Delete them, or close all instances first. |
| `Invoke-WebRequest` fails against Zotero | HTTP/1.0 response without `Content-Length`. Use `curl.exe`. |
| `where`/`Get-Command` cannot find a tool a service needs | The background service inherited a stale `PATH`. Use absolute executable paths in MCP `command` arrays. |
| `npx` not found by the MCP server | Same PATH issue, or Node not installed. Prefer the absolute `node.exe` + `cli.js` form (§7). |
| `extract_texts.py` reports "NO PDF FOUND" for everything | `ZOTERO_STORAGE` points at the container path. Set it to `%USERPROFILE%\Zotero\storage`. |
| Ports `8888`/`8082`/`11434` unused | Expected: those belong to the Docker stack, not the Windows path. |

## 11. What still needs Linux (the Docker stack)

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
