#!/usr/bin/env bash
# Launch (or relaunch) Chromium inside the Zotero container with CDP on :9222.
# Idempotent: kills any existing chromium first.
set -euo pipefail

CONTAINER=${ZOTERO_CONTAINER:-zotero}
DEBUG_PORT=${CHROMIUM_DEBUG_PORT:-9222}
DATA_DIR=${CHROMIUM_DATA_DIR:-/config/.chromium-debug}

# Optional: load an unpacked extension (container-visible path, e.g. /config/my-ext).
# Chromium keeps --load-extension (only branded Chrome 137+ dropped it); the
# --disable-features toggle is a harmless belt-and-suspenders for that case.
EXT_DIR=${CHROMIUM_EXT:-}
EXT_FLAG=""
[ -n "$EXT_DIR" ] && EXT_FLAG="--load-extension=$EXT_DIR --disable-features=DisableLoadExtensionCommandLineSwitch"

docker exec "$CONTAINER" bash -c "pkill -f 'user-data-dir=$DATA_DIR' || true; sleep 2; rm -f $DATA_DIR/SingletonLock $DATA_DIR/SingletonCookie $DATA_DIR/SingletonSocket" 2>/dev/null || true

# Bypass the linuxserver wrapper (/usr/bin/chromium) — it hardcodes
# --start-maximized + --test-type, which produce a borderless, focus-grabbing
# window under labwc/Wayland. Call /usr/bin/chromium-real directly with a
# normal resizable window.
docker exec -u 1000 -d "$CONTAINER" bash -c "
  export XDG_RUNTIME_DIR=/config/.XDG
  export WAYLAND_DISPLAY=\$(ls /config/.XDG/wayland-* 2>/dev/null | grep -v lock | head -1 | xargs -r basename)
  [ -z \"\$WAYLAND_DISPLAY\" ] && export WAYLAND_DISPLAY=wayland-1
  # X display drifts across container restarts (:0 vs :1) — detect the live one
  # from the running Xwayland, fall back to the newest socket, then :0.
  DISP=\$(pgrep -a Xwayland 2>/dev/null | grep -oE ' :[0-9]+' | head -1 | tr -d ' ')
  [ -z \"\$DISP\" ] && DISP=\$(ls /tmp/.X11-unix 2>/dev/null | tail -1 | sed 's/^X/:/')
  [ -z \"\$DISP\" ] && DISP=:0
  export DISPLAY=\$DISP
  exec /usr/bin/chromium-real \
    --ozone-platform=x11 \
    --no-first-run \
    --no-default-browser-check \
    --no-sandbox \
    --test-type \
    --password-store=basic \
    --disable-dev-shm-usage \
    --start-maximized \
    --remote-debugging-port=$DEBUG_PORT \
    --remote-allow-origins=* \
    --user-data-dir=$DATA_DIR \
    $EXT_FLAG \
    > /tmp/chromium.log 2>&1
"

for _ in 1 2 3 4 5; do
  sleep 1
  if curl -sf "http://127.0.0.1:$DEBUG_PORT/json/version" >/dev/null; then
    echo "Chromium CDP ready at http://127.0.0.1:$DEBUG_PORT"
    exit 0
  fi
done

echo "Chromium did not come up on :$DEBUG_PORT" >&2
docker exec "$CONTAINER" tail -20 /tmp/chromium.log >&2 || true
exit 1
