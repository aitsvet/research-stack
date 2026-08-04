#!/usr/bin/env bash
# Recursively unpack a tree of nested .zip/.rar bundles, verifying every one.
#
# Runs inside the docconv service (unrar/unar/7zz live there, not on the host):
#   docker compose --profile tools run --rm --entrypoint bash docconv \
#     /opt/scripts/unpack_archives.sh /corpus/<subdir> [--keep-nested]
#
# Each archive is extracted into a sibling directory named after its stem, then
# every entry the archive declares is checked for existence on disk. Counting
# files is not enough — archive listings include directory entries — and exit
# status is not enough either: unar returns 0 even when individual members fail
# ("Attempted to read more data than was available"), and unrar without a UTF-8
# locale writes Cyrillic names as "?????" and then cannot reopen its own paths.
# Nested archives are removed once verified, since they are reproducible from
# the parent bundle; the top-level bundle stays as the downloaded artefact.
set -uo pipefail

ROOT=${1:?usage: unpack_archives.sh <dir> [--keep-nested]}
KEEP=${2:-}
cd "$ROOT" || exit 1

entries() {                      # every path the archive declares, one per line
  case "${1,,}" in
    *.rar) unrar lb -r "$1" 2>/dev/null ;;
    *)     7zz l -ba -slt "$1" 2>/dev/null | sed -n 's/^Path = //p' ;;
  esac
}

extract() {
  case "${1,,}" in
    *.rar) unrar x -y -inul -op"$2" "$1" </dev/null >/dev/null 2>&1 ;;
    *)     7zz x -y -o"$2" "$1" </dev/null >/dev/null 2>&1 ;;
  esac
}

total=0 okc=0 bad=0
for depth in 1 2 3 4 5; do
  mapfile -d '' archives < <(find . -type f \( -iname '*.zip' -o -iname '*.rar' \) -print0)
  [ ${#archives[@]} -eq 0 ] && break
  progress=0
  for a in "${archives[@]}"; do
    d="${a%.*}"
    [ -d "$d" ] && continue                      # unpacked on an earlier pass
    progress=1; total=$((total + 1))
    mkdir -p "$d"
    extract "$a" "$d"

    declared=0 missing=0 firstmiss=""
    while IFS= read -r e; do
      [ -z "$e" ] && continue
      declared=$((declared + 1))
      if [ ! -e "$d/$e" ]; then
        missing=$((missing + 1))
        [ -z "$firstmiss" ] && firstmiss="$e"
      fi
    done < <(entries "$a")

    if [ "$declared" -gt 0 ] && [ "$missing" -eq 0 ]; then
      okc=$((okc + 1))
      printf 'OK       %5d entries  %s\n' "$declared" "$a"
      if [ "$KEEP" != "--keep-nested" ] && [ "$(dirname "$a")" != "." ]; then
        rm -f "$a"
      fi
    else
      bad=$((bad + 1))
      printf 'MISSING  %5d/%-5d      %s   [first: %s]\n' \
             "$missing" "$declared" "$a" "${firstmiss:-<listing failed>}"
    fi
  done
  [ $progress -eq 0 ] && break
done
printf '\narchives=%d verified=%d incomplete=%d\n' "$total" "$okc" "$bad"
[ "$bad" -eq 0 ]
