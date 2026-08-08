#!/usr/bin/with-contenv bash
# Pin selkies' hardcoded 0.0.0.0 binds (data websocket :8082, control plane,
# settings default) to loopback — upstream has no CLI/env knob for them. Runs
# from /custom-cont-init.d before services start; idempotent. The web stream
# keeps working: the image's nginx proxies /websocket to 127.0.0.1:8082.
set -e
for f in /lsiopy/lib/python3.*/site-packages/selkies/selkies.py \
         /lsiopy/lib/python3.*/site-packages/selkies/settings.py; do
  if grep -q '0\.0\.0\.0' "$f"; then
    sed -i 's/0\.0\.0\.0/127.0.0.1/g' "$f"
    echo "[selkies-loopback] patched $f"
  else
    echo "[selkies-loopback] $f already loopback"
  fi
done
