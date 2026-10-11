import importlib.util
import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPANION = ROOT / "companion"
sys.path.insert(0, str(COMPANION))

from ai import sessions as sessions_mod  # noqa: E402
from ai.adapters import antigravity, claude_code, codex, cursor  # noqa: E402


class SessionAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = pathlib.Path(self.temp.name)

    def test_antigravity_lists_from_summaries_and_transcript(self):
        base = self.home / ".gemini" / "antigravity"
        cid = "11111111-1111-1111-1111-111111111111"
        (base / "conversations").mkdir(parents=True)
        (base / "conversations" / f"{cid}.db").write_bytes(b"x")
        logs = base / "brain" / cid / ".system_generated" / "logs"
        logs.mkdir(parents=True)
        (logs / "transcript_full.jsonl").write_text(
            json.dumps(
                {
                    "type": "USER_INPUT",
                    "content": "<USER_REQUEST>\nfix the login bug\n</USER_REQUEST>",
                }
            )
            + "\n"
        )
        db = base / "conversation_summaries.db"
        con = sqlite3.connect(db)
        con.execute(
            """
            CREATE TABLE conversation_summaries (
              conversation_id TEXT, title TEXT, preview TEXT, step_count INT,
              last_modified_time TEXT, workspace_uris TEXT, status TEXT, source TEXT,
              project_id TEXT, agent_name TEXT, parent_conversation_id TEXT,
              nesting_depth INT, battle_id TEXT, winning_conversation_id TEXT,
              not_fully_idle INT, killed INT, last_user_input_time TEXT,
              last_user_input_step_index INT, app_data_dir TEXT, raw_summary BLOB,
              group_id TEXT
            )
            """
        )
        con.execute(
            """
            INSERT INTO conversation_summaries (
              conversation_id, title, preview, step_count, last_modified_time,
              workspace_uris, status, source, project_id, agent_name,
              parent_conversation_id, nesting_depth, battle_id, winning_conversation_id,
              not_fully_idle, killed, last_user_input_time, last_user_input_step_index,
              app_data_dir, raw_summary, group_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                cid,
                "Login bug",
                "Login bug",
                3,
                "2026-09-12 06:47:55.261614+00:00",
                '["file:///Users/sonnguyen/Projects/demoApp"]',
                "",
                "",
                "",
                "",
                "",
                0,
                "",
                "",
                0,
                0,
                "2026-09-12 06:46:58.942285+00:00",
                1,
                "antigravity",
                None,
                "",
            ),
        )
        con.commit()
        con.close()
        rows = antigravity.list_sessions(base, "antigravity.app", "app")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["project"], "demoApp")
        self.assertIn("login", (rows[0]["preview"] or "").lower())

    def test_claude_code_lists_jsonl(self):
        root = self.home / ".claude"
        path = root / "projects" / "-Users-demo-Projects-foo" / "sess.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "type": "user",
                    "cwd": "/Users/demo/Projects/foo",
                    "sessionId": "abc-123",
                    "timestamp": "2026-09-01T10:00:00.000Z",
                    "message": {"content": "please review the PR"},
                }
            )
            + "\n"
        )
        rows = claude_code.list_sessions(root)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "abc-123")
        self.assertEqual(rows[0]["project"], "foo")
        self.assertIn("review", rows[0]["preview"])

    def test_codex_lists_rollout(self):
        root = self.home / ".codex"
        path = (
            root
            / "sessions"
            / "2026"
            / "09"
            / "01"
            / "rollout-2026-09-01T12-00-00-abcd.jsonl"
        )
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "timestamp": "2026-09-01T12:00:00.000Z",
                    "type": "session_meta",
                    "payload": {
                        "id": "abcd",
                        "cwd": "/Users/demo/Projects/bar",
                    },
                }
            )
            + "\n"
            + json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "role": "user",
                        "content": [{"type": "input_text", "text": "ship the feature"}],
                    },
                }
            )
            + "\n"
        )
        rows = codex.list_sessions(root)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "abcd")
        self.assertEqual(rows[0]["project"], "bar")

    def test_cursor_cli_lists_store_db(self):
        chats = self.home / ".cursor" / "chats" / "hash1" / "agent-1"
        chats.mkdir(parents=True)
        db = chats / "store.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE meta (key TEXT, value TEXT)")
        con.execute("CREATE TABLE blobs (id TEXT, data BLOB)")
        meta = {
            "agentId": "agent-1",
            "name": "My chat",
            "createdAt": 1700000000000,
        }
        con.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            ("0", meta.__class__.__name__ and json.dumps(meta).encode().hex()),
        )
        # fix: store hex of json
        con.execute("DELETE FROM meta")
        con.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            ("0", json.dumps(meta).encode().hex()),
        )
        user = {
            "role": "user",
            "content": "Workspace Path: /Users/demo/Projects/zoo\n\nhello world",
        }
        con.execute(
            "INSERT INTO blobs(id, data) VALUES (?, ?)",
            ("1", json.dumps(user).encode()),
        )
        con.commit()
        con.close()
        rows = cursor.list_cli_sessions(self.home / ".cursor" / "chats")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "agent-1")
        self.assertEqual(rows[0]["project"], "zoo")

    def test_list_sessions_filters_and_limits(self):
        # minimal claude fixture
        root = self.home / ".claude" / "projects" / "p"
        root.mkdir(parents=True)
        for i, name in enumerate(("alpha", "beta", "gamma")):
            (root / f"{name}.jsonl").write_text(
                json.dumps(
                    {
                        "type": "user",
                        "cwd": f"/Users/demo/Projects/{name}",
                        "sessionId": f"id-{name}",
                        "timestamp": f"2026-09-0{i+1}T10:00:00.000Z",
                        "message": {"content": f"work on {name}"},
                    }
                )
                + "\n"
            )
        payload = sessions_mod.list_sessions(
            sources=["claude.code"],
            project="beta",
            limit=10,
            home=self.home,
            env={},
        )
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["sessions"][0]["project"], "beta")


if __name__ == "__main__":
    unittest.main()
