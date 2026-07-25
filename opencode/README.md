# OpenCode

Optional AI front-end for the stack (an alternative to Claude Code or
`../openwebui`): OpenCode web UI in a container, plus an opt-in Telegram
bridge.

```bash
cp .env.example .env && $EDITOR .env      # WORK_DIR (and JUPYTER_TOKEN if used)
docker compose up -d                      # web UI on 127.0.0.1 (port in logs)
docker compose --profile telegram up -d   # + Telegram bot (needs .env.telegram,
                                          #   see @grinev/opencode-telegram-bot)
```

`opencode.json` wires two MCPs:

- **jupyter** → `http://127.0.0.1:4040/mcp` — bring up `../jupyter` first;
- **chrome** → CDP on `localhost:9222` — provided by the stack's own Chromium
  (`../scripts/launch_chromium.sh`), no separate browser container needed.

It also carries an example `ollama` provider block for local models — edit or
remove to taste.
