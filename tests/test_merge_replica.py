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
