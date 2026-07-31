#!/usr/bin/env bash
# Render a URL to PDF via the container chromium (text-focused: images off),
# copy it out, and convert to paginated markdown via extract_texts.py md.
# Usage: fetch_pdf.sh "<url>" "<outdir>" "<basename-without-ext>"
set -u
URL="$1"; OUTDIR="$2"; BASE="$3"
C="${ZOTERO_CONTAINER:-zotero}"
ZS="${ZOTERO_SETUP:-$HOME/research-stack}"
ZV="$ZS/.venv/bin/python"
tmp="/tmp/cpdf_$$.pdf"
docker exec -u 1000 "$C" bash -lc "/usr/bin/chromium-real --headless=new --no-sandbox --disable-gpu \
  --blink-settings=imagesEnabled=false --user-data-dir=/tmp/cpdf_$$ \
  --print-to-pdf=/tmp/o_$$.pdf --print-to-pdf-no-header --no-pdf-header-footer \
  --virtual-time-budget=15000 '$URL'" >/dev/null 2>&1
docker cp "$C:/tmp/o_$$.pdf" "$OUTDIR/$BASE.pdf" 2>/dev/null
docker exec "$C" rm -f "/tmp/o_$$.pdf" 2>/dev/null
docker exec -u 1000 "$C" rm -rf "/tmp/cpdf_$$" 2>/dev/null
if [ -s "$OUTDIR/$BASE.pdf" ]; then
  sz=$(stat -c%s "$OUTDIR/$BASE.pdf")
  "$ZV" "$ZS/scripts/extract_texts.py" md "$OUTDIR/$BASE.pdf" --out "$OUTDIR" --suffix .md >/dev/null 2>&1
  echo "OK  $BASE  (pdf=${sz}b, md=$(stat -c%s "$OUTDIR/$BASE.md" 2>/dev/null || echo 0)b)"
else
  echo "FAIL $BASE  ($URL)"
fi
