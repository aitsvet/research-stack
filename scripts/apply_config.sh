#!/usr/bin/env bash
# Idempotent: install repo-tracked Zotero `user.js` into the running container's
# bind-mounted profile dir. Other config (labwc.xml etc.) is bind-mounted via
# docker-compose.yml and applied by the image's init script.
#
# Run after `docker compose up -d` on first launch — and again any time `user.js`
# is edited.
set -euo pipefail

CONTAINER=${ZOTERO_CONTAINER:-zotero}
HERE="$(cd "$(dirname "$0")/.." && pwd)"

PROFILE=$(docker exec "$CONTAINER" bash -c 'ls -d /config/.zotero/zotero/*.default 2>/dev/null | head -1' || true)
if [ -z "$PROFILE" ]; then
  echo "No Zotero profile yet at /config/.zotero/zotero/*.default — launch the container and let Zotero start once, then rerun." >&2
  exit 1
fi
docker cp "$HERE/user.js" "$CONTAINER:$PROFILE/user.js"
docker exec "$CONTAINER" chown abc:abc "$PROFILE/user.js"
echo "Installed $HERE/user.js → $PROFILE/user.js"
echo "Restart Zotero (or container) for prefs to take effect."
