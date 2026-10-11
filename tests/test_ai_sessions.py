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

    def test_claude_subagents_are_executions_not_conversations(self):
        project = self.home / ".claude" / "projects" / "demo"
        session_id = "session-parent"
        root = project / f"{session_id}.jsonl"
        root.parent.mkdir(parents=True)
        root.write_text(
            json.dumps(
                {
                    "type": "user",
                    "cwd": "/Users/demo/Projects/foo",
                    "sessionId": session_id,
                    "timestamp": "2026-09-01T10:00:00.000Z",
                    "message": {"content": "coordinate the agents"},
                }
            )
            + "\n"
        )
        subagents = project / session_id / "subagents"
        subagents.mkdir(parents=True)
        for child in ("agent-one", "agent-two"):
            (subagents / f"{child}.jsonl").write_text(
                json.dumps(
                    {
                        "type": "assistant",
                        "sessionId": session_id,
                        "timestamp": "2026-09-01T10:01:00.000Z",
                        "message": {"id": f"msg-{child}", "content": "done"},
                    }
                )
                + "\n"
            )

        rows = claude_code.list_sessions(self.home / ".claude")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], session_id)
        self.assertEqual(
            rows[0]["conversation_key"], f"claude-code:conversation:{session_id}"
        )
        self.assertEqual(rows[0]["execution_count"], 3)
        self.assertEqual(
            {item["kind"] for item in rows[0]["executions"]}, {"root", "child"}
        )

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

    def test_codex_surfaces_come_from_desktop_tui_vscode_and_exec_metadata(self):
        root = self.home / ".codex" / "sessions" / "2026" / "09" / "01"
        root.mkdir(parents=True)
        fixtures = (
            ("desktop", "Codex Desktop", "vscode", "desktop"),
            ("terminal", "codex-tui", "cli", "terminal"),
            ("vscode", "other-client", "vscode", "vscode"),
            ("exec", "codex_exec", "exec", "exec"),
        )
        for session_id, originator, source, _expected in fixtures:
            (root / f"rollout-{session_id}.jsonl").write_text(
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": session_id,
                            "originator": originator,
                            "source": source,
                        },
                    }
                )
                + "\n"
            )

        rows = codex.list_sessions(self.home / ".codex")

        surfaces = {row["id"]: row["surface"] for row in rows}
        self.assertEqual(
            surfaces,
            {session_id: expected for session_id, _o, _s, expected in fixtures},
        )

    def test_codex_child_discovered_before_parent_inherits_parent_surface(self):
        root = self.home / ".codex" / "sessions" / "2026" / "09" / "01"
        root.mkdir(parents=True)
        child = {
            "type": "session_meta",
            "payload": {
                "id": "a-child",
                "parent_thread_id": "z-parent",
                "originator": "Codex Desktop",
                "source": {"subagent": {"other": "worker"}},
            },
        }
        parent = {
            "type": "session_meta",
            "payload": {
                "id": "z-parent",
                "originator": "Codex Desktop",
                "source": "vscode",
            },
        }
        (root / "rollout-a-child.jsonl").write_text(json.dumps(child) + "\n")
        (root / "rollout-z-parent.jsonl").write_text(json.dumps(parent) + "\n")

        rows = codex.list_sessions(self.home / ".codex")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "z-parent")
        self.assertEqual(rows[0]["surface"], "desktop")
        self.assertEqual(rows[0]["execution_count"], 2)
        self.assertEqual(
            {item["id"] for item in rows[0]["executions"]},
            {"a-child", "z-parent"},
        )

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

    def test_cursor_cli_and_transcript_uuid_merge_into_one_conversation(self):
        chat_id = "cursor-shared"
        chats = self.home / ".cursor" / "chats" / "hash1" / chat_id
        chats.mkdir(parents=True)
        db = chats / "store.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE meta (key TEXT, value TEXT)")
        con.execute("CREATE TABLE blobs (id TEXT, data BLOB)")
        meta = {"agentId": chat_id, "name": "Shared", "createdAt": 1700000000000}
        con.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            ("0", json.dumps(meta).encode().hex()),
        )
        con.commit()
        con.close()
        transcript = (
            self.home
            / ".cursor"
            / "projects"
            / "demo"
            / "agent-transcripts"
            / chat_id
            / f"{chat_id}.jsonl"
        )
        transcript.parent.mkdir(parents=True)
        transcript.write_text(json.dumps({"role": "user", "content": "hello"}) + "\n")

        payload = sessions_mod.list_sessions(
            sources=["cursor.cli", "cursor.agent-transcripts"],
            limit=10,
            home=self.home,
            env={},
        )

        self.assertEqual(payload["conversation_count"], 1)
        self.assertEqual(payload["representation_count"], 2)
        self.assertEqual(payload["sessions"][0]["surface"], "cli")
        self.assertEqual(
            payload["sessions"][0]["source_ids"],
            ["cursor.agent-transcripts", "cursor.cli"],
        )

    def test_antigravity_same_provider_uuid_merges_sources_without_duplicate_session(
        self,
    ):
        conversation_id = "antigravity-shared"
        for relative in ("antigravity", "antigravity-cli"):
            conversations = self.home / ".gemini" / relative / "conversations"
            conversations.mkdir(parents=True)
            (conversations / f"{conversation_id}.db").write_bytes(b"not-a-db")

        payload = sessions_mod.list_sessions(
            sources=["antigravity.app", "antigravity.cli"],
            limit=10,
            home=self.home,
            env={},
        )

        self.assertEqual(payload["conversation_count"], 1)
        self.assertEqual(payload["representation_count"], 2)
        self.assertEqual(
            payload["sessions"][0]["source_ids"],
            ["antigravity.app", "antigravity.cli"],
        )

    def test_same_bare_id_across_tools_requires_compound_selector(self):
        shared_id = "shared-id"
        claude = self.home / ".claude" / "projects" / "demo" / f"{shared_id}.jsonl"
        claude.parent.mkdir(parents=True)
        claude.write_text(
            json.dumps(
                {"type": "user", "sessionId": shared_id, "message": {"content": "x"}}
            )
            + "\n"
        )
        codex_path = (
            self.home
            / ".codex"
            / "sessions"
            / "2026"
            / "01"
            / "01"
            / f"rollout-{shared_id}.jsonl"
        )
        codex_path.parent.mkdir(parents=True)
        codex_path.write_text(
            json.dumps({"type": "session_meta", "payload": {"id": shared_id}}) + "\n"
        )
        rows = sessions_mod.collect_sessions(home=self.home, env={})

        with self.assertRaisesRegex(
            ValueError, "Ambiguous session id.*claude-code.*codex"
        ):
            sessions_mod.resolve_conversation_selectors(
                rows,
                conversation_keys=None,
                compound_sessions=None,
                legacy_session_ids=[shared_id],
            )

    def test_session_response_reports_conversation_execution_and_representation_counts(
        self,
    ):
        project = self.home / ".claude" / "projects" / "demo"
        parent = project / "session-1.jsonl"
        child = project / "session-1" / "subagents" / "agent-1.jsonl"
        child.parent.mkdir(parents=True)
        parent.write_text(
            json.dumps(
                {"type": "user", "sessionId": "session-1", "message": {"content": "x"}}
            )
            + "\n"
        )
        child.write_text(
            json.dumps({"type": "assistant", "sessionId": "session-1", "message": {}})
            + "\n"
        )

        payload = sessions_mod.list_sessions(
            sources=["claude.code"], limit=10, home=self.home, env={}
        )

        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["conversation_count"], 1)
        self.assertEqual(payload["execution_count"], 2)
        self.assertEqual(payload["representation_count"], 2)
        self.assertNotIn("executions", payload["sessions"][0])
        detailed = sessions_mod.list_sessions(
            sources=["claude.code"],
            limit=10,
            home=self.home,
            env={},
            include_executions=True,
        )
        self.assertEqual(len(detailed["sessions"][0]["executions"]), 2)

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
                        "timestamp": f"2026-09-0{i + 1}T10:00:00.000Z",
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
