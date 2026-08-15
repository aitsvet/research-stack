#!/usr/bin/env bash
# Dump a full library manifest (all collections + all items) as diff-friendly markdown.
# Reads Zotero's local API (:23119) directly — the MCP plugin is not involved.
#
# Usage: library_manifest.sh [output.md]     # default: stdout
# Env:   ZOTERO_API — API base, default http://localhost:23119/api/users/0
set -euo pipefail

BASE="${ZOTERO_API:-http://localhost:23119/api/users/0}"
OUT="${1:-/dev/stdout}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fetch_all() { # $1 = endpoint path, $2 = output jsonl
  local ep="$1" out="$2" start=0 n
  : > "$out"
  while :; do
    # The local Zotero API must not be routed through an inherited HTTP proxy.
    # Non-local ZOTERO_API overrides still keep their ordinary proxy behavior.
    resp=$(curl -sf --noproxy localhost,127.0.0.1,::1 \
      "$BASE$ep?format=json&limit=100&start=$start")
    n=$(jq 'length' <<<"$resp")
    jq -c '.[]' <<<"$resp" >> "$out"
    start=$((start + 100))
    [ "$n" -lt 100 ] && break
  done
}

fetch_all "/collections" "$TMP/cols.jsonl"
fetch_all "/items"       "$TMP/items.jsonl"
fetch_all "/items/trash" "$TMP/trash.jsonl"

echo "collections=$(wc -l < "$TMP/cols.jsonl") items=$(wc -l < "$TMP/items.jsonl") trash=$(wc -l < "$TMP/trash.jsonl")" >&2

# Keep only fields the renderer uses. In particular, do this before --slurpfile:
# copying multi-megabyte note/abstract bodies through every collection filter
# made a medium library take many minutes even though the manifest prints only
# the first 80 characters of a standalone note.
compact_items() {
  local src="$1"
  jq -c '
    def clean: gsub("[\r\n\t]+"; " ") | gsub(" +"; " ") | sub(" +$"; "");
    {
      key: .key,
      meta: {
        creatorSummary: (.meta.creatorSummary // ""),
        parsedDate: (.meta.parsedDate // "")
      },
      data: {
        itemType: .data.itemType,
        parentItem: (.data.parentItem // null),
        collections: (.data.collections // [])
      },
      manifestTitle: (
        (.data.title // .data.caseName // .data.nameOfAct //
          (if .data.itemType == "note"
           then ((.data.note // "")[0:4096]
                 | gsub("<[^>]*>"; " ") | clean | .[0:80])
           else null end) // "(untitled)") | clean
      )
    }
  ' "$src" > "$src.compact"
  mv "$src.compact" "$src"
}
compact_items "$TMP/items.jsonl"
compact_items "$TMP/trash.jsonl"

jq -n -r \
  --slurpfile cols "$TMP/cols.jsonl" \
  --slurpfile items "$TMP/items.jsonl" \
  --slurpfile trash "$TMP/trash.jsonl" \
  --arg date "$(date -u +%Y-%m-%d)" \
  --arg base "$BASE" '
  def title($i): $i.manifestTitle;

  ($cols | map({(.key): {name: .data.name, parent: (.data.parentCollection // false)}}) | add) as $cmap |
  def cpath($k): if $cmap[$k].parent == false then $cmap[$k].name
                 else cpath($cmap[$k].parent) + " / " + $cmap[$k].name end;

  ($items | map(select(.data.parentItem != null))) as $children |
  ($items | map(select(.data.parentItem == null))) as $top |
  ($children | group_by(.data.parentItem)
    | map({(.[0].data.parentItem):
        { att:  (map(select(.data.itemType == "attachment")) | length),
          note: (map(select(.data.itemType == "note"))       | length) }})
    | add // {}) as $kids |

  def itemline($i):
    ($i.meta.creatorSummary // "") as $cr |
    (($i.meta.parsedDate // "") | .[0:4]) as $yr |
    ($kids[$i.key] // {att:0, note:0}) as $k |
    "- `\($i.key)` \($i.data.itemType) — " +
    (if $cr != "" then $cr + " " else "" end) +
    (if $yr != "" then "(\($yr)) " else "" end) +
    title($i) +
    (if $k.att > 0 or $k.note > 0
     then " [" + ([(if $k.att > 0 then "\($k.att) att" else empty end),
                   (if $k.note > 0 then "\($k.note) note" else empty end)] | join(", ")) + "]"
     else "" end);

  def section($name; $list):
    if ($list | length) == 0 then empty else
      "### \($name) — \($list | length) items\n\n" +
      ($list | sort_by(title(.) | ascii_downcase) | map(itemline(.)) | join("\n")) + "\n"
    end;

  ($cols | map(.key) | map({key: ., path: cpath(.)}) | sort_by(.path | ascii_downcase)) as $csorted |

  (
  "# Zotero library manifest\n\n" +
  "Generated \($date) from \($base).\n" +
  "Purpose: diff against another peer'\''s manifest after a library sync.\n" +
  "Item keys are sync-stable identifiers. `[N att, M note]` = child attachments/notes of that item.\n\n" +
  "## Totals\n\n" +
  "- Items (excl. trash): \($items | length) — top-level \($top | length), child attachments \($children | map(select(.data.itemType == "attachment")) | length), child notes \($children | map(select(.data.itemType == "note")) | length)\n" +
  "- Collections: \($cols | length)\n" +
  "- Trash: \($trash | length)\n\n" +
  "### Items by type\n\n" +
  ($items | group_by(.data.itemType) | sort_by(-length)
    | map("- \(.[0].data.itemType): \(length)") | join("\n")) + "\n\n" +
  "## Collections\n\n" +
  ($csorted | map(
      . as $c |
      "- `\($c.key)` \($c.path) — \($top | map(select((.data.collections // []) | index($c.key))) | length) items"
    ) | join("\n")) + "\n\n" +
  "## Items by collection\n\n" +
  ($csorted | map(
      . as $c |
      section("\($c.path) (`\($c.key)`)";
              $top | map(select((.data.collections // []) | index($c.key))))
    ) | join("\n")) + "\n" +
  section("Unfiled (no collection)";
          $top | map(select((.data.collections // []) | length == 0))) + "\n" +
  (if ($trash | length) > 0 then
    "## Trash — \($trash | length) items\n\n" +
    ($trash | map("- `\(.key)` \(.data.itemType) — " + title(.)) | join("\n")) + "\n"
   else "" end)
  ) | rtrimstr("\n")
' > "$OUT"
