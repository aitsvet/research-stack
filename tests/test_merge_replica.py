import copy
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from merge_replica import classify  # noqa: E402


def state():
    return {
        "items": {
            "AAAAAAAA": {
                "type": "book",
                "dateAdded": "2026-01-01 00:00:00",
                "dateModified": "2026-01-01 00:00:00",
                "trashed": False,
            }
        },
        "notes": {},
        "atts": {},
        "annotations": {},
        "fields": {"AAAAAAAA": {"title": "Stable identity"}},
        "creators": {},
        "tags": {},
        "relations": {},
        "publications": set(),
        "retractions": {},
        "colls": {"CCCCCCCC": {"name": "Corpus", "parent": None, "trashed": False}},
        "coll_relations": {},
        "members": {("CCCCCCCC", "AAAAAAAA")},
    }


class ClassifyTests(unittest.TestCase):
    def test_equal(self):
        self.assertEqual(classify(state(), copy.deepcopy(state()))["mode"], "equal")

    def test_replica_fast_forward_preserves_new_keys(self):
        origin, replica = state(), copy.deepcopy(state())
        replica["items"]["BBBBBBBB"] = {
            "type": "attachment",
            "dateAdded": "2026-01-02 00:00:00",
            "dateModified": "2026-01-02 00:00:00",
            "trashed": False,
        }
        replica["atts"]["BBBBBBBB"] = {
            "parent": "AAAAAAAA",
            "linkMode": 0,
            "contentType": "application/pdf",
            "charset": "",
            "path": "storage:paper.pdf",
            "storageModTime": 1,
            "storageHash": "abc",
            "lastRead": None,
        }
        plan = classify(origin, replica, copy.deepcopy(origin))
        self.assertEqual(plan["mode"], "replica_fast_forward")
        self.assertEqual(plan["conflicts"]["replica_only_items"], ["BBBBBBBB"])
        self.assertNotIn("key_map", plan)

    def test_origin_fast_forward(self):
        replica, origin = state(), copy.deepcopy(state())
        origin["items"]["BBBBBBBB"] = {
            "type": "note",
            "dateAdded": "2026-01-02 00:00:00",
            "dateModified": "2026-01-02 00:00:00",
            "trashed": False,
        }
        origin["notes"]["BBBBBBBB"] = {
            "parent": "AAAAAAAA",
            "note": "Text",
            "title": "",
        }
        self.assertEqual(
            classify(origin, replica, copy.deepcopy(replica))["mode"],
            "origin_fast_forward",
        )

    def test_different_shared_key_edits_are_conflict(self):
        base = state()
        origin, replica = copy.deepcopy(base), copy.deepcopy(base)
        origin["fields"]["AAAAAAAA"]["title"] = "Origin edit"
        replica["fields"]["AAAAAAAA"]["title"] = "Different object"
        plan = classify(origin, replica, base)
        self.assertEqual(plan["mode"], "conflict")
        self.assertEqual(plan["conflicts"]["changed_items"][0]["key"], "AAAAAAAA")

    def test_independent_additions_are_conflict(self):
        origin, replica = state(), copy.deepcopy(state())
        origin["items"]["OOOOOOOO"] = copy.deepcopy(origin["items"]["AAAAAAAA"])
        replica["items"]["RRRRRRRR"] = copy.deepcopy(replica["items"]["AAAAAAAA"])
        plan = classify(origin, replica, state())
        self.assertEqual(plan["mode"], "conflict")
        self.assertEqual(plan["stats"]["origin_only_items"], 1)
        self.assertEqual(plan["stats"]["replica_only_items"], 1)

    def test_replica_removal_is_a_known_change_with_a_base(self):
        origin, replica = state(), copy.deepcopy(state())
        replica["members"].clear()
        self.assertEqual(
            classify(origin, replica, copy.deepcopy(origin))["mode"],
            "replica_fast_forward",
        )

    def test_no_base_refuses_to_guess_direction(self):
        origin, replica = state(), copy.deepcopy(state())
        replica["members"].clear()
        plan = classify(origin, replica)
        self.assertEqual(plan["mode"], "conflict")
        self.assertIn("no last-common base", plan["stats"]["reason"])


if __name__ == "__main__":
    unittest.main()


import json  # noqa: E402
import shutil  # noqa: E402
import sqlite3  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402

import merge_replica  # noqa: E402

SCHEMA = """
CREATE TABLE itemTypes (itemTypeID INTEGER PRIMARY KEY, typeName TEXT);
CREATE VIEW itemTypesCombined AS SELECT itemTypeID, typeName FROM itemTypes;
CREATE TABLE fields (fieldID INTEGER PRIMARY KEY, fieldName TEXT);
CREATE VIEW fieldsCombined AS SELECT fieldID, fieldName FROM fields;
CREATE TABLE creatorTypes (creatorTypeID INTEGER PRIMARY KEY, creatorType TEXT);
CREATE TABLE charsets (charsetID INTEGER PRIMARY KEY, charset TEXT);
CREATE TABLE items (itemID INTEGER PRIMARY KEY, itemTypeID INT, dateAdded, dateModified,
  clientDateModified, libraryID INT, key TEXT, version INT DEFAULT 0, synced INT DEFAULT 0);
CREATE TABLE deletedItems (itemID INTEGER PRIMARY KEY REFERENCES items ON DELETE CASCADE, dateDeleted);
CREATE TABLE itemDataValues (valueID INTEGER PRIMARY KEY, value UNIQUE);
CREATE TABLE itemData (itemID INT REFERENCES items ON DELETE CASCADE, fieldID INT, valueID INT,
  PRIMARY KEY (itemID, fieldID));
CREATE TABLE creators (creatorID INTEGER PRIMARY KEY, firstName, lastName, fieldMode INT);
CREATE TABLE itemCreators (itemID INT REFERENCES items ON DELETE CASCADE, creatorID INT,
  creatorTypeID INT, orderIndex INT, PRIMARY KEY (itemID, creatorID, creatorTypeID, orderIndex));
CREATE TABLE tags (tagID INTEGER PRIMARY KEY, name UNIQUE);
CREATE TABLE itemTags (itemID INT REFERENCES items ON DELETE CASCADE, tagID INT, type INT,
  PRIMARY KEY (itemID, tagID));
CREATE TABLE itemNotes (itemID INTEGER PRIMARY KEY REFERENCES items ON DELETE CASCADE,
  parentItemID INT REFERENCES items ON DELETE CASCADE, note, title);
CREATE TABLE itemAttachments (itemID INTEGER PRIMARY KEY REFERENCES items ON DELETE CASCADE,
  parentItemID INT REFERENCES items ON DELETE CASCADE, linkMode INT, contentType, charsetID INT,
  path, syncState INT DEFAULT 0, storageModTime INT, storageHash, lastProcessedModificationTime INT,
  lastRead INT);
CREATE TABLE itemAnnotations (itemID INTEGER PRIMARY KEY REFERENCES items ON DELETE CASCADE,
  parentItemID INT REFERENCES items ON DELETE CASCADE, type, authorName, text, comment, color,
  pageLabel, sortIndex, position, isExternal);
CREATE TABLE collections (collectionID INTEGER PRIMARY KEY, collectionName, parentCollectionID INT,
  clientDateModified, libraryID INT, key TEXT, version INT DEFAULT 0, synced INT DEFAULT 0);
CREATE TABLE deletedCollections (collectionID INTEGER PRIMARY KEY, dateDeleted);
CREATE TABLE collectionItems (collectionID INT REFERENCES collections ON DELETE CASCADE,
  itemID INT REFERENCES items ON DELETE CASCADE, orderIndex INT, PRIMARY KEY (collectionID, itemID));
INSERT INTO itemTypes VALUES (1, 'book'), (2, 'attachment');
INSERT INTO fields VALUES (1, 'title');
INSERT INTO creatorTypes VALUES (1, 'author');
INSERT INTO charsets VALUES (1, 'utf-8');
INSERT INTO collections VALUES (1, 'Corpus', NULL, '', 1, 'CCCCCCCC', 0, 0);
"""


def add_book(con, key, title, collection=True, padding=0):
    # Padding rows make local integer IDs diverge between peers on purpose.
    for _ in range(padding):
        con.execute("INSERT INTO itemDataValues (value) VALUES (?)", (f"pad-{key}-{_}",))
    item = con.execute("INSERT INTO items (itemTypeID, dateAdded, dateModified, clientDateModified, "
                       "libraryID, key) VALUES (1, 'd', 'd', 'd', 1, ?)", (key,)).lastrowid
    value = con.execute("INSERT OR IGNORE INTO itemDataValues (value) VALUES (?)", (title,)).lastrowid
    value = con.execute("SELECT valueID FROM itemDataValues WHERE value=?", (title,)).fetchone()[0]
    con.execute("INSERT INTO itemData VALUES (?, 1, ?)", (item, value))
    creator = con.execute("INSERT INTO creators (firstName, lastName, fieldMode) VALUES ('A', ?, 0)",
                          (title,)).lastrowid
    con.execute("INSERT INTO itemCreators VALUES (?, ?, 1, 0)", (item, creator))
    tag = con.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", ("t-" + title,)).lastrowid
    tag = con.execute("SELECT tagID FROM tags WHERE name=?", ("t-" + title,)).fetchone()[0]
    con.execute("INSERT INTO itemTags VALUES (?, ?, 0)", (item, tag))
    if collection:
        con.execute("INSERT INTO collectionItems VALUES (1, ?, 0)", (item,))
    return item


def add_attachment(con, key, parent):
    item = con.execute("INSERT INTO items (itemTypeID, dateAdded, dateModified, clientDateModified, "
                       "libraryID, key) VALUES (2, 'd', 'd', 'd', 1, ?)", (key,)).lastrowid
    con.execute("INSERT INTO itemAttachments (itemID, parentItemID, linkMode, contentType, charsetID, path) "
                "VALUES (?, ?, 0, 'application/pdf', 1, 'storage:f.pdf')", (item, parent))
    return item


class UnionTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.dir)
        base = self.dir / "base.sqlite"
        con = sqlite3.connect(base); con.executescript(SCHEMA)
        add_book(con, "AAAAAAAA", "shared"); con.commit(); con.close()
        self.base = base
        self.origin, self.replica = self.dir / "origin.sqlite", self.dir / "replica.sqlite"
        shutil.copy(base, self.origin); shutil.copy(base, self.replica)

    def edit(self, path, change):
        con = sqlite3.connect(path); change(con); con.commit(); con.close()

    def test_disjoint_additions_union_keeps_keys_and_fast_forwards_both(self):
        self.edit(self.origin, lambda c: add_book(c, "OOOOOOOO", "origin"))
        def replica(con):
            book = add_book(con, "RRRRRRRR", "replica", padding=3)
            add_attachment(con, "RATTACH1", book)
        self.edit(self.replica, replica)
        out = self.dir / "union.sqlite"
        self.assertEqual({"items": 2, "collections": 0, "memberships": 1},
                         merge_replica.union(str(self.origin), str(self.replica), str(self.base), str(out)))
        merged = merge_replica.load(str(out))
        self.assertEqual({"AAAAAAAA", "OOOOOOOO", "RRRRRRRR", "RATTACH1"}, set(merged["items"]))
        self.assertEqual("RRRRRRRR", merged["atts"]["RATTACH1"]["parent"])
        self.assertEqual("replica", merged["fields"]["RRRRRRRR"]["title"])
        for peer in (self.origin, self.replica):
            self.assertEqual("origin_fast_forward",
                             merge_replica.classify(merged, merge_replica.load(str(peer)),
                                                    merge_replica.load(str(self.base)))["mode"])

    def test_union_refuses_a_replica_change(self):
        self.edit(self.origin, lambda c: add_book(c, "OOOOOOOO", "origin"))
        self.edit(self.replica, lambda c: c.execute("UPDATE itemDataValues SET value='edited' "
                                                     "WHERE value='shared'"))
        with self.assertRaisesRegex(ValueError, "not a disjoint union"):
            merge_replica.union(str(self.origin), str(self.replica), str(self.base),
                                str(self.dir / "union.sqlite"))

    def test_empty_trash_removes_trashed_items_with_children_only(self):
        def trash(con):
            book = add_book(con, "TTTTTTTT", "trashed")
            add_attachment(con, "TATTACH1", book)
            con.execute("INSERT INTO deletedItems VALUES (?, 'd')", (book,))
        self.edit(self.origin, trash)
        out = self.dir / "removed.json"
        script = Path(__file__).resolve().parents[1] / "scripts" / "empty_trash.py"
        subprocess.run([sys.executable, str(script), "--db", str(self.origin), "--out", str(out)], check=True,
                       capture_output=True)
        removed = json.loads(out.read_text())
        self.assertEqual((["TATTACH1", "TTTTTTTT"], ["TATTACH1"]), (removed["items"], removed["attachments"]))
        state = merge_replica.load(str(self.origin))
        self.assertEqual({"AAAAAAAA"}, set(state["items"]))
        con = sqlite3.connect(self.origin)
        self.assertEqual([("t-shared",)], con.execute("SELECT name FROM tags").fetchall())
