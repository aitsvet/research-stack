# research-stack

A self-hosted research environment and document pipeline:

- **Zotero** as the authoritative library, exposed over **MCP** so an AI
  assistant can search, read, annotate and (optionally) write to it.
- **Chromium/Chrome over CDP** so the assistant drives the same browser session
  you do — the page it scrapes is the page you see.
- A **discovery → acquisition → extraction → grounded-drafting** pipeline for
  literature work, plus an export toolchain that turns Markdown into a
  journal-formatted `.docx`/`.pdf`.

The repository carries only the **pipeline and its operating rules** — never a
corpus, a manuscript, or a private library. Domain material belongs in a
separate (private) project repository that consumes these tools.

## Deployment shapes

| Shape | Guide | What runs |
|---|---|---|
| One Linux host, everything in Docker | `SETUP.md` | Zotero web UI (Selkies), in-Zotero MCP plugin, in-container Chromium, optional AI appliances (Ollama, Open WebUI, JupyterLab) |
| A Windows workstation, no containers | `WINDOWS.md` | Zotero desktop + a CDP browser + the MCP servers + the Python pipeline, all native |

Both shapes share the same Python pipeline in `scripts/`, the same Zotero
`user.js`, and the same browser MCPs — only how Zotero and Chromium are
launched differs.

## Documentation

| Doc | Read it when |
|---|---|
| `AGENTS.md` | standing rules — the daily cheat-sheet and the map to everything else |
| `SETUP.md` | Docker deployment, architecture, security, and Zotero/MCP troubleshooting |
| `WINDOWS.md` | Windows-native setup: Python, Git Bash, Node, Zotero, Chrome CDP, MCP wiring |
| `SCRIPTS.md` | choosing a tool — the script catalogue, the pipeline, per-tool traps |
| `ROUTES.md` | a source won't come out — host routes, bot-walls, lookup playbooks |
| `READING.md` | how to read long primary sources without blowing the context |
| `EXTRACT.md` | the corpus as a mirror of Zotero, and how to extract text from it |
| `CLEARANCE.md` | clearing the tracked tree and reachable history before publication |

If you are an OpenCode agent, `AGENTS.md` is loaded automatically; the table
above is the route into the rest. If you are a human (or a fresh agent given
only this repository's URL), start with **Quick start** below and then read the
guide for your platform.

## Quick start — Docker (Linux host)

```bash
git clone https://github.com/aitsvet/research-stack.git
cd research-stack
cp .env.example .env && $EDITOR .env     # ZOTERO_PASSWORD; OpenAlex/Unpaywall e-mail
docker compose up -d
./scripts/launch_chromium.sh             # CDP does not come up on its own
```

Then register the MCP servers as described in `SETUP.md` §*Registering MCP
servers in your AI tool*.

## Quick start — Windows (no Docker)

The repository is public over HTTPS, so a clone needs no keys:

```powershell
git clone https://github.com/aitsvet/research-stack.git
cd research-stack
```

Then open **`WINDOWS.md`** and work top to bottom. It is a checklist:
**§1** is a preflight block that reports what is already installed, **§2** turns
that table into a plan, **§3** asks three choices (how to clone — HTTPS by
default, so SSH keys are optional and only needed to push; which Chrome gets
CDP; which AI client), and **§4** is the install list — do only the items §1
flagged. It brings up Zotero with its local API and MCP plugin, Chrome with CDP
on `:9222`, the MCP servers in your client, and the environment variables the
pipeline expects. The Docker-only appliances (Selkies web UI, Ollama, Open
WebUI, JupyterLab, the LibreOffice and PlantUML containers) are not part of the
Windows path.

## Repository layout

```
research-stack/
├── AGENTS.md  SETUP.md  WINDOWS.md  SCRIPTS.md  ROUTES.md  READING.md  EXTRACT.md  CLEARANCE.md
├── docker-compose.yml          # the Linux stack (zotero + optional profiles)
├── user.js                     # Zotero prefs: MCP plugin + local API
├── requirements.txt            # Python deps for the pipeline
├── scripts/                    # discovery / acquisition / extraction / export toolkit
│   ├── deck/                   # separate PPTX toolkit — read its own SKILL.md
│   └── zotero-sync/            # peer library sync — read its own SKILL.md
├── docker/                     # images and config for docconv, plantuml, ollama, zotero
├── opencode/  jupyter/  open-webui/   # optional AI appliances, each with its own README
└── tests/                      # unit tests for the pure-python helpers
```

## Publication

This repository is **public**. Every commit is a publication act: run the
publication gate in `AGENTS.md` on every change, and follow `CLEARANCE.md` for
anything that touches the tree broadly or the history. Secrets live in `.env`
(gitignored), never in tracked files; `.mcp.json` therefore holds only the
secret-free browser servers.
