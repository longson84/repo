import importlib.util
import json
import pathlib
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
        self.assertEqual(payload["version"], 1)
        self.assertIn("detectedAt", payload)
        found = [s for s in payload["sources"] if s["id"] == "claude.code"][0]
        self.assertTrue(found["exists"])
        self.assertEqual(found["session_estimate"], 1)
        cache_path = self.home / "ai-detect.json"
        detect.save_detect_cache(cache_path, payload)
        loaded = detect.load_detect_cache(cache_path)
        self.assertEqual(loaded["summary"]["found"], payload["summary"]["found"])


if __name__ == "__main__":
    unittest.main()
