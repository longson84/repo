import importlib.util
import json
import os
import pathlib
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_server():
    spec = importlib.util.spec_from_file_location(
        "repo_companion_ai_api", ROOT / "companion" / "server.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AiApiTests(unittest.TestCase):
    def setUp(self):
        self.module = load_server()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        (self.root / "proj").mkdir()
        self.home_patcher = patch.object(pathlib.Path, "home", return_value=self.root)
        self.home_patcher.start()
        self.addCleanup(self.home_patcher.stop)
        self.env_patcher = patch.dict(
            os.environ,
            {
                "CLAUDE_CONFIG_DIR": str(self.root / ".claude"),
                "CODEX_HOME": str(self.root / ".codex"),
            },
        )
        self.env_patcher.start()
        self.addCleanup(self.env_patcher.stop)
        self.config = self.root / "settings.json"
        self.config.write_text(json.dumps({"paths": []}) + "\n")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.module.Handler)
        self.server.state = self.module.State([self.root], self.config)
        self.server.token = "test-token"
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)

    def request(self, method, path, body=None, token="test-token"):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=30)
        payload = None if body is None else json.dumps(body).encode()
        headers = {
            "Host": f"127.0.0.1:{self.port}",
            "Origin": "http://127.0.0.1:8766",
            "Authorization": f"Bearer {token}",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(payload))
        conn.request(method, path, body=payload, headers=headers)
        response = conn.getresponse()
        data = json.loads(response.read().decode())
        conn.close()
        return response.status, data

    def test_get_detect_empty_then_post(self):
        status, data = self.request("GET", "/api/ai/detect")
        self.assertEqual(status, 200)
        self.assertEqual(data["sources"], [])
        status, data = self.request("POST", "/api/ai/detect", {})
        self.assertEqual(status, 200)
        self.assertGreaterEqual(data["summary"]["total"], 10)
        self.assertTrue(any(s["id"] == "claude.code" for s in data["sources"]))
        status, cached = self.request("GET", "/api/ai/detect")
        self.assertEqual(status, 200)
        self.assertEqual(cached["detectedAt"], data["detectedAt"])

    def test_detect_rejects_bad_token(self):
        status, data = self.request("GET", "/api/ai/detect", token="wrong")
        self.assertEqual(status, 403)
        self.assertIn("error", data)

    def test_project_identity_config_api_round_trip_and_validation(self):
        status, empty = self.request("GET", "/api/project-identities")
        self.assertEqual(status, 200)
        self.assertEqual(empty["version"], 1)
        self.assertEqual(empty["projects"], [])
        self.assertEqual(empty["warnings"], [])

        payload = {
            "version": 1,
            "projects": [
                {
                    "id": "companyq",
                    "name": "CompanyQ",
                    "workspace_aliases": [str(self.root / "OldCompanyQ")],
                }
            ],
        }
        status, saved = self.request("POST", "/api/project-identities", payload)
        self.assertEqual(status, 200)
        self.assertEqual(saved["projects"][0]["id"], "companyq")
        self.assertEqual(saved["warnings"], [])
        self.assertEqual(self.config.read_text(), json.dumps({"paths": []}) + "\n")

        status, loaded = self.request("GET", "/api/project-identities")
        self.assertEqual(status, 200)
        self.assertEqual(loaded["projects"], saved["projects"])

        invalid = {
            "version": 1,
            "projects": [
                {"id": "one", "name": "One", "workspace_aliases": ["/tmp/x"]},
                {"id": "two", "name": "Two", "workspace_aliases": ["/tmp/x"]},
            ],
        }
        status, error = self.request("POST", "/api/project-identities", invalid)
        self.assertEqual(status, 400)
        self.assertIn("alias", error["error"])

    def test_status_keeps_paths_and_adds_canonical_workspaces(self):
        status, payload = self.request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(payload["paths"], [])
        self.assertEqual(payload["workspaces"], [])

    def test_sessions_lists_wave1(self):
        status, data = self.request(
            "POST",
            "/api/ai/sessions",
            {"sources": ["claude.code"], "limit": 10},
        )
        self.assertEqual(status, 200)
        self.assertIn("sessions", data)
        self.assertEqual(data["sources"], ["claude.code"])
        self.assertIsInstance(data["count"], int)

    def test_usage_exposes_tokens_without_pricing(self):
        status, prices = self.request("GET", "/api/ai/prices")
        self.assertEqual(status, 404)
        self.assertEqual(prices["error"], "Unknown endpoint.")
        status, usage = self.request(
            "POST",
            "/api/ai/usage",
            {"sources": ["antigravity.app", "claude.code", "codex.home"]},
        )
        self.assertEqual(status, 200)
        self.assertIn("totals", usage)
        self.assertIn("by_model", usage)
        self.assertIn("caveats", usage)
        self.assertIn(usage["cache"]["status"], {"rebuilt", "ready"})
        self.assertIn("hits", usage["cache"])
        self.assertNotIn("cost_usd", usage["totals"])
        self.assertNotIn("pricing_coverage", usage)
        self.assertNotIn("prices", usage)

    def test_api_exposes_quality_reconciliation_and_cache_metadata(self):
        status, usage = self.request(
            "POST", "/api/ai/usage", {"sources": ["claude.code"]}
        )
        self.assertEqual(status, 200)
        self.assertIn("warning_count", usage["quality"])
        self.assertIn("reconciliation", usage)
        self.assertIn("status", usage["cache"])

    def test_usage_api_accepts_conversation_keys_and_compound_sessions(self):
        claude_root = self.root / "claude-home"
        transcript = claude_root / "projects" / "demo" / "session-1.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "sessionId": "session-1",
                    "timestamp": "2026-03-01T12:00:00Z",
                    "message": {
                        "id": "msg-1",
                        "model": "claude-sonnet-4-6",
                        "usage": {"input_tokens": 3, "output_tokens": 5},
                    },
                }
            )
            + "\n"
        )
        other = claude_root / "projects" / "demo" / "session-2.jsonl"
        other.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "sessionId": "session-2",
                    "timestamp": "2026-03-01T12:01:00Z",
                    "message": {
                        "id": "msg-2",
                        "model": "claude-sonnet-4-6",
                        "usage": {"input_tokens": 4, "output_tokens": 7},
                    },
                }
            )
            + "\n"
        )
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(claude_root)}):
            status, canonical = self.request(
                "POST",
                "/api/ai/usage",
                {
                    "sources": ["claude.code"],
                    "conversation_keys": ["claude-code:conversation:session-1"],
                },
            )
            status_compound, compound = self.request(
                "POST",
                "/api/ai/usage",
                {
                    "sources": ["claude.code"],
                    "sessions": [
                        {"source_id": "claude.code", "session_id": "session-1"}
                    ],
                },
            )

        self.assertEqual(status, 200)
        self.assertEqual(status_compound, 200)
        self.assertEqual(canonical["totals"]["output_tokens"], 5)
        self.assertEqual(compound["totals"], canonical["totals"])

    def test_search_requires_query(self):
        status, data = self.request("POST", "/api/ai/search", {"q": "  "})
        self.assertEqual(status, 400)
        self.assertIn("error", data)

    def test_search_ok(self):
        status, data = self.request(
            "POST",
            "/api/ai/search",
            {"q": "theana-unlikely-needle-xyz", "sources": ["claude.code"], "limit": 5},
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["q"], "theana-unlikely-needle-xyz")
        self.assertIn("hits", data)
        self.assertIsInstance(data["count"], int)


if __name__ == "__main__":
    unittest.main()
