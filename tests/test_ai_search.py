import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "companion"))

import ai.search as search_mod  # noqa: E402


NEEDLE = "ZZUNIQUE_NEEDLE_PHASE4_TEST_9f3a"


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = pathlib.Path(self.temp.name)

    def _write_antigravity(self, text: str) -> str:
        cid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        base = self.home / ".gemini" / "antigravity"
        logs = base / "brain" / cid / ".system_generated" / "logs"
        logs.mkdir(parents=True)
        (logs / "transcript_full.jsonl").write_text(
            json.dumps({"type": "USER_INPUT", "content": text}) + "\n"
        )
        return cid

    def _write_claude(self, text: str) -> str:
        sid = "claude-sess-1"
        path = self.home / ".claude" / "projects" / "demo" / f"{sid}.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "type": "user",
                    "sessionId": sid,
                    "message": {"content": text},
                }
            )
            + "\n"
        )
        return sid

    def test_finds_needle_in_antigravity_and_claude(self):
        ag_id = self._write_antigravity(f"please fix {NEEDLE} tomorrow")
        cl_id = self._write_claude(f"remember {NEEDLE} forever")
        # distractor without needle
        other = self.home / ".claude" / "projects" / "demo" / "other.jsonl"
        other.write_text(
            json.dumps({"type": "user", "message": {"content": "hello"}}) + "\n"
        )

        result = search_mod.search_transcripts(
            NEEDLE,
            sources=["antigravity.app", "claude.code"],
            limit=20,
            home=self.home,
            prefer_rg=False,
        )
        self.assertGreaterEqual(result["count"], 2)
        sessions = {hit["session_id"] for hit in result["hits"]}
        self.assertIn(ag_id, sessions)
        self.assertIn(cl_id, sessions)
        for hit in result["hits"]:
            self.assertIn(NEEDLE, hit["snippet"])
            self.assertLessEqual(len(hit["snippet"]), 220)

    def test_limit_and_empty_query(self):
        self._write_claude(f"aaa {NEEDLE} bbb {NEEDLE} ccc")
        result = search_mod.search_transcripts(
            NEEDLE,
            sources=["claude.code"],
            limit=1,
            home=self.home,
            prefer_rg=False,
        )
        self.assertEqual(result["count"], 1)
        self.assertTrue(result["truncated"])
        with self.assertRaises(ValueError):
            search_mod.search_transcripts("  ", home=self.home)

    def test_codex_and_cursor_agent_transcript(self):
        codex = (
            self.home / ".codex" / "sessions" / "2026" / "01" / "01" / "rollout-x.jsonl"
        )
        codex.parent.mkdir(parents=True)
        codex.write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {"id": "codex-42", "cwd": "/tmp"},
                }
            )
            + "\n"
            + json.dumps(
                {
                    "type": "response_item",
                    "payload": {"role": "user", "content": [{"text": f"ask {NEEDLE}"}]},
                }
            )
            + "\n"
        )
        agent = (
            self.home
            / ".cursor"
            / "projects"
            / "demo"
            / "agent-transcripts"
            / "t1"
            / "t1.jsonl"
        )
        agent.parent.mkdir(parents=True)
        agent.write_text(json.dumps({"role": "user", "content": f"hi {NEEDLE}"}) + "\n")

        result = search_mod.search_transcripts(
            NEEDLE,
            sources=["codex.home", "cursor.agent-transcripts"],
            home=self.home,
            prefer_rg=False,
        )
        sources = {hit["source_id"] for hit in result["hits"]}
        self.assertIn("codex.home", sources)
        self.assertIn("cursor.agent-transcripts", sources)

    def test_cursor_cli_store_db(self):
        db_path = self.home / ".cursor" / "chats" / "hash" / "chat1" / "store.db"
        db_path.parent.mkdir(parents=True)
        con = sqlite3.connect(db_path)
        con.execute("CREATE TABLE blobs (id INTEGER PRIMARY KEY, data BLOB)")
        payload = json.dumps(
            {"role": "user", "content": f"needle lives here {NEEDLE}"}
        ).encode()
        con.execute("INSERT INTO blobs (data) VALUES (?)", (payload,))
        con.commit()
        con.close()
        result = search_mod.search_transcripts(
            NEEDLE,
            sources=["cursor.cli"],
            home=self.home,
            prefer_rg=False,
        )
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["hits"][0]["session_id"], "chat1")


if __name__ == "__main__":
    unittest.main()
