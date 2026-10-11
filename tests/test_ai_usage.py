import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "companion"))

from ai.adapters import antigravity, claude_code, codex  # noqa: E402
from ai import usage as usage_mod  # noqa: E402


def _varint(n: int) -> bytes:
    out = bytearray()
    while n > 0x7F:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n & 0x7F)
    return bytes(out)


def _key(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def _f_varint(field: int, value: int) -> bytes:
    return _key(field, 0) + _varint(value)


def _f_bytes(field: int, data: bytes) -> bytes:
    return _key(field, 2) + _varint(len(data)) + data


class AntigravityUsageTests(unittest.TestCase):
    def test_parse_usage_protobuf_f2_f5_f3_f9_f10(self):
        usage_msg = (
            _f_varint(1, 1319)
            + _f_varint(2, 1000)
            + _f_varint(3, 50)
            + _f_varint(5, 2000)
            + _f_varint(9, 30)
            + _f_varint(10, 20)
        )
        ts = _f_varint(1, 1_725_000_000) + _f_varint(2, 0)
        meta = _f_bytes(1, ts) + _f_bytes(9, usage_msg) + _f_varint(11, 1319)
        parsed = antigravity.parse_usage_metadata(meta)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["model_id"], 1319)
        self.assertEqual(parsed["model"], "gemini-3-flash")
        self.assertEqual(parsed["input_tokens"], 1000)
        self.assertEqual(parsed["cached_tokens"], 2000)
        self.assertEqual(parsed["output_tokens"], 50)
        self.assertEqual(parsed["thinking_tokens"], 30)
        self.assertEqual(parsed["visible_tokens"], 20)
        self.assertEqual(
            parsed["thinking_tokens"] + parsed["visible_tokens"],
            parsed["output_tokens"],
        )
        self.assertEqual(parsed["date"], "2024-08-30")

    def test_iter_usage_from_conversation_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            cid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
            conv = root / "conversations"
            conv.mkdir(parents=True)
            usage_msg = (
                _f_varint(1, 1318)
                + _f_varint(2, 10)
                + _f_varint(3, 5)
                + _f_varint(9, 2)
                + _f_varint(10, 3)
            )
            meta = _f_bytes(9, usage_msg) + _f_varint(11, 1318)
            db = conv / f"{cid}.db"
            con = sqlite3.connect(db)
            con.execute(
                "CREATE TABLE steps (idx INTEGER PRIMARY KEY, step_type INT, metadata BLOB)"
            )
            con.execute(
                "INSERT INTO steps (idx, step_type, metadata) VALUES (1, 15, ?)",
                (meta,),
            )
            con.commit()
            con.close()
            events = list(
                antigravity.iter_usage(root, "antigravity.app", "app")
            )
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["session_id"], cid)
            self.assertEqual(events[0]["model"], "gemini-3-pro-high")
            self.assertEqual(events[0]["input_tokens"], 10)


class ClaudeCodexUsageTests(unittest.TestCase):
    def test_claude_message_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            path = root / "projects" / "demo" / "s1.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "assistant",
                                "sessionId": "s1",
                                "timestamp": "2026-03-01T12:00:00.000Z",
                                "message": {
                                    "model": "claude-sonnet-4-6",
                                    "usage": {
                                        "input_tokens": 10,
                                        "cache_creation_input_tokens": 100,
                                        "cache_read_input_tokens": 50,
                                        "output_tokens": 20,
                                    },
                                },
                            }
                        ),
                    ]
                )
                + "\n"
            )
            events = list(claude_code.iter_usage(root, "claude.code"))
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["input_tokens"], 10)
            self.assertEqual(events[0]["cache_write_tokens"], 100)
            self.assertEqual(events[0]["cached_tokens"], 50)
            self.assertEqual(events[0]["output_tokens"], 20)
            self.assertEqual(events[0]["model"], "claude-sonnet-4-6")
            self.assertEqual(events[0]["date"], "2026-03-01")

    def test_codex_last_token_usage_not_cumulative_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            path = (
                root
                / "sessions"
                / "2026"
                / "03"
                / "01"
                / "rollout-2026-03-01T12-00-00-uuid.jsonl"
            )
            path.parent.mkdir(parents=True)
            lines = [
                {
                    "type": "session_meta",
                    "timestamp": "2026-03-01T12:00:00Z",
                    "payload": {"id": "codex-1", "cwd": "/Users/x/Projects/demo"},
                },
                {
                    "type": "turn_context",
                    "timestamp": "2026-03-01T12:00:01Z",
                    "payload": {"model": "gpt-5.5", "turn_id": "t1"},
                },
                {
                    "type": "event_msg",
                    "timestamp": "2026-03-01T12:00:02Z",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 1000,
                                "cached_input_tokens": 400,
                                "output_tokens": 50,
                            },
                            "last_token_usage": {
                                "input_tokens": 1000,
                                "cached_input_tokens": 400,
                                "output_tokens": 50,
                            },
                        },
                    },
                },
                {
                    "type": "event_msg",
                    "timestamp": "2026-03-01T12:01:00Z",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 2500,
                                "cached_input_tokens": 1200,
                                "output_tokens": 80,
                            },
                            "last_token_usage": {
                                "input_tokens": 1500,
                                "cached_input_tokens": 800,
                                "output_tokens": 30,
                            },
                        },
                    },
                },
            ]
            path.write_text("\n".join(json.dumps(row) for row in lines) + "\n")
            events = list(codex.iter_usage(root, "codex.home"))
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0]["input_tokens"], 600)  # 1000-400
            self.assertEqual(events[0]["cached_tokens"], 400)
            self.assertEqual(events[1]["input_tokens"], 700)  # 1500-800
            self.assertEqual(events[1]["cached_tokens"], 800)
            self.assertEqual(events[1]["output_tokens"], 30)
            self.assertEqual(events[0]["model"], "gpt-5.5")
            total_in = sum(e["input_tokens"] for e in events)
            self.assertEqual(total_in, 1300)


class AggregateUsageTests(unittest.TestCase):
    def test_aggregate_by_model_and_surface(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            root = home / ".gemini" / "antigravity"
            cid = "11111111-1111-1111-1111-111111111111"
            conv = root / "conversations"
            conv.mkdir(parents=True)
            usage_msg = (
                _f_varint(1, 1319)
                + _f_varint(2, 1_000_000)
                + _f_varint(3, 0)
                + _f_varint(5, 0)
            )
            meta = _f_bytes(9, usage_msg) + _f_varint(11, 1319)
            db = conv / f"{cid}.db"
            con = sqlite3.connect(db)
            con.execute(
                "CREATE TABLE steps (idx INTEGER PRIMARY KEY, step_type INT, metadata BLOB)"
            )
            con.execute(
                "INSERT INTO steps (idx, step_type, metadata) VALUES (1, 15, ?)",
                (meta,),
            )
            con.commit()
            con.close()
            prices_path = home / "prices.json"
            result = usage_mod.aggregate_usage(
                sources=["antigravity.app"],
                home=home,
                prices_path=prices_path,
            )
            self.assertIn("by_model", result)
            self.assertIn("by_surface", result)
            self.assertIn("by_date", result)
            self.assertAlmostEqual(result["totals"]["cost_usd"], 0.75, places=4)
            self.assertEqual(result["totals"]["input_tokens"], 1_000_000)
            models = {row["model"]: row for row in result["by_model"]}
            self.assertIn("gemini-3-flash", models)

    def test_period_rollups_include_by_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            root = home / ".claude"
            path = (
                root
                / "projects"
                / "-Users-demo-Projects-demoApp"
                / "s1.jsonl"
            )
            path.parent.mkdir(parents=True)
            lines = []
            for day, model, tokens in (
                ("2026-03-01", "claude-sonnet-4-6", 1_000_000),
                ("2026-03-02", "claude-sonnet-4-6", 500_000),
                ("2026-04-01", "gpt-5.5", 1_000_000),
            ):
                lines.append(
                    json.dumps(
                        {
                            "type": "assistant",
                            "sessionId": "s1",
                            "cwd": "/Users/demo/Projects/demoApp",
                            "timestamp": f"{day}T12:00:00.000Z",
                            "message": {
                                "model": model,
                                "usage": {
                                    "input_tokens": tokens,
                                    "output_tokens": 0,
                                    "cache_read_input_tokens": 0,
                                    "cache_creation_input_tokens": 0,
                                },
                            },
                        }
                    )
                )
            path.write_text("\n".join(lines) + "\n")
            result = usage_mod.aggregate_usage(
                sources=["claude.code"],
                home=home,
                prices_path=home / "prices.json",
            )
            self.assertEqual(len(result["by_day"]), 3)
            self.assertEqual(len(result["by_month"]), 2)
            self.assertEqual(len(result["by_year"]), 1)
            weeks = {row["period"] for row in result["by_week"]}
            self.assertTrue(any(p.startswith("2026-W") for p in weeks))
            march = next(r for r in result["by_month"] if r["period"] == "2026-03")
            self.assertEqual(march["input_tokens"], 1_500_000)
            self.assertEqual(march["by_model"][0]["model"], "claude-sonnet-4-6")
            self.assertEqual(march["by_project"][0]["project"], "demoApp")
            self.assertEqual(result["by_year"][0]["period"], "2026")
            projects = {row["project"]: row for row in result["by_project"]}
            self.assertIn("demoApp", projects)
            self.assertEqual(projects["demoApp"]["input_tokens"], 2_500_000)


if __name__ == "__main__":
    unittest.main()
