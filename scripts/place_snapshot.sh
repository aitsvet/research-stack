#!/usr/bin/env bash
# Swap the live Zotero DBs with the snapshots from config/Zotero/.snapshot/
# and verify. Runs ON THE REPLICA (normally over ssh from sync_library.sh)
# while its zotero container is stopped. The previous DB is kept as *.prev.
#
#   ./scripts/place_snapshot.sh [expected_item_count]

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
Z="$ROOT/config/Zotero"
want="${1-}"

[ -f "$Z/.snapshot/zotero.sqlite" ] || { echo "no $Z/.snapshot/zotero.sqlite" >&2; exit 1; }

for db in zotero.sqlite zotero-mcp-vectors.sqlite; do
  if [ -f "$Z/.snapshot/$db" ]; then
    if [ -f "$Z/$db" ]; then cp -a "$Z/$db" "$Z/$db.prev"; fi
    rm -f "$Z/$db-journal" "$Z/$db-wal" "$Z/$db-shm"
    # cp, not mv: the copy left in .snapshot is the delta base for the next rsync
    cp -a "$Z/.snapshot/$db" "$Z/$db"
  fi
done

python3 - "$Z/zotero.sqlite" "$want" <<'PY'
import sqlite3, sys
path, want = sys.argv[1], sys.argv[2]
try:
    con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    ok = con.execute('PRAGMA integrity_check').fetchone()[0]
    got = con.execute('SELECT COUNT(*) FROM items').fetchone()[0]
    if ok != 'ok':
        raise RuntimeError(f'integrity_check: {ok}')
    if want and int(want) != got:
        raise RuntimeError(f'items: {got}, origin had {want}')
except Exception as e:
    sys.exit(f'{e}; rollback: cp {path}.prev {path}')
print(f'replica: integrity ok, items: {got}')
PY
