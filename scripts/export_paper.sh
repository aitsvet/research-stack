#!/usr/bin/env bash
# Markdown master -> journal .docx -> .pdf, in one pass.
#
# The Markdown is the source of truth. Nothing is ever edited in the .docx:
# every change goes into the .md and the export is re-run, so the two cannot
# drift. md_docx.py --check proves that after each build by diffing the docx
# paragraph text against the Markdown.
#
#   export_paper.sh <project>/papers/article.md \
#       --template <project>/papers/templates/template.docx \
#       --name "Article_title"
#
# --name is the file stem the journal should receive (defaults to the .md stem).
# --image-cm sets figure width (default 15).
# --doc also writes Word 97-2003 .doc, for journals that ask for that format.
#
# The PDF step runs LibreOffice inside the docconv compose service so the host
# stays free of LibreOffice and the rendering matches earlier submissions
# (Debian bookworm, LibreOffice 7.4, Liberation Serif substituted for Times).
set -euo pipefail

STACK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MD=""; TEMPLATE="${DOCX_TEMPLATE:-}"; NAME=""; IMAGE_CM=15; DOC=0
while [ $# -gt 0 ]; do
  case "$1" in
    --template) TEMPLATE="$2"; shift 2 ;;
    --name)     NAME="$2"; shift 2 ;;
    --image-cm) IMAGE_CM="$2"; shift 2 ;;
    --doc)      DOC=1; shift ;;
    -*) echo "unknown option: $1" >&2; exit 2 ;;
    *)  MD="$1"; shift ;;
  esac
done
[ -n "$MD" ] || { echo "usage: export_paper.sh PAPER.md --template ACCEPTED.docx [--name STEM]" >&2; exit 2; }
[ -n "$TEMPLATE" ] || { echo "--template (or \$DOCX_TEMPLATE) is required" >&2; exit 2; }

MD_ABS="$(readlink -f "$MD")"
OUT_DIR="$(dirname "$MD_ABS")"
[ -n "$NAME" ] || NAME="$(basename "${MD_ABS%.*}")"
DOCX="$OUT_DIR/$NAME.docx"

python3 "$STACK/scripts/md_docx.py" "$MD_ABS" --template "$(readlink -f "$TEMPLATE")" \
        --out "$DOCX" --image-cm "$IMAGE_CM" --check

# officecli keeps a resident per file; drop it so validate reads the new bytes.
if command -v officecli >/dev/null 2>&1; then
  officecli close "$DOCX" >/dev/null 2>&1 || true
  officecli validate "$DOCX" | tail -1
fi

STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
cp "$DOCX" "$STAGE/"
( cd "$STACK" && CORPUS="$STAGE" docker compose --profile tools run --rm \
    --entrypoint soffice docconv -env:UserInstallation=file:///tmp/loprofile \
    --headless --convert-to pdf --outdir /corpus "/corpus/$NAME.docx" >/dev/null )
cp "$STAGE/$NAME.pdf" "$OUT_DIR/"

if [ "$DOC" = 1 ]; then
  ( cd "$STACK" && CORPUS="$STAGE" docker compose --profile tools run --rm \
      --entrypoint soffice docconv -env:UserInstallation=file:///tmp/loprofile \
      --headless --convert-to "doc:MS Word 97" --outdir /corpus "/corpus/$NAME.docx" >/dev/null )
  cp "$STAGE/$NAME.doc" "$OUT_DIR/"
  echo "doc:  $OUT_DIR/$NAME.doc"
fi

echo "docx: $DOCX"
echo "pdf:  $OUT_DIR/$NAME.pdf ($(pdfinfo "$OUT_DIR/$NAME.pdf" | awk '/^Pages/{print $2}') pages)"
