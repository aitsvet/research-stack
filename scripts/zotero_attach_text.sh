#!/usr/bin/env bash
# Attach a local text/markdown file to a Zotero item as a child note.
#
# Use when:
#   - You want to import .md / .txt / .html into Zotero without it ever
#     entering the model's context.
#   - The MCP tool `import_attachment_url` rejects your URL (it forbids
#     loopback and RFC-1918 addresses, so a temp local HTTP server won't work).
#
# Thin CLI over zotero_mcp.add_note_file — the file bytes are read only by
# python, never by the calling shell/model.
#
# Usage: zotero_attach_text.sh <itemKey> <file> [title] [tag1,tag2,...]
set -euo pipefail

ITEM=${1:?itemKey required}
FILE=${2:?file path required}
TITLE=${3:-$(basename "$FILE")}
TAGS=${4:-markdown-extract}

[[ -r $FILE ]] || { echo "Cannot read $FILE" >&2; exit 1; }

# Locate .env relative to this script (scripts/ is sibling of .env).
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ENV_FILE=${ZOTERO_ENV:-$HERE/../.env}
set -a; source "$ENV_FILE"; set +a

ZS_SCRIPTS="$HERE" python3 - "$ITEM" "$FILE" "$TITLE" "$TAGS" <<'PY'
import os, sys
sys.path.insert(0, os.environ["ZS_SCRIPTS"])
from zotero_mcp import MCP, add_note_file, result_text
item, path, title, tags_csv = sys.argv[1:5]
resp = add_note_file(MCP("attach-text", throttle=0.2), item, path, title,
                     tags=[t for t in tags_csv.split(",") if t])
print(result_text(resp)[:200])
PY
