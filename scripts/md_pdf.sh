#!/usr/bin/env bash
# Markdown (with relative images) -> PDF in one call, printed by the container
# chromium — nothing installed on the host.
#   md_pdf.sh notes.md              -> notes.pdf next to it
#   md_pdf.sh notes.md out.pdf
#   md_pdf.sh <dir> [out.pdf]       -> all *.md in the dir (sorted) into one
#                                      PDF, page break between documents
# Relative image links resolve against the md's directory: it is served on
# 127.0.0.1:${MD_PDF_PORT:-8377} for the duration of the print (the container
# shares the host network). Wraps fetch_pdf.sh with images ON and no md
# follow-up; FETCH_PDF_BUDGET passes through for slow-settling pages.
set -u
SRC="$(readlink -f "$1")"
ZS="${ZOTERO_SETUP:-$HOME/research-stack}"
PORT="${MD_PDF_PORT:-8377}"
if [ -d "$SRC" ]; then DIR="$SRC"; BASE="$(basename "$SRC")"
else DIR="$(dirname "$SRC")"; BASE="$(basename "${SRC%.md}")"; fi
OUT="${2:-$DIR/$BASE.pdf}"
OUTDIR="$(dirname "$(readlink -f "$OUT")")"
OUTBASE="$(basename "$OUT")"; OUTBASE="${OUTBASE%.pdf}"
PAGE=".mdpdf_$$.html"

"$ZS/.venv/bin/python" - "$SRC" "$DIR/$PAGE" <<'PYEOF'
import sys, re, pathlib, markdown
src, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
docs = sorted(src.glob("*.md")) if src.is_dir() else [src]
css = ("body{font-family:sans-serif;max-width:850px;margin:auto;line-height:1.45}"
       "img{max-width:100%}h3{margin-top:1.6em}"
       "blockquote{border-left:3px solid #bbb;margin:.6em 0;padding:.2em .9em;color:#444}")
parts = []
for p in docs:
    md = p.read_text(encoding="utf-8")
    # html_md.py emits ![alt]() for a figure it could only describe, not save —
    # an empty-src <img> prints as a broken-image icon, so render the alt as text.
    md = re.sub(r'!\[([^\]]*)\]\(\)', r'*[иллюстрация: \1]*', md)
    parts.append(markdown.markdown(md, extensions=["tables"]))
body = '<div style="page-break-after:always"></div>'.join(parts)
out.write_text(f'<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head>'
               f'<body>{body}</body></html>', encoding="utf-8")
PYEOF

python3 -m http.server "$PORT" --bind 127.0.0.1 --directory "$DIR" >/dev/null 2>&1 &
SRV=$!
curl -sf --retry 10 --retry-connrefused --retry-delay 1 -o /dev/null "http://127.0.0.1:$PORT/$PAGE" \
  || { kill $SRV 2>/dev/null; rm -f "$DIR/$PAGE"; echo "FAIL: no server on :$PORT (busy? set MD_PDF_PORT)"; exit 1; }
FETCH_PDF_IMAGES=1 FETCH_PDF_NO_MD=1 "$ZS/scripts/fetch_pdf.sh" \
  "http://127.0.0.1:$PORT/$PAGE" "$OUTDIR" "$OUTBASE" >/dev/null
kill $SRV 2>/dev/null
rm -f "$DIR/$PAGE"
if [ -s "$OUTDIR/$OUTBASE.pdf" ]; then
  echo "OK  $(stat -c%s "$OUTDIR/$OUTBASE.pdf")b  -> $OUTDIR/$OUTBASE.pdf"
else
  echo "FAIL $SRC"; exit 1
fi
