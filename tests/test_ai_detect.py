import importlib.util
import json
import pathlib
import sqlite3
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
AI = ROOT / "companion" / "ai"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


catalog = load("ai_catalog", AI / "catalog.py")
detect = load("ai_detect", AI / "detect.py")


class DetectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = pathlib.Path(self.temp.name)

    def test_missing_path(self):
        source = {
            "id": "antigravity.app",
            "tool": "antigravity",
            "surface": "app",
            "path": str(self.home / "missing"),
            "notes": "test",
        }
        result = detect.detect_one(source)
        self.assertFalse(result["exists"])
        self.assertEqual(result["size_bytes"], 0)
        self.assertEqual(result["kind"], "missing")

    def test_antigravity_fixture_counts_sessions(self):
        base = self.home / ".gemini" / "antigravity"
        brain = base / "brain" / "abc-123"
        brain.mkdir(parents=True)
        (base / "brain" / "tempmediaStorage").mkdir()
        conv = base / "conversations"
        conv.mkdir()
        (conv / "abc-123.db").write_bytes(b"sqlite")
        logs = brain / ".system_generated" / "logs"
        logs.mkdir(parents=True)
        (logs / "transcript_full.jsonl").write_text(
            '{"type":"USER_INPUT","content":"hi"}\n'
        )
        source = {
            "id": "antigravity.app",
            "tool": "antigravity",
            "surface": "app",
            "path": str(base),
            "notes": "App",
        }
        result = detect.detect_one(source)
        self.assertTrue(result["exists"])
        self.assertEqual(result["session_estimate"], 1)
        self.assertGreater(result["size_bytes"], 0)
        self.assertIn("transcript_full=1", result["notes"])

    def test_detect_all_and_cache_roundtrip(self):
        base = self.home / ".claude" / "projects" / "-tmp"
        base.mkdir(parents=True)
        (base / "sess.jsonl").write_text("{}\n")
        sources = catalog.resolve_sources(env={}, home=self.home)
        payload = detect.detect_all(sources)
        self.assertEqual(payload["version"], 2)
        self.assertIn("detectedAt", payload)
        found = [s for s in payload["sources"] if s["id"] == "claude.code"][0]
        self.assertTrue(found["exists"])
        self.assertEqual(found["session_estimate"], 1)
        cache_path = self.home / "ai-detect.json"
        detect.save_detect_cache(cache_path, payload)
        loaded = detect.load_detect_cache(cache_path)
        self.assertEqual(loaded["summary"]["found"], payload["summary"]["found"])

    def test_detect_separates_stores_representations_conversations_and_executions(self):
        root = self.home / ".claude"
        project = root / "projects" / "demo"
        child = project / "session-1" / "subagents"
        child.mkdir(parents=True)
        (project / "session-1.jsonl").write_text("{}\n")
        (child / "agent-a.jsonl").write_text("{}\n")
        (child / "agent-b.jsonl").write_text("{}\n")
        source = {
            "id": "claude.code",
            "tool": "claude-code",
            "surface": "cli",
            "path": str(root),
            "notes": "",
        }

        payload = detect.detect_all([source])

        detail = payload["sources"][0]
        self.assertEqual(detail["representation_estimate"], 3)
        self.assertEqual(detail["conversation_estimate"], 1)
        self.assertEqual(detail["execution_estimate"], 3)
        self.assertEqual(payload["summary"]["stores_found"], 1)
        self.assertEqual(payload["summary"]["conversation_estimate"], 1)
        self.assertEqual(payload["summary"]["execution_estimate"], 3)

    def test_cursor_ide_database_presence_is_not_one_session(self):
        root = self.home / "Cursor"
        state = root / "User" / "globalStorage" / "state.vscdb"
        state.parent.mkdir(parents=True)
        state.write_bytes(b"sqlite")
        result = detect.detect_one(
            {
                "id": "cursor.ide",
                "tool": "cursor",
                "surface": "ide",
                "path": str(root),
                "notes": "",
            }
        )
        self.assertEqual(result["representation_estimate"], 1)
        self.assertIsNone(result["conversation_estimate"])
        self.assertIsNone(result["session_estimate"])

    def test_detect_does_not_sum_overlapping_cursor_representations(self):
        cli = self.home / "cli"
        db = cli / "workspace" / "shared-id" / "store.db"
        db.parent.mkdir(parents=True)
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE meta(value BLOB)")
        con.execute(
            "INSERT INTO meta VALUES (?)",
            (json.dumps({"agentId": "shared-id"}),),
        )
        con.commit()
        con.close()
        projects = self.home / "projects"
        transcript = (
            projects / "demo" / "agent-transcripts" / "shared-id" / "shared-id.jsonl"
        )
        transcript.parent.mkdir(parents=True)
        transcript.write_text("{}\n")
        payload = detect.detect_all(
            [
                {
                    "id": "cursor.cli",
                    "tool": "cursor",
                    "surface": "cli",
                    "path": str(cli),
                    "notes": "",
                },
                {
                    "id": "cursor.agent-transcripts",
                    "tool": "cursor",
                    "surface": "unknown",
                    "path": str(projects),
                    "notes": "",
                },
            ]
        )
        self.assertEqual(payload["summary"]["representation_estimate"], 2)
        self.assertEqual(payload["summary"]["conversation_estimate"], 1)
        self.assertEqual(payload["summary"]["execution_estimate"], 1)

    def test_old_detect_cache_is_ignored(self):
        path = self.home / "detect.json"
        path.write_text('{"version":1,"sources":[]}')
        self.assertIsNone(detect.load_detect_cache(path))


if __name__ == "__main__":
    unittest.main()
