#!/usr/bin/env bash
# Crash-consistent snapshot of the Zotero sqlite DBs while the container runs.
# A running Zotero holds zotero.sqlite under an EXCLUSIVE lock, so the sqlite
# backup API and even read-only connections fail ("database is locked").
# Instead the container is frozen (docker pause) for a fraction of a second,
# the DB is copied together with its hot -journal, and the container resumes.
# The copy is like a power-cut image: sqlite rolls the journal back
# deterministically on first open, which is done right here, followed by an
# integrity_check.
#
#   ./scripts/snapshot_db.sh        # snapshot into config/Zotero/.snapshot/
#
# Invoked over ssh / locally by sync_library.sh; safe to run by hand.

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ZDIR="$ROOT/config/Zotero"
SNAP="$ZDIR/.snapshot"
DBS=(zotero.sqlite zotero-mcp-vectors.sqlite)

[ -f "$ZDIR/zotero.sqlite" ] || { echo "no $ZDIR/zotero.sqlite" >&2; exit 1; }
mkdir -p "$SNAP"
rm -f "$SNAP"/*

paused=""
if [ -n "$(docker ps -q -f name='^zotero$' 2>/dev/null || true)" ]; then
  docker pause zotero >/dev/null
  paused=1
  trap 'docker unpause zotero >/dev/null' EXIT
fi

for db in "${DBS[@]}"; do
  if [ -f "$ZDIR/$db" ]; then
    cp "$ZDIR/$db" "$SNAP/$db"
    for suf in -journal -wal -shm; do
      if [ -f "$ZDIR/$db$suf" ]; then cp "$ZDIR/$db$suf" "$SNAP/$db$suf"; fi
    done
  fi
done

if [ -n "$paused" ]; then
  trap - EXIT
  docker unpause zotero >/dev/null
fi

# Journal rollback (first open) + integrity_check; after the rollback the
# journal is gone and the copy is self-contained. Prints "items: N", which
# sync_library.sh compares against the replica after the swap.
python3 - "$SNAP" <<'PY'
import os, sqlite3, sys
snap = sys.argv[1]
for db in sorted(os.listdir(snap)):
    if not db.endswith('.sqlite'):
        continue
    path = os.path.join(snap, db)
    con = sqlite3.connect(path)
    ok = con.execute('PRAGMA integrity_check').fetchone()[0]
    if ok != 'ok':
        sys.exit(f'{db}: integrity_check: {ok}')
    extra = ''
    try:
        n = con.execute('SELECT COUNT(*) FROM items').fetchone()[0]
        extra = f', items: {n}'
    except sqlite3.Error:
        pass
    con.close()
    print(f'{db}: ok ({os.path.getsize(path) // 2**20} MB{extra})')
PY
