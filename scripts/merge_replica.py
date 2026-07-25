#!/usr/bin/env python3
"""Merge replica edits into the origin Zotero library: diff two sqlite copies,
then replay the replica's edits into the origin's live Zotero via its MCP
(invoked by sync_library.sh; the whole cycle: SETUP.md, "Library sync between
peers").

    merge_replica.py plan  --origin SNAP.sqlite --replica REPLICA.sqlite --out plan.json
    merge_replica.py apply plan.json [--replica-storage DIR] [--serve-ip IP] [--rescue DIR]

Replayed: new regular items (+fields, creators, tags), child/standalone
notes, note edits (fresher clientDateModified wins), tags on shared items,
collections and membership, file attachments with a mappable parent (files
are fetched by sync_library.sh), trashing (unless the origin edited the item
later). NOT replayed (origin wins, everything is logged): field edits of
shared items, annotations, standalone/linked attachments (their files go to
rescue). New objects get origin keys; no duplicates on later cycles because
the replica DB is replaced wholesale with the merged copy.
"""
import argparse, json, os, sqlite3, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SKIP_TYPES = {"note", "attachment", "annotation"}
MAX_PAYLOAD = 3300          # MCP: bodies over ~3700 bytes → -32700 Parse error


def load(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    db = {"items": {}, "notes": {}, "atts": {}, "fields": {}, "creators": {},
          "tags": {}, "colls": {}, "members": set(), "trashed": {}}
    for r in con.execute(
            "SELECT i.itemID id, i.key, t.typeName tn, i.clientDateModified cdm "
            "FROM items i JOIN itemTypes t USING(itemTypeID) WHERE i.libraryID=1"):
        db["items"][r["key"]] = {"id": r["id"], "type": r["tn"], "cdm": r["cdm"]}
    byid = {v["id"]: k for k, v in db["items"].items()}
    for r in con.execute("SELECT itemID, dateDeleted FROM deletedItems"):
        if r["itemID"] in byid:
            db["trashed"][byid[r["itemID"]]] = r["dateDeleted"] or ""
    for r in con.execute("SELECT itemID, parentItemID, note, title FROM itemNotes"):
        k = byid.get(r["itemID"])
        if k:
            db["notes"][k] = {"parent": byid.get(r["parentItemID"]),
                              "note": r["note"] or "", "title": r["title"] or ""}
    for r in con.execute(
            "SELECT itemID, parentItemID, linkMode, contentType, path FROM itemAttachments"):
        k = byid.get(r["itemID"])
        if k:
            db["atts"][k] = {"parent": byid.get(r["parentItemID"]),
                             "linkMode": r["linkMode"], "ct": r["contentType"],
                             "path": r["path"] or ""}
    for r in con.execute(
            "SELECT id.itemID, f.fieldName, v.value FROM itemData id "
            "JOIN fields f USING(fieldID) JOIN itemDataValues v USING(valueID)"):
        k = byid.get(r["itemID"])
        if k:
            db["fields"].setdefault(k, {})[r["fieldName"]] = r["value"]
    for r in con.execute(
            "SELECT ic.itemID, ct.creatorType, c.firstName, c.lastName, c.fieldMode "
            "FROM itemCreators ic JOIN creators c USING(creatorID) "
            "JOIN creatorTypes ct USING(creatorTypeID) ORDER BY ic.itemID, ic.orderIndex"):
        k = byid.get(r["itemID"])
        if k:
            c = {"creatorType": r["creatorType"]}
            if r["fieldMode"] == 1:
                c["name"] = r["lastName"] or ""
            else:
                c["firstName"], c["lastName"] = r["firstName"] or "", r["lastName"] or ""
            db["creators"].setdefault(k, []).append(c)
    for r in con.execute(
            "SELECT it.itemID, t.name FROM itemTags it JOIN tags t USING(tagID)"):
        k = byid.get(r["itemID"])
        if k:
            db["tags"].setdefault(k, set()).add(r["name"])
    cbyid = {}
    for r in con.execute(
            "SELECT collectionID, collectionName, parentCollectionID, key "
            "FROM collections WHERE libraryID=1"):
        cbyid[r["collectionID"]] = r["key"]
        db["colls"][r["key"]] = {"name": r["collectionName"],
                                 "parentID": r["parentCollectionID"]}
    for c in db["colls"].values():
        c["parent"] = cbyid.get(c["parentID"])
    for r in con.execute("SELECT collectionID, itemID FROM collectionItems"):
        ck, ik = cbyid.get(r["collectionID"]), byid.get(r["itemID"])
        if ck and ik:
            db["members"].add((ck, ik))
    con.close()
    return db


def build_plan(origin, replica):
    o, r = load(origin), load(replica)
    plan = {"collections": [], "items": [], "notes": [], "note_updates": [],
            "tags": [], "memberships": [], "attachments": [], "trash": [],
            "storage_keys": [], "rescue_keys": [], "conflict_notes": [],
            "stats": {}}
    skipped = {"deleted_on_replica": 0, "field_edits": 0, "annotations": 0,
               "att_no_file_or_parent": 0}
    new = {k for k in r["items"] if k not in o["items"] and k not in r["trashed"]}

    for k in sorted(r["trashed"]):
        if k in o["items"] and k not in o["trashed"]:
            if r["trashed"][k] > o["items"][k]["cdm"]:
                plan["trash"].append({"key": k})
            else:
                skipped["deleted_on_replica"] += 1

    # new collections, parents first
    newc = [k for k in r["colls"] if k not in o["colls"]]
    done, order = set(), []
    while newc:
        for k in list(newc):
            p = r["colls"][k]["parent"]
            if p is None or p in o["colls"] or p in done:
                order.append(k); done.add(k); newc.remove(k)
                break
        else:
            order.extend(newc); newc = []          # cycle/broken parent — as is
    for k in order:
        c = r["colls"][k]
        p = c["parent"]
        plan["collections"].append({"rkey": k, "name": c["name"],
            "parent": None if p is None else
                      ({"ref": f"origin:{p}"} if p in o["colls"] else {"ref": f"new:{p}"})})

    for k in sorted(new):
        it = r["items"][k]
        if it["type"] == "annotation":
            skipped["annotations"] += 1
        elif it["type"] == "note":
            n = r["notes"].get(k, {"parent": None, "note": ""})
            p = n["parent"]
            if p is None:
                ref = None
            elif p in o["items"] and o["items"][p]["type"] not in SKIP_TYPES:
                ref = {"ref": f"origin:{p}"}
            elif p in new and r["items"][p]["type"] not in SKIP_TYPES:
                ref = {"ref": f"new:{p}"}
            else:
                ref = None                          # unmappable parent → standalone
            plan["notes"].append({"rkey": k, "parent": ref, "content": n["note"],
                                  "tags": sorted(r["tags"].get(k, set()))})
        elif it["type"] == "attachment":
            a = r["atts"][k]
            fn = a["path"][8:] if a["path"].startswith("storage:") else ""
            p = a["parent"]
            pref = ({"ref": f"origin:{p}"} if p in o["items"] else
                    {"ref": f"new:{p}"} if p in new else None)
            if a["linkMode"] in (0, 1) and fn and pref:
                plan["attachments"].append({"rkey": k, "parent": pref, "file": fn,
                    "ct": a["ct"] or "application/pdf",
                    "title": r["fields"].get(k, {}).get("title") or fn})
                plan["storage_keys"].append(k)
            else:
                skipped["att_no_file_or_parent"] += 1
                if a["linkMode"] in (0, 1) and fn:
                    plan["rescue_keys"].append(k)
        else:
            plan["items"].append({"rkey": k, "itemType": it["type"],
                "fields": r["fields"].get(k, {}),
                "creators": r["creators"].get(k, []),
                "tags": sorted(r["tags"].get(k, set()))})

    common = [k for k in r["items"]
              if k in o["items"] and k not in r["trashed"] and k not in o["trashed"]]
    for k in common:
        if k in r["notes"] and k in o["notes"]:
            if r["notes"][k]["note"] != o["notes"][k]["note"]:
                if r["items"][k]["cdm"] > o["items"][k]["cdm"]:
                    plan["note_updates"].append({"key": k, "content": r["notes"][k]["note"]})
                else:
                    plan["conflict_notes"].append({"key": k, "content": r["notes"][k]["note"]})
        elif r["items"][k]["type"] not in SKIP_TYPES:
            if (r["fields"].get(k, {}) != o["fields"].get(k, {})
                    and r["items"][k]["cdm"] > o["items"][k]["cdm"]):
                skipped["field_edits"] += 1
        add = r["tags"].get(k, set()) - o["tags"].get(k, set())
        if add:
            plan["tags"].append({"key": k, "tags": sorted(add)})

    for (ck, ik) in sorted(r["members"] - o["members"]):
        cref = ({"ref": f"origin:{ck}"} if ck in o["colls"] else
                {"ref": f"newcoll:{ck}"} if any(c["rkey"] == ck for c in plan["collections"]) else None)
        iref = ({"ref": f"origin:{ik}"} if ik in o["items"] else
                {"ref": f"new:{ik}"} if ik in new else None)
        if cref and iref:
            plan["memberships"].append({"coll": cref, "item": iref})

    plan["stats"] = {"origin_items": len(o["items"]), "replica_items": len(r["items"]),
                     "new_items": len(plan["items"]), "new_notes": len(plan["notes"]),
                     "note_updates": len(plan["note_updates"]),
                     "tag_additions": len(plan["tags"]),
                     "new_collections": len(plan["collections"]),
                     "memberships": len(plan["memberships"]),
                     "attachments": len(plan["attachments"]),
                     "trash": len(plan["trash"]),
                     "skipped": skipped}
    return plan


def shrink(args_dict):
    """Trim the longest fields until the JSON fits the MCP body limit."""
    while len(json.dumps(args_dict, ensure_ascii=False).encode()) > MAX_PAYLOAD:
        f = args_dict.get("fields") or {}
        if not f:
            break
        top = max(f, key=lambda k: len(f[k]))
        if len(f[top]) <= 200:
            break
        f[top] = f[top][: max(200, len(f[top]) // 2)] + "…"
    return args_dict


def apply_plan(plan, storage, serve_ip, rescue):
    from zotero_mcp import MCP, item_key, result_text
    mcp = MCP("merge_replica")
    imap, cmap, fail = {}, {}, []

    def ref(x, m):
        if x is None:
            return None
        kind, key = x["ref"].split(":", 1)
        return key if kind == "origin" else m.get(key)

    def ok(resp):
        try:
            return not resp.get("error") and not resp["result"].get("isError")
        except Exception:
            return False

    for c in plan["collections"]:
        args = {"name": c["name"]}
        p = ref(c["parent"], cmap)
        if p:
            args["parentCollectionKey"] = p
        resp = mcp.call("create_collection", args)
        k = item_key(resp)
        if k:
            cmap[c["rkey"]] = k
        else:
            fail.append(("collection", c["rkey"], result_text(resp)[:100]))

    for it in plan["items"]:
        args = shrink({"itemType": it["itemType"], "fields": it["fields"],
                       "creators": it["creators"], "tags": it["tags"]})
        resp = mcp.call("create_item", args)
        k = item_key(resp)
        if k:
            imap[it["rkey"]] = k
        else:
            fail.append(("item", it["rkey"], result_text(resp)[:100]))

    for n in plan["notes"]:
        args = {"content": n["content"], "tags": n["tags"]}
        p = ref(n["parent"], imap)
        if p:
            args["itemKey"] = p
        resp = mcp.call("add_note", args)
        k = item_key(resp)
        if k:
            imap[n["rkey"]] = k
        else:
            fail.append(("note", n["rkey"], result_text(resp)[:100]))
            _save_rescue(rescue, n["rkey"], n["content"])

    for u in plan["note_updates"]:
        resp = mcp.call("update_note", {"noteKey": u["key"], "content": u["content"]})
        if not ok(resp):
            fail.append(("note_update", u["key"], result_text(resp)[:100]))
            _save_rescue(rescue, u["key"], u["content"])

    for t in plan["tags"]:
        resp = mcp.call("add_tags", {"itemKey": t["key"], "tags": t["tags"]})
        if not ok(resp):
            fail.append(("tags", t["key"], result_text(resp)[:100]))

    bycoll = {}
    for m in plan["memberships"]:
        ck = ref(m["coll"], cmap)
        ik = ref(m["item"], imap)
        if ck and ik:
            bycoll.setdefault(ck, []).append(ik)
    for ck, iks in bycoll.items():
        for i in range(0, len(iks), 100):
            resp = mcp.call("batch_add_to_collection",
                            {"collectionKey": ck, "itemKeys": iks[i:i + 100]})
            if not ok(resp):
                fail.append(("membership", ck, result_text(resp)[:100]))

    if plan["attachments"] and storage:
        from zotero_mcp import import_local_files
        byct = {}
        for a in plan["attachments"]:
            p = ref(a["parent"], imap)
            f = os.path.join(storage, a["rkey"], a["file"])
            if p and os.path.isfile(f):
                byct.setdefault(a["ct"], []).append((f, p, a["title"]))
            else:
                fail.append(("attachment", a["rkey"], "missing file or parent"))
        for ct, jobs in byct.items():
            for key, okf, txt in import_local_files(
                    mcp, jobs, serve_ip, content_type=ct, if_exists="add"):
                if not okf:
                    fail.append(("attachment", key, txt))

    for t in plan.get("trash", []):
        resp = mcp.call("trash_item", {"itemKey": t["key"]})
        if not ok(resp):
            fail.append(("trash", t["key"], result_text(resp)[:100]))

    for c in plan["conflict_notes"]:
        _save_rescue(rescue, c["key"] + ".conflict", c["content"])

    print(f"applied: items+notes {len(imap)}, collections {len(cmap)}, "
          f"tags {len(plan['tags'])}, memberships {len(plan['memberships'])}, "
          f"attachments {len(plan['attachments'])}, trashed {len(plan.get('trash', []))}, "
          f"errors {len(fail)}")
    for f3 in fail:
        print("  ERROR:", *f3)
    return 1 if fail else 0


def _save_rescue(rescue, name, content):
    if rescue:
        os.makedirs(rescue, exist_ok=True)
        with open(os.path.join(rescue, f"{name}.html"), "w", encoding="utf-8") as f:
            f.write(content)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("plan")
    p1.add_argument("--origin", required=True)
    p1.add_argument("--replica", required=True)
    p1.add_argument("--out", required=True)
    p2 = sub.add_parser("apply")
    p2.add_argument("plan")
    p2.add_argument("--replica-storage")
    p2.add_argument("--serve-ip")
    p2.add_argument("--rescue")
    a = ap.parse_args()
    if a.cmd == "plan":
        plan = build_plan(a.origin, a.replica)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=1)
        print("plan:", json.dumps(plan["stats"], ensure_ascii=False))
    else:
        with open(a.plan, encoding="utf-8") as f:
            plan = json.load(f)
        sys.exit(apply_plan(plan, a.replica_storage, a.serve_ip, a.rescue))


if __name__ == "__main__":
    main()
