#!/usr/bin/env python3
"""Empty the Zotero trash in an offline snapshot, as Zotero's own Empty Trash does.

    empty_trash.py --db SNAPSHOT.sqlite [--out removed.json] [--dry-run]

Removes every trashed item together with its child attachments, notes and
annotations, and every trashed collection; foreign keys cascade the dependent
rows. Tags, creators and field values no other item uses are purged. Writes the
removed keys; attachment keys name the `storage/<KEY>/` folders to delete when
the snapshot is installed. Never run it on the live database of a running Zotero.
"""
import argparse
import json
import sqlite3

import merge_replica


def trashed(con):
    items = {i for (i,) in con.execute("SELECT itemID FROM deletedItems")}
    while True:
        children = {i for (i,) in con.execute(
            "SELECT itemID FROM itemAttachments WHERE parentItemID IN (SELECT value FROM json_each(?)) "
            "UNION SELECT itemID FROM itemNotes WHERE parentItemID IN (SELECT value FROM json_each(?)) "
            "UNION SELECT itemID FROM itemAnnotations WHERE parentItemID IN (SELECT value FROM json_each(?))",
            [json.dumps(sorted(items))] * 3)} - items
        if not children:
            return items
        items |= children


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True)
    parser.add_argument("--out")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    before = merge_replica.load(args.db)
    con = sqlite3.connect(args.db)
    con.execute("PRAGMA foreign_keys=ON")
    items = trashed(con)
    ids = json.dumps(sorted(items))
    rows = con.execute("SELECT i.key, a.itemID IS NOT NULL FROM items i LEFT JOIN itemAttachments a USING(itemID) "
                       "WHERE i.itemID IN (SELECT value FROM json_each(?))", (ids,)).fetchall()
    collections = [k for (k,) in con.execute(
        "SELECT key FROM collections WHERE collectionID IN (SELECT collectionID FROM deletedCollections)")]
    removed = {"items": sorted(k for k, _ in rows), "attachments": sorted(k for k, a in rows if a),
               "collections": sorted(collections)}
    if not args.dry_run:
        con.execute("DELETE FROM items WHERE itemID IN (SELECT value FROM json_each(?))", (ids,))
        con.execute("DELETE FROM collections WHERE collectionID IN (SELECT collectionID FROM deletedCollections)")
        con.execute("DELETE FROM tags WHERE tagID NOT IN (SELECT tagID FROM itemTags)")
        con.execute("DELETE FROM creators WHERE creatorID NOT IN (SELECT creatorID FROM itemCreators)")
        con.execute("DELETE FROM itemDataValues WHERE valueID NOT IN (SELECT valueID FROM itemData)")
        con.commit()
        problems = con.execute("PRAGMA foreign_key_check").fetchall()
        if problems or con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise SystemExit(f"checks failed: {problems[:5]}")
        con.close()
        # Only the trashed objects and their memberships may disappear.
        delta = merge_replica._delta(before, merge_replica.load(args.db))
        gone = set(removed["items"]) | set(removed["collections"])
        unexpected = [k for k, op in delta.items() if op != ("remove",) or
                      (k[0] in ("item", "collection") and k[1] not in gone) or
                      (k[0] == "membership" and k[1] not in gone and k[2] not in gone)]
        if unexpected:
            raise SystemExit(f"unexpected changes: {unexpected[:5]}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(removed, handle, indent=2)
            handle.write("\n")
    print("empty-trash:", json.dumps({k: len(v) for k, v in removed.items()}))


if __name__ == "__main__":
    main()
