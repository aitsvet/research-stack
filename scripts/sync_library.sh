#!/usr/bin/env bash
# Identity-preserving Zotero sync between peers — like fast-forward-only git
# with a hub. Run on the ORIGIN, once per replica. The Zotero object key is a
# durable identity and is never reassigned; see merge_replica.py.
#
#   SYNC_REPLICA=user@replica ./scripts/sync_library.sh [--dry-run]
#
# Variables:
#   SYNC_REPLICA       ssh address of the replica. Empty = local-path test.
#   SYNC_REPLICA_ROOT  research-stack path on replica (default: research-stack).
#   SYNC_SSH_KEY       optional identity file.
#   SYNC_SSH_JUMP      preferred jump host.
#   SYNC_SSH_FALLBACK_JUMP  fallback jump; "config" (default) means use the
#                      replica's ordinary ~/.ssh/config route without -J.
#   SYNC_SSH_PERSIST   shared SSH connection lifetime (default: 15m).
#
# --dry-run copies verified DB snapshots into .sync for comparison, but only
# pauses replica Zotero briefly and performs no library/profile/container swap.

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [ -f "$ROOT/.env" ]; then set -a; . "$ROOT/.env"; set +a; fi
if [ -f "$ROOT/.sync/config.env" ]; then
  set -a; . "$ROOT/.sync/config.env"; set +a
fi

REPLICA="${SYNC_REPLICA-}"
RROOT="${SYNC_REPLICA_ROOT:-research-stack}"
SYNC="$ROOT/.sync"
BASE="$SYNC/base/zotero.sqlite"
KEY="${SYNC_SSH_KEY-}"
JUMP="${SYNC_SSH_JUMP-}"
FALLBACK_JUMP="${SYNC_SSH_FALLBACK_JUMP-config}"
PERSIST="${SYNC_SSH_PERSIST:-15m}"
PYTHON="$ROOT/.venv/bin/python"

[ -x "$PYTHON" ] || { echo "missing $PYTHON — create the repo venv before syncing" >&2; exit 1; }
DRY=()
if [ "${1-}" = --dry-run ]; then
  DRY=(--dry-run)
elif [ -n "${1-}" ]; then
  echo "usage: $0 [--dry-run]" >&2
  exit 2
fi

mkdir -p "$SYNC"
exec 9>"$SYNC/.lock"
if ! flock -n 9; then echo "another sync is already running" >&2; exit 1; fi
rm -f "$SYNC/snap1.txt"

SSH_BASE=(ssh
  -o BatchMode=yes
  -o StrictHostKeyChecking=accept-new
  -o ConnectTimeout=30
  -o ConnectionAttempts=3
  -o ServerAliveInterval=10
  -o ServerAliveCountMax=3
  -o ControlMaster=auto
  -o ControlPersist="$PERSIST"
  -o ControlPath="$SYNC/ssh-%C"
)
if [ -n "$KEY" ] && [ -f "$KEY" ]; then SSH_BASE+=(-i "$KEY"); fi

set_ssh_route() {
  SSH=("${SSH_BASE[@]}")
  if [ -n "$1" ] && [ "$1" != config ]; then SSH+=(-J "$1"); fi
}

if [ -n "$REPLICA" ]; then
  routes=()
  if [ -n "$JUMP" ]; then
    routes+=("$JUMP")
    if [ -n "$FALLBACK_JUMP" ] && [ "$FALLBACK_JUMP" != "$JUMP" ]; then
      routes+=("$FALLBACK_JUMP")
    fi
  else
    routes+=(config)
  fi
  route_ok=""
  for route in "${routes[@]}"; do
    set_ssh_route "$route"
    if "${SSH[@]}" "$REPLICA" true 2>/dev/null; then
      route_ok=1
      if [ "$route" != "${routes[0]}" ]; then
        echo "preferred SSH jump unavailable; using fallback route" >&2
      fi
      break
    fi
  done
  [ -n "$route_ok" ] || { echo "replica $REPLICA unreachable over ssh" >&2; exit 1; }
else
  set_ssh_route config
fi
export RSYNC_RSH="${SSH[*]}"

rsh() {
  if [ -n "$REPLICA" ]; then "${SSH[@]}" "$REPLICA" "$@"; else bash -c "$@"; fi
}
rpath() {
  if [ -n "$REPLICA" ]; then echo "$REPLICA:$RROOT"; else echo "$RROOT"; fi
}

# Only the exact zotero container is paused/stopped. Compose siblings are never
# created or started by this workflow.
was_running=""
if rsh "[ -f '$RROOT/docker-compose.yml' ] && [ -n \"\$(docker ps -q -f name='^zotero\$' 2>/dev/null)\" ]" 2>/dev/null; then
  was_running=1
fi
phase=fetch
dry_paused=""
local_was=""
local_phase=""

restore_replica() {
  if [ -n "$dry_paused" ]; then
    rsh "docker unpause zotero >/dev/null" || true
  elif [ -n "$was_running" ] && [ ${#DRY[@]} -eq 0 ]; then
    if [ "$phase" = place ]; then
      echo "!! post-swap DB check failed — replica zotero LEFT STOPPED" >&2
    else
      rsh "docker start zotero >/dev/null" || true
    fi
  fi
}
restore_local() {
  if [ -n "$local_was" ]; then
    if [ "$local_phase" = place ]; then
      echo "!! local post-swap DB check failed — local zotero LEFT STOPPED" >&2
    else
      docker start zotero >/dev/null || true
    fi
  fi
}
cleanup() {
  restore_local
  restore_replica
  # Leave the multiplexed master alive for ControlPersist so a follow-up
  # verification or recovery command reuses the authenticated jump route.
}
trap cleanup EXIT

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
mkdir -p "$SYNC/replica"
rm -f "$SYNC/replica/zotero.sqlite-journal"
if rsync -azc --partial "$(rpath)/config/Zotero/zotero.sqlite" "$SYNC/replica/"; then
  rsync -azc --partial "$(rpath)/config/Zotero/zotero.sqlite-journal" \
    "$SYNC/replica/" 2>/dev/null || true
  have_replica_db=1
else
  echo "==> replica has no DB yet"
fi

if [ -n "$dry_paused" ]; then
  rsh "docker unpause zotero >/dev/null"
  dry_paused=""
fi

# First fill is an exact replica copy and establishes the common ancestor.
if [ ! -f "$ROOT/config/Zotero/zotero.sqlite" ]; then
  [ -n "$have_replica_db" ] || { echo "no DB on either peer" >&2; exit 1; }
  if [ ${#DRY[@]} -gt 0 ]; then
    echo "==> dry-run: origin is empty — a real run would first-fill it"
    exit 0
  fi
  echo "==> first fill: transferring the stopped replica library"
  if [ -n "$(docker ps -q -f name='^zotero$' 2>/dev/null || true)" ]; then
    docker stop zotero >/dev/null
    local_was=1
  fi
  mkdir -p "$ROOT/config"
  rsync -az --partial --delete --info=stats1 --exclude '*.bak' --exclude '*.prev' \
    "$(rpath)/config/Zotero/" "$ROOT/config/Zotero/"
  rsync -az --partial --delete --info=stats1 \
    "$(rpath)/config/.zotero/" "$ROOT/config/.zotero/" 2>/dev/null || true
  "$PYTHON" - "$ROOT/config/Zotero/zotero.sqlite" <<'PY'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
ok = con.execute('PRAGMA integrity_check').fetchone()[0]
n = con.execute('SELECT COUNT(*) FROM items').fetchone()[0]
if ok != 'ok': sys.exit(f'integrity_check: {ok}')
print(f'origin filled: integrity ok, items: {n}')
PY
  mkdir -p "$(dirname "$BASE")"
  cp -a "$ROOT/config/Zotero/zotero.sqlite" "$BASE"
  if [ -n "$local_was" ]; then docker start zotero >/dev/null; local_was=""; fi
  echo "==> done"
  exit 0
fi

echo "==> origin snapshot"
./scripts/snapshot_db.sh > "$SYNC/snap1.txt"
cat "$SYNC/snap1.txt"

if [ -z "$have_replica_db" ]; then
  echo "replica has no database; refusing to overwrite it implicitly" >&2
  exit 1
fi

# Roll back a fetched hot journal, then verify before comparison.
"$PYTHON" - "$SYNC/replica/zotero.sqlite" <<'PY'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
ok = con.execute('PRAGMA integrity_check').fetchone()[0]
if ok != 'ok': sys.exit(f'fetched replica DB is corrupt: {ok}')
PY

echo "==> three-way fast-forward plan"
base_arg=()
if [ -f "$BASE" ]; then base_arg=(--base "$BASE"); fi
"$PYTHON" ./scripts/merge_replica.py plan \
  --origin "$ROOT/config/Zotero/.snapshot/zotero.sqlite" \
  --replica "$SYNC/replica/zotero.sqlite" "${base_arg[@]}" \
  --out "$SYNC/plan.json"

if ! "$PYTHON" -c "import json,sys; s=json.load(open('$SYNC/plan.json'))['stats']; sys.exit(1 if s['origin_items']<10 and s['replica_items']>100 else 0)"; then
  echo "!! origin is nearly empty while replica is full; refusing deployment-order loss" >&2
  exit 1
fi

mode="$("$PYTHON" -c "import json; print(json.load(open('$SYNC/plan.json'))['mode'])")"
reason="$("$PYTHON" -c "import json; print(json.load(open('$SYNC/plan.json'))['stats']['reason'])")"
echo "==> $mode: $reason"
if [ "$mode" = conflict ]; then
  echo "!! stopped before mutation; inspect $SYNC/plan.json" >&2
  exit 2
fi

if [ "$mode" = replica_fast_forward ]; then
  echo "==> replica is authoritative; pulling storage without deleting local cache"
  mkdir -p "$ROOT/config/Zotero/storage"
  rsync -az --partial "${DRY[@]}" --info=stats1 \
    "$(rpath)/config/Zotero/storage/" "$ROOT/config/Zotero/storage/"
  if [ ${#DRY[@]} -gt 0 ]; then
    echo "==> dry-run: replica snapshot would replace the unchanged origin; no push performed"
    exit 0
  fi

  if [ -n "$(docker ps -q -f name='^zotero$' 2>/dev/null || true)" ]; then
    docker stop zotero >/dev/null
    local_was=1
  fi
  mkdir -p "$SYNC/pre-fast-forward" "$ROOT/config/Zotero/.snapshot"
  cp -a "$ROOT/config/Zotero/.snapshot/zotero.sqlite" \
    "$SYNC/pre-fast-forward/origin.sqlite"
  rm -f "$ROOT/config/Zotero/.snapshot/zotero.sqlite"{,-journal,-wal,-shm}
  cp -a "$SYNC/replica/zotero.sqlite" "$ROOT/config/Zotero/.snapshot/zotero.sqlite"
  replica_items="$("$PYTHON" -c "import json; print(json.load(open('$SYNC/plan.json'))['stats']['replica_items'])")"
  local_phase=place
  ./scripts/place_snapshot.sh "$replica_items"
  local_phase=done
  if [ -n "$local_was" ]; then docker start zotero >/dev/null; local_was=""; fi
  echo "==> origin fast-forwarded with replica keys unchanged"
elif [ "$mode" = equal ]; then
  echo "==> peers already equal"
else
  echo "==> origin contains the winning snapshot"
fi

want_items="$("$PYTHON" -c "import sqlite3; c=sqlite3.connect('file:$ROOT/config/Zotero/.snapshot/zotero.sqlite?mode=ro',uri=True); print(c.execute('select count(*) from items').fetchone()[0])")"

# Push the winning data and snapshot, then atomically install the DB remotely.
phase=push
rsh "mkdir -p '$RROOT/config'"
echo "==> rsync push: data"
rsync -az --partial --delete "${DRY[@]}" --info=stats1 \
  --exclude '/zotero.sqlite' --exclude '/zotero.sqlite-*' \
  --exclude '/zotero-mcp-vectors.sqlite' --exclude '/zotero-mcp-vectors.sqlite-*' \
  --exclude '*.bak' --exclude '*.prev' \
  "$ROOT/config/Zotero/" "$(rpath)/config/Zotero/"

echo "==> rsync push: profile"
rsync -az --partial --delete "${DRY[@]}" --info=stats1 \
  --exclude '.parentlock' --exclude 'crashes/' --exclude 'datareporting/' \
  --exclude 'places.sqlite*' --exclude '*-wal' --exclude '*-shm' \
  --exclude 'Telemetry*' \
  "$ROOT/config/.zotero/" "$(rpath)/config/.zotero/"

if [ ${#DRY[@]} -gt 0 ]; then
  echo "==> dry-run: DB swap and common base untouched"
  exit 0
fi

echo "==> swapping the DB on the replica"
phase=place
rsh "'$RROOT/scripts/place_snapshot.sh' '$want_items'"
phase=done

# Advance the common ancestor only after both peers accepted the winner.
mkdir -p "$(dirname "$BASE")"
cp -a "$ROOT/config/Zotero/.snapshot/zotero.sqlite" "$BASE"

if [ -n "$was_running" ]; then
  echo "==> starting zotero on the replica"
  rsh "docker start zotero >/dev/null"
  was_running=""
fi
echo "==> done"
