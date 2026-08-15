#!/usr/bin/env python3
"""Classify two Zotero snapshots for identity-preserving synchronization.

The Zotero eight-character object key is the durable identity used by storage
directories, manifests, citations, and external exports.  A sync must never
recreate an object under a different key.

    merge_replica.py plan --origin ORIGIN.sqlite --replica REPLICA.sqlite \
        --out plan.json

Against the last common snapshot (``--base``), the classifier permits only
whole-snapshot fast-forwards:

* ``replica_fast_forward``: every origin object and relationship is unchanged
  on the replica, which only adds objects/relationships;
* ``origin_fast_forward``: the inverse;
* ``equal``: no logical difference;
* ``conflict``: both peers contain changes that the other does not, or no
  common base is available.  The caller must stop before any mutation and
  leave the report for manual resolution or Zotero native sync.

Installing the winning SQLite snapshot preserves item, note, attachment, and
collection keys exactly.  There is deliberately no MCP replay/apply mode.
"""

import argparse
import json
import sqlite3


def _has_table(con, name):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (name,),
    ).fetchone() is not None


def load(path):
    """Load the user-library state using stable keys rather than local IDs."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    db = {
        "items": {},
        "notes": {},
        "atts": {},
        "annotations": {},
        "fields": {},
        "creators": {},
        "tags": {},
        "relations": {},
        "publications": set(),
        "retractions": {},
        "colls": {},
        "coll_relations": {},
        "members": set(),
    }

    for row in con.execute(
        "SELECT i.itemID id, i.key, t.typeName type, i.dateAdded, "
        "i.dateModified, di.itemID IS NOT NULL trashed "
        "FROM items i JOIN itemTypes t USING(itemTypeID) "
        "LEFT JOIN deletedItems di USING(itemID) WHERE i.libraryID=1"
    ):
        db["items"][row["key"]] = {
            "id": row["id"],
            "type": row["type"],
            "dateAdded": row["dateAdded"],
            "dateModified": row["dateModified"],
            "trashed": bool(row["trashed"]),
        }
    by_id = {value["id"]: key for key, value in db["items"].items()}

    for row in con.execute("SELECT itemID, parentItemID, note, title FROM itemNotes"):
        key = by_id.get(row["itemID"])
        if key:
            db["notes"][key] = {
                "parent": by_id.get(row["parentItemID"]),
                "note": row["note"] or "",
                "title": row["title"] or "",
            }

    for row in con.execute(
        "SELECT a.itemID, a.parentItemID, a.linkMode, a.contentType, "
        "c.charset, a.path, a.storageModTime, a.storageHash, a.lastRead "
        "FROM itemAttachments a LEFT JOIN charsets c USING(charsetID)"
    ):
        key = by_id.get(row["itemID"])
        if key:
            db["atts"][key] = {
                "parent": by_id.get(row["parentItemID"]),
                "linkMode": row["linkMode"],
                "contentType": row["contentType"] or "",
                "charset": row["charset"] or "",
                "path": row["path"] or "",
                "storageModTime": row["storageModTime"],
                "storageHash": row["storageHash"] or "",
                "lastRead": row["lastRead"],
            }

    if _has_table(con, "itemAnnotations"):
        for row in con.execute(
            "SELECT itemID, parentItemID, type, authorName, text, comment, "
            "color, pageLabel, sortIndex, position, isExternal FROM itemAnnotations"
        ):
            key = by_id.get(row["itemID"])
            if key:
                db["annotations"][key] = {
                    name: row[name]
                    for name in (
                        "type",
                        "authorName",
                        "text",
                        "comment",
                        "color",
                        "pageLabel",
                        "sortIndex",
                        "position",
                        "isExternal",
                    )
                }
                db["annotations"][key]["parent"] = by_id.get(row["parentItemID"])

    for row in con.execute(
        "SELECT d.itemID, f.fieldName, v.value FROM itemData d "
        "JOIN fieldsCombined f USING(fieldID) JOIN itemDataValues v USING(valueID)"
    ):
        key = by_id.get(row["itemID"])
        if key:
            db["fields"].setdefault(key, {})[row["fieldName"]] = row["value"]

    for row in con.execute(
        "SELECT ic.itemID, ct.creatorType, c.firstName, c.lastName, c.fieldMode "
        "FROM itemCreators ic JOIN creators c USING(creatorID) "
        "JOIN creatorTypes ct USING(creatorTypeID) "
        "ORDER BY ic.itemID, ic.orderIndex"
    ):
        key = by_id.get(row["itemID"])
        if key:
            creator = {"creatorType": row["creatorType"]}
            if row["fieldMode"] == 1:
                creator["name"] = row["lastName"] or ""
            else:
                creator["firstName"] = row["firstName"] or ""
                creator["lastName"] = row["lastName"] or ""
            db["creators"].setdefault(key, []).append(creator)

    for row in con.execute(
        "SELECT it.itemID, t.name, it.type FROM itemTags it JOIN tags t USING(tagID)"
    ):
        key = by_id.get(row["itemID"])
        if key:
            db["tags"].setdefault(key, set()).add((row["name"], row["type"]))

    if _has_table(con, "itemRelations"):
        for row in con.execute(
            "SELECT ir.itemID, rp.predicate, ir.object FROM itemRelations ir "
            "JOIN relationPredicates rp USING(predicateID)"
        ):
            key = by_id.get(row["itemID"])
            if key:
                db["relations"].setdefault(key, set()).add(
                    (row["predicate"], row["object"])
                )

    if _has_table(con, "publicationsItems"):
        db["publications"] = {
            by_id[row[0]]
            for row in con.execute("SELECT itemID FROM publicationsItems")
            if row[0] in by_id
        }
    if _has_table(con, "retractedItems"):
        for row in con.execute("SELECT itemID, data, flag FROM retractedItems"):
            key = by_id.get(row["itemID"])
            if key:
                db["retractions"][key] = {
                    "data": row["data"] or "",
                    "flag": row["flag"],
                }

    collection_by_id = {}
    for row in con.execute(
        "SELECT c.collectionID, c.collectionName, c.parentCollectionID, c.key, "
        "dc.collectionID IS NOT NULL trashed FROM collections c "
        "LEFT JOIN deletedCollections dc USING(collectionID) WHERE c.libraryID=1"
    ):
        collection_by_id[row["collectionID"]] = row["key"]
        db["colls"][row["key"]] = {
            "name": row["collectionName"],
            "parentID": row["parentCollectionID"],
            "trashed": bool(row["trashed"]),
        }
    for collection in db["colls"].values():
        collection["parent"] = collection_by_id.get(collection.pop("parentID"))

    if _has_table(con, "collectionRelations"):
        for row in con.execute(
            "SELECT cr.collectionID, rp.predicate, cr.object "
            "FROM collectionRelations cr JOIN relationPredicates rp USING(predicateID)"
        ):
            key = collection_by_id.get(row["collectionID"])
            if key:
                db["coll_relations"].setdefault(key, set()).add(
                    (row["predicate"], row["object"])
                )

    for row in con.execute("SELECT collectionID, itemID FROM collectionItems"):
        collection_key = collection_by_id.get(row["collectionID"])
        item_key = by_id.get(row["itemID"])
        if collection_key and item_key:
            db["members"].add((collection_key, item_key))
    con.close()

    # Local integer IDs are deliberately excluded from comparisons.
    for item in db["items"].values():
        item.pop("id")
    return db


def _item_payload(db, key):
    payload = dict(db["items"][key])
    for section, default in (
        ("fields", {}),
        ("creators", []),
        ("notes", None),
        ("atts", None),
        ("annotations", None),
        ("retractions", None),
    ):
        payload[section] = db[section].get(key, default)
    payload["tags"] = sorted(db["tags"].get(key, set()))
    payload["relations"] = sorted(db["relations"].get(key, set()))
    payload["inPublications"] = key in db["publications"]
    return payload


def _collection_payload(db, key):
    payload = dict(db["colls"][key])
    payload["relations"] = sorted(db["coll_relations"].get(key, set()))
    return payload


def _changed(left, right, keys, payload):
    changed = []
    for key in sorted(keys):
        a, b = payload(left, key), payload(right, key)
        if a != b:
            changed.append({
                "key": key,
                "aspects": sorted(name for name in set(a) | set(b) if a.get(name) != b.get(name)),
            })
    return changed


def _freeze(value):
    """Turn a nested state payload into a deterministic comparable value."""
    if isinstance(value, dict):
        return tuple(sorted((key, _freeze(item)) for key, item in value.items()))
    if isinstance(value, set):
        return tuple(sorted((_freeze(item) for item in value), key=repr))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _delta(base, current):
    """Logical operations from a common base to one current snapshot."""
    operations = {}
    base_items, current_items = set(base["items"]), set(current["items"])
    for key in base_items | current_items:
        operation_key = ("item", key)
        if key not in current_items:
            operations[operation_key] = ("remove",)
        elif key not in base_items:
            operations[operation_key] = ("add", _freeze(_item_payload(current, key)))
        else:
            before, after = _item_payload(base, key), _item_payload(current, key)
            if before != after:
                operations[operation_key] = ("change", _freeze(after))

    base_colls, current_colls = set(base["colls"]), set(current["colls"])
    for key in base_colls | current_colls:
        operation_key = ("collection", key)
        if key not in current_colls:
            operations[operation_key] = ("remove",)
        elif key not in base_colls:
            operations[operation_key] = (
                "add",
                _freeze(_collection_payload(current, key)),
            )
        else:
            before = _collection_payload(base, key)
            after = _collection_payload(current, key)
            if before != after:
                operations[operation_key] = ("change", _freeze(after))

    for pair in base["members"] | current["members"]:
        if (pair in base["members"]) != (pair in current["members"]):
            operations[("membership",) + pair] = (
                "add" if pair in current["members"] else "remove",
            )
    return operations


def _same_state(left, right):
    return not _delta(left, right)


def _is_subset_delta(left, right):
    return all(right.get(key) == value for key, value in left.items())


def classify(origin, replica, base=None):
    """Return a JSON-serializable, three-way fast-forward/conflict plan."""
    origin_items, replica_items = set(origin["items"]), set(replica["items"])
    origin_colls, replica_colls = set(origin["colls"]), set(replica["colls"])
    changed_items = _changed(
        origin, replica, origin_items & replica_items, _item_payload
    )
    changed_colls = _changed(
        origin, replica, origin_colls & replica_colls, _collection_payload
    )
    origin_only_members = origin["members"] - replica["members"]
    replica_only_members = replica["members"] - origin["members"]

    origin_delta = _delta(base, origin) if base is not None else None
    replica_delta = _delta(base, replica) if base is not None else None

    if _same_state(origin, replica):
        mode = "equal"
        reason = "snapshots are logically equal"
    elif base is None:
        mode = "conflict"
        reason = "no last-common base; refusing to guess which peer changed"
    elif not origin_delta:
        mode = "replica_fast_forward"
        reason = "only the replica changed since the common base"
    elif not replica_delta:
        mode = "origin_fast_forward"
        reason = "only the origin changed since the common base"
    elif _is_subset_delta(origin_delta, replica_delta):
        mode = "replica_fast_forward"
        reason = "the replica contains every origin change plus additional changes"
    elif _is_subset_delta(replica_delta, origin_delta):
        mode = "origin_fast_forward"
        reason = "the origin contains every replica change plus additional changes"
    else:
        mode = "conflict"
        reason = "both peers contain changes absent from the other"

    report = {
        "origin_only_items": sorted(origin_items - replica_items),
        "replica_only_items": sorted(replica_items - origin_items),
        "changed_items": changed_items,
        "origin_only_collections": sorted(origin_colls - replica_colls),
        "replica_only_collections": sorted(replica_colls - origin_colls),
        "changed_collections": changed_colls,
        "origin_only_memberships": [list(pair) for pair in sorted(origin_only_members)],
        "replica_only_memberships": [list(pair) for pair in sorted(replica_only_members)],
    }
    stats = {
        "mode": mode,
        "reason": reason,
        "has_base": base is not None,
        "origin_items": len(origin_items),
        "replica_items": len(replica_items),
        "origin_collections": len(origin_colls),
        "replica_collections": len(replica_colls),
        "origin_only_items": len(report["origin_only_items"]),
        "replica_only_items": len(report["replica_only_items"]),
        "changed_items": len(changed_items),
        "origin_only_collections": len(report["origin_only_collections"]),
        "replica_only_collections": len(report["replica_only_collections"]),
        "changed_collections": len(changed_colls),
        "origin_only_memberships": len(origin_only_members),
        "replica_only_memberships": len(replica_only_members),
        "origin_changes_from_base": None if origin_delta is None else len(origin_delta),
        "replica_changes_from_base": None if replica_delta is None else len(replica_delta),
    }
    return {"schema": 2, "mode": mode, "stats": stats, "conflicts": report}


def build_plan(origin_path, replica_path, base_path=None):
    return classify(
        load(origin_path),
        load(replica_path),
        load(base_path) if base_path else None,
    )


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("--origin", required=True)
    plan_parser.add_argument("--replica", required=True)
    plan_parser.add_argument("--base")
    plan_parser.add_argument("--out", required=True)
    args = parser.parse_args()

    plan = build_plan(args.origin, args.replica, args.base)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(plan, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print("plan:", json.dumps(plan["stats"], ensure_ascii=False))


if __name__ == "__main__":
    main()
