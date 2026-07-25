# JupyterLab + MCP

Optional service for the stack: JupyterLab (with real-time collaboration) plus
`jupyter-mcp-server`, which exposes the running lab as a streamable-HTTP MCP —
any AI front-end can then read, edit and execute notebooks.

```bash
cp .env.example .env && $EDITOR .env    # JUPYTER_TOKEN, WORK_DIR
docker compose up -d
```

- Lab UI: `http://127.0.0.1:8888` (token from `.env`).
- MCP endpoint: `http://127.0.0.1:4040/mcp`. Register in Claude Code:
  `claude mcp add jupyter http://127.0.0.1:4040/mcp -t http`;
  OpenCode: already wired in `../opencode/opencode.json`.
