#!/usr/bin/env bash
# Render a URL to PDF via the container chromium, copy it out, and (by
# default) convert to paginated markdown via extract_texts.py md.
# Usage: fetch_pdf.sh "<url>" "<outdir>" "<basename-without-ext>"
#
# Defaults are tuned for the OA-paper text-extraction use case (images off,
# since only the text layer matters and images just cost bandwidth/time).
# For a visual document where images must survive into the PDF (e.g.
# rendering a local notes page with embedded pictures), override:
#   FETCH_PDF_IMAGES=1 FETCH_PDF_NO_MD=1 fetch_pdf.sh "$URL" "$OUTDIR" "$BASE"
#
# Egress proxy: CHROMIUM_PROXY=socks5://localhost:3333 routes this render the
# same way launch_chromium.sh does, for hosts that refuse datacenter/foreign
# IPs (kremlin.ru, sozd.duma.gov.ru, tc26.ru). The container is on the host
# network, so a host-side proxy is reachable at localhost.
set -u
URL="$1"; OUTDIR="$2"; BASE="$3"
C="${ZOTERO_CONTAINER:-zotero}"
ZS="${ZOTERO_SETUP:-$HOME/research-stack}"
ZV="$ZS/.venv/bin/python"
IMG_FLAG=""
[ "${FETCH_PDF_IMAGES:-0}" = "1" ] || IMG_FLAG="--blink-settings=imagesEnabled=false"
PROXY_FLAG=""
[ -n "${CHROMIUM_PROXY:-}" ] && PROXY_FLAG="--proxy-server=${CHROMIUM_PROXY}"
tmp="/tmp/cpdf_$$.pdf"
docker exec -u 1000 "$C" bash -lc "/usr/bin/chromium-real --headless=new --no-sandbox --disable-gpu \
  $IMG_FLAG $PROXY_FLAG --user-data-dir=/tmp/cpdf_$$ \
  --print-to-pdf=/tmp/o_$$.pdf --print-to-pdf-no-header --no-pdf-header-footer \
  --virtual-time-budget=${FETCH_PDF_BUDGET:-15000} '$URL'" >/dev/null 2>&1
docker cp "$C:/tmp/o_$$.pdf" "$OUTDIR/$BASE.pdf" 2>/dev/null
docker exec "$C" rm -f "/tmp/o_$$.pdf" 2>/dev/null
docker exec -u 1000 "$C" rm -rf "/tmp/cpdf_$$" 2>/dev/null
if [ -s "$OUTDIR/$BASE.pdf" ]; then
  sz=$(stat -c%s "$OUTDIR/$BASE.pdf")
  if [ "${FETCH_PDF_NO_MD:-0}" = "1" ]; then
    echo "OK  $BASE  (pdf=${sz}b)"
  else
    "$ZV" "$ZS/scripts/extract_texts.py" md "$OUTDIR/$BASE.pdf" --out "$OUTDIR" --suffix .md >/dev/null 2>&1
    echo "OK  $BASE  (pdf=${sz}b, md=$(stat -c%s "$OUTDIR/$BASE.md" 2>/dev/null || echo 0)b)"
  fi
else
  echo "FAIL $BASE  ($URL)"
fi
