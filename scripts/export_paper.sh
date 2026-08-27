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
# --record FILE appends what was actually produced — date, file name, page count,
# sha256 — to a committed log. The artefacts themselves are build output and stay
# out of version control, so without this nothing in the repository says which
# bytes a venue received.
#
# The PDF step runs LibreOffice inside the docconv compose service so the host
# stays free of LibreOffice and the rendering matches earlier submissions
# (Debian bookworm, LibreOffice 7.4, Liberation Serif substituted for Times).
set -euo pipefail

STACK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MD=""; TEMPLATE="${DOCX_TEMPLATE:-}"; NAME=""; IMAGE_CM=15; DOC=0; RECORD=""
while [ $# -gt 0 ]; do
  case "$1" in
    --template) TEMPLATE="$2"; shift 2 ;;
    --name)     NAME="$2"; shift 2 ;;
    --image-cm) IMAGE_CM="$2"; shift 2 ;;
    --doc)      DOC=1; shift ;;
    --record)   RECORD="$2"; shift 2 ;;
    -*) echo "unknown option: $1" >&2; exit 2 ;;
    *)  MD="$1"; shift ;;
  esac
done
[ -n "$MD" ] || { echo "usage: export_paper.sh PAPER.md --template ACCEPTED.docx [--name STEM]" >&2; exit 2; }
[ -n "$TEMPLATE" ] || { echo "--template (or \$DOCX_TEMPLATE) is required." >&2
  echo "The template is a build input, not an artefact: md_docx.py keeps its styles," >&2
  echo "section properties and footnote plumbing and rebuilds only the body. Keep one" >&2
  echo "in version control next to the papers, e.g. <project>/papers/templates/*.docx," >&2
  echo "and make it from an accepted submission with scripts/make_docx_template.py." >&2
  exit 2; }

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

PAGES="$(pdfinfo "$OUT_DIR/$NAME.pdf" | awk '/^Pages/{print $2}')"
echo "docx: $DOCX"
echo "pdf:  $OUT_DIR/$NAME.pdf ($PAGES pages)"

if [ -n "$RECORD" ]; then
  [ "$DOC" = 1 ] && FINAL="$OUT_DIR/$NAME.doc" || FINAL="$DOCX"
  [ -f "$RECORD" ] || printf '# Submitted files\n\nBuild artefacts are not versioned; this is what was produced and sent.\n\n| Date | File | Pages | sha256 |\n|---|---|---|---|\n' > "$RECORD"
  printf '| %s | %s | %s | %s |\n' "$(date -I)" "$(basename "$FINAL")" "$PAGES" \
      "$(sha256sum "$FINAL" | cut -c1-64)" >> "$RECORD"
  echo "record: $RECORD"
fi
