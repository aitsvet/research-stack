#!/usr/bin/env bash
# Two-way Zotero library sync between peers — like git with a hub: one host is
# the ORIGIN (the merge point; run this script there, once per replica), any
# number of other hosts are replicas. Independent of whatever AI front-end
# (Claude Code, OpenCode, Open WebUI, …) runs on any of the peers.
# Design and operations: SETUP.md, "Library sync between peers".
# Merge semantics: merge_replica.py header.
#
#   SYNC_REPLICA=user@replica ./scripts/sync_library.sh [--dry-run]
#
# Variables:
#   SYNC_REPLICA       ssh address of the replica (user@host). Empty = local
#                      test: the replica is the local path SYNC_REPLICA_ROOT.
#   SYNC_REPLICA_ROOT  path of zotero-setup on the replica (default
#                      "zotero-setup", relative to $HOME there).
#   SYNC_SSH_KEY       dedicated ssh key (default: regular ssh identity).
#
# --dry-run: merge plan + rsync volumes; containers and DBs are untouched
# (except a sub-second pause of the replica's zotero while its DB is copied).

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
REPLICA="${SYNC_REPLICA-}"
RROOT="${SYNC_REPLICA_ROOT:-zotero-setup}"
SYNC="$ROOT/.sync"
KEY="${SYNC_SSH_KEY-}"

DRY=()
if [ "${1-}" = "--dry-run" ]; then DRY=(--dry-run); fi

SSH=(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new)
if [ -n "$KEY" ] && [ -f "$KEY" ]; then SSH+=(-i "$KEY"); fi
export RSYNC_RSH="${SSH[*]}"

rsh() { # run a command on the replica (or locally in test mode)
  if [ -n "$REPLICA" ]; then "${SSH[@]}" "$REPLICA" "$@"; else bash -c "$@"; fi
}
rpath() { # replica path prefix for rsync
  if [ -n "$REPLICA" ]; then echo "$REPLICA:$RROOT"; else echo "$RROOT"; fi
}

mkdir -p "$SYNC"
exec 9>"$SYNC/.lock"
if ! flock -n 9; then echo "another sync is already running" >&2; exit 1; fi
rm -f "$SYNC/snap1.txt" "$SYNC/snap2.txt"

if [ -n "$REPLICA" ] && ! rsh "true" 2>/dev/null; then
  echo "replica $REPLICA unreachable over ssh" >&2
  exit 1
fi

if [ -f "$ROOT/.env" ]; then set -a; . "$ROOT/.env"; set +a; fi

# --- 1. replica container and its DB ---------------------------------------
was_running=""
if rsh "[ -f '$RROOT/docker-compose.yml' ] && [ -n \"\$(docker ps -q -f name='^zotero\$' 2>/dev/null)\" ]" 2>/dev/null; then
  was_running=1
fi
phase=fetch
dry_paused=""
restore() { # restart the replica container if we stopped it and its DB is sane
  if [ -n "$dry_paused" ]; then
    rsh "docker unpause zotero >/dev/null" || true
  elif [ -n "$was_running" ] && [ ${#DRY[@]} -eq 0 ]; then
    if [ "$phase" = place ]; then
      echo "!! post-swap DB check failed — replica container LEFT STOPPED" >&2
    else
      rsh "docker start zotero >/dev/null" || true
    fi
  fi
}
trap restore EXIT

if [ -n "$was_running" ]; then
  if [ ${#DRY[@]} -gt 0 ]; then
    echo "==> dry-run: pausing replica zotero while its DB is copied"
    rsh "docker pause zotero >/dev/null"
    dry_paused=1
  else
    echo "==> stopping zotero on the replica"
    rsh "docker stop zotero >/dev/null"
  fi
fi

have_replica_db=""
rm -rf "$SYNC/replica"; mkdir -p "$SYNC/replica"
if rsync -a "$(rpath)/config/Zotero/zotero.sqlite" "$SYNC/replica/" 2>/dev/null; then
  rsync -a "$(rpath)/config/Zotero/zotero.sqlite-journal" "$SYNC/replica/" 2>/dev/null || true
  have_replica_db=1
else
  echo "==> replica has no DB yet — nothing to merge"
fi

if [ -n "$dry_paused" ]; then
  rsh "docker unpause zotero >/dev/null"
  dry_paused=""
fi

# --- 1b. origin has no DB yet → first fill from the replica -----------------
if [ ! -f "$ROOT/config/Zotero/zotero.sqlite" ]; then
  if [ -z "$have_replica_db" ]; then
    echo "no DB on either peer — nothing to sync" >&2
    exit 1
  fi
  if [ ${#DRY[@]} -gt 0 ]; then
    echo "==> dry-run: origin is empty — a real run would first-fill it from the replica"
    exit 0
  fi
  echo "==> first fill: transferring the library from the replica (its container is stopped, DB is whole)"
  local_was=""
  if [ -f "$ROOT/docker-compose.yml" ] && [ -n "$(docker ps -q -f name='^zotero$' 2>/dev/null || true)" ]; then
    docker stop zotero >/dev/null; local_was=1
  fi
  mkdir -p "$ROOT/config"
  rsync -a --delete --info=stats1 --exclude '*.bak' --exclude '*.prev' \
    "$(rpath)/config/Zotero/" "$ROOT/config/Zotero/"
  rsync -a --delete --info=stats1 "$(rpath)/config/.zotero/" "$ROOT/config/.zotero/" 2>/dev/null || true
  python3 - "$ROOT/config/Zotero/zotero.sqlite" <<'PY'
import sqlite3, sys
con = sqlite3.connect(f'file:{sys.argv[1]}?mode=ro', uri=True)
ok = con.execute('PRAGMA integrity_check').fetchone()[0]
n = con.execute('SELECT COUNT(*) FROM items').fetchone()[0]
if ok != 'ok': sys.exit(f'integrity_check: {ok}')
print(f'origin filled: integrity ok, items: {n}')
PY
  if [ -n "$local_was" ]; then docker start zotero >/dev/null; fi
  echo "==> done (subsequent runs do the normal two-way cycle)"
  exit 0
fi

# --- 2. origin snapshot + merge plan ----------------------------------------
echo "==> origin snapshot"
./scripts/snapshot_db.sh > "$SYNC/snap1.txt"; cat "$SYNC/snap1.txt"

if [ -n "$have_replica_db" ]; then
  python3 - "$SYNC/replica/zotero.sqlite" <<'PY'   # journal rollback + copy check
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
ok = con.execute('PRAGMA integrity_check').fetchone()[0]
if ok != 'ok': sys.exit(f'fetched replica DB is corrupt: {ok}')
PY
  echo "==> merge plan (replica edits)"
  python3 ./scripts/merge_replica.py plan \
    --origin "$ROOT/config/Zotero/.snapshot/zotero.sqlite" \
    --replica "$SYNC/replica/zotero.sqlite" --out "$SYNC/plan.json"

  # a freshly created empty origin DB against a full replica is a deployment
  # ordering mistake, not "thousands of new items on the replica"
  if ! python3 -c "import json,sys; s=json.load(open('$SYNC/plan.json'))['stats']; sys.exit(1 if s['origin_items']<10 and s['replica_items']>100 else 0)"; then
    echo "!! origin DB is nearly empty while the replica is full: the container likely created a fresh DB." >&2
    echo "   Stop zotero, remove config/Zotero/zotero.sqlite and re-run — the first fill will kick in." >&2
    exit 1
  fi

  # files of the replica's new attachments (to import) + rescued ones
  for kind in storage_keys rescue_keys; do
    if [ "$kind" = rescue_keys ]; then dest="$SYNC/rescue-storage"; else dest="$SYNC/storage"; fi
    keys="$(python3 -c "import json;print(' '.join(json.load(open('$SYNC/plan.json'))['$kind']))")"
    if [ -n "$keys" ]; then
      mkdir -p "$dest"
      for k in $keys; do
        rsync -a "$(rpath)/config/Zotero/storage/$k" "$dest/" || echo "!! failed to fetch storage/$k" >&2
      done
    fi
  done

  # --- 3. replay replica edits into the origin's live Zotero ---------------
  ops="$(python3 -c "import json; s=json.load(open('$SYNC/plan.json'))['stats']; print(sum(s[k] for k in ('new_items','new_notes','note_updates','tag_additions','new_collections','memberships','attachments','trash')))")"
  if [ ${#DRY[@]} -eq 0 ] && [ "$ops" -gt 0 ]; then
    phase=apply
    echo "==> applying replica edits on the origin (MCP)"
    python3 ./scripts/merge_replica.py apply "$SYNC/plan.json" \
      --replica-storage "$SYNC/storage" \
      --serve-ip "$(hostname -I | awk '{print $1}')" \
      --rescue "$SYNC/rescue-notes"
    echo "==> snapshot of the merged origin"
    ./scripts/snapshot_db.sh > "$SYNC/snap2.txt"; cat "$SYNC/snap2.txt"
  elif [ ${#DRY[@]} -eq 0 ]; then
    echo "==> no replica edits — merge skipped"
  fi
fi
want_items="$(sed -n 's/.*items: \([0-9]\{1,\}\).*/\1/p' "$SYNC"/snap2.txt "$SYNC"/snap1.txt 2>/dev/null | head -1 || true)"

# --- 4. push to the replica and swap its DB ---------------------------------
phase=push
rsh "mkdir -p '$RROOT/config'"    # rsync only creates the last path element
echo "==> rsync push: data (storage, styles, translators, DB snapshots)"
# Live DBs are excluded ONLY at the root (the leading "/" anchors the pattern
# to config/Zotero/): unanchored, rsync would also exclude
# .snapshot/zotero.sqlite — the whole point of the transfer.
rsync -a --delete "${DRY[@]}" --info=stats1 \
  --exclude '/zotero.sqlite' --exclude '/zotero.sqlite-*' \
  --exclude '/zotero-mcp-vectors.sqlite' --exclude '/zotero-mcp-vectors.sqlite-*' \
  --exclude '*.bak' --exclude '*.prev' \
  "$ROOT/config/Zotero/" "$(rpath)/config/Zotero/"

echo "==> rsync push: profile (prefs.js, extensions — MCP plugin and token inside)"
rsync -a --delete "${DRY[@]}" --info=stats1 \
  --exclude '.parentlock' --exclude 'crashes/' --exclude 'datareporting/' \
  --exclude 'places.sqlite*' --exclude '*-wal' --exclude '*-shm' \
  --exclude 'Telemetry*' \
  "$ROOT/config/.zotero/" "$(rpath)/config/.zotero/"

if [ ${#DRY[@]} -gt 0 ]; then
  echo "==> dry-run: DB swap and replica container untouched"
  exit 0
fi

echo "==> swapping the DB on the replica"
phase=place
rsh "'$RROOT/scripts/place_snapshot.sh' '$want_items'"
phase=done

if [ -n "$was_running" ]; then
  echo "==> starting zotero on the replica"
  rsh "docker start zotero >/dev/null"
  was_running=""
fi
echo "==> done"
