import importlib.util
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ingest_sources.py"
spec = importlib.util.spec_from_file_location("ingest_sources", SCRIPT)
ingest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ingest)


class LocalFileImport(unittest.TestCase):
    def run_import(self, root, importer, note):
        args = [str(SCRIPT), str(root / "sources.json"), "--collection", "COLLECT1",
                "--pdfdir", str(root / "files"), "--notesdir", str(root / "notes"),
                "--state", str(root / "state.json"), "--serve-ip", "192.0.2.1"]
        client = Mock()
        client.call.return_value = {"result": {"content": [{"text": '{"key":"ABCD1234"}'}]}}
        with patch.object(sys, "argv", args), patch.object(ingest, "MCP", return_value=client), \
                patch.object(ingest, "import_local_files", importer), \
                patch.object(ingest, "add_note_file", note), \
                patch.object(ingest.acqlog, "record"), redirect_stdout(io.StringIO()):
            ingest.main()
        return client, json.loads((root / "state.json").read_text())["sample"]

    def fixture(self, root):
        (root / "source.bin").write_bytes(b"original data")
        (root / "full.txt").write_text("Complete supplied extract")
        (root / "sources.json").write_text(json.dumps([{
            "id": "sample", "kind": "localfile", "title": "Sample document",
            "file_path": str(root / "source.bin"), "text_path": str(root / "full.txt")
        }]))

    def test_failed_attachment_reuses_parent_on_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.fixture(root)
            client, first = self.run_import(root, Mock(return_value=[("ABCD1234", False, "offline")]), Mock())
            self.assertEqual(client.call.call_count, 1)
            self.assertIn("error", first)
            retry, second = self.run_import(root, Mock(return_value=[("ABCD1234", True, "imported")]), Mock())
            retry.call.assert_not_called()
            self.assertNotIn("error", second)
            self.assertEqual((root / "source.bin").read_bytes(), b"original data")

    def test_failed_note_retries_without_reimporting_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.fixture(root)
            self.run_import(root, Mock(return_value=[("ABCD1234", True, "imported")]),
                            Mock(side_effect=RuntimeError("interrupted")))
            importer = Mock(); client, state = self.run_import(root, importer, Mock())
            importer.assert_not_called(); client.call.assert_not_called()
            self.assertEqual(state["note"], "ok")

    def test_large_note_preserved_as_full_text_attachment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.fixture(root)
            importer = Mock(return_value=[("ABCD1234", True, "imported")])
            error = urllib.error.HTTPError("http://example.invalid", 413, "too large", {}, None)
            _, state = self.run_import(root, importer, Mock(side_effect=error))
            self.assertEqual(state["note"], "full-text attachment")
            self.assertEqual(importer.call_count, 2)
            self.assertEqual(Path(importer.call_args.args[1][0][0]).read_text(), "Complete supplied extract")

    def test_completed_retry_has_no_writes_to_zotero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.fixture(root)
            self.run_import(root, Mock(return_value=[("ABCD1234", True, "imported")]), Mock())
            importer, note = Mock(), Mock()
            client, _ = self.run_import(root, importer, note)
            client.call.assert_not_called(); importer.assert_not_called(); note.assert_not_called()

    def test_note_rejection_does_not_mark_import_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.fixture(root)
            rejected = {"result": {"content": [{"text": '{"success":false,"error":"rejected"}'}]}}
            _, state = self.run_import(root, Mock(return_value=[("ABCD1234", True, "imported")]),
                                       Mock(return_value=rejected))
            self.assertIn("note failed", state["error"])
            self.assertNotEqual(state.get("note"), "ok")
            importer = Mock()
            _, retry = self.run_import(root, importer, Mock())
            importer.assert_not_called()
            self.assertEqual(retry["note"], "ok")


if __name__ == "__main__":
    unittest.main()
