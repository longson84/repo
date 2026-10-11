import importlib.util
import json
import os
import pathlib
import subprocess
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

    def select_github_workspace(self, name="proj"):
        workspace = self.root / name
        workspace.mkdir(exist_ok=True)
        subprocess.run(
            ["git", "-C", str(workspace), "init", "-b", "main"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(workspace),
                "remote",
                "add",
                "origin",
                f"https://github.com/example/{name}.git",
            ],
            check=True,
        )
        self.server.state.select([str(workspace)])
        return workspace, self.server.state.workspace_inventory()[0]["repository_id"]

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
        workspace, repository_id = self.select_github_workspace()
        settings_before = self.config.read_bytes()
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
                    "repositories": [repository_id],
                    "workspace_aliases": [str(workspace)],
                }
            ],
        }
        status, saved = self.request("POST", "/api/project-identities", payload)
        self.assertEqual(status, 200)
        self.assertEqual(saved["projects"][0]["id"], "companyq")
        self.assertEqual(saved["warnings"], [])
        self.assertEqual(self.config.read_bytes(), settings_before)

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

    def test_project_identity_api_returns_fingerprint_and_rejects_stale_write(self):
        _, repository_id = self.select_github_workspace()
        status, initial = self.request("GET", "/api/project-identities")
        self.assertEqual(status, 200)
        self.assertRegex(initial["fingerprint"], r"^[0-9a-f]{64}$")

        status, newer = self.request(
            "POST",
            "/api/project-identities",
            {
                "version": 1,
                "projects": [
                    {
                        "id": "newer",
                        "name": "Newer",
                        "repositories": [repository_id],
                    }
                ],
                "expected_fingerprint": initial["fingerprint"],
            },
        )
        self.assertEqual(status, 200)
        self.assertNotEqual(newer["fingerprint"], initial["fingerprint"])

        status, conflict = self.request(
            "POST",
            "/api/project-identities",
            {
                "version": 1,
                "projects": [
                    {
                        "id": "stale",
                        "name": "Stale",
                        "repositories": [repository_id],
                    }
                ],
                "expected_fingerprint": initial["fingerprint"],
            },
        )
        self.assertEqual(status, 409)
        self.assertEqual(conflict["code"], "identity_config_conflict")

        status, loaded = self.request("GET", "/api/project-identities")
        self.assertEqual(status, 200)
        self.assertEqual(loaded["projects"], newer["projects"])

    def test_project_grouping_save_preserves_tracking_and_advanced_fields(self):
        workspace_path, repository_id = self.select_github_workspace()
        workspace = str(workspace_path.resolve())
        settings_before = self.config.read_bytes()
        existing = {
            "version": 1,
            "projects": [
                {
                    "id": "general",
                    "name": "General",
                    "repositories": [
                        repository_id,
                        "remote:github.com/example/hidden",
                    ],
                    "primary_workspaces": {
                        "remote:github.com/example/hidden": workspace
                    },
                    "workspace_aliases": [workspace],
                }
            ],
        }
        self.module.save_identity_config(self.server.state.identity_path, existing)
        status, current = self.request("GET", "/api/project-identities")
        self.assertEqual(status, 200)

        status, saved = self.request(
            "POST",
            "/api/project-identities",
            {
                **existing,
                "projects": [{**existing["projects"][0], "name": "General Tools"}],
                "expected_fingerprint": current["fingerprint"],
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(saved["projects"][0]["name"], "General Tools")
        self.assertEqual(
            saved["projects"][0]["repositories"],
            [repository_id],
        )
        self.assertEqual(saved["projects"][0]["primary_workspaces"], {})
        self.assertEqual(saved["projects"][0]["workspace_aliases"], [workspace])
        self.assertEqual(self.config.read_bytes(), settings_before)
        self.assertEqual(self.server.state.paths, [workspace])
        self.assertEqual(self.server.state.identity_path.stat().st_mode & 0o777, 0o600)

        status, state = self.request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(state["workspaces"][0]["project_id"], "general")

    def test_status_keeps_paths_and_adds_canonical_workspaces(self):
        status, payload = self.request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(payload["paths"], [])
        self.assertEqual(payload["workspaces"], [])
        self.assertIn("reconciliation", payload)

    def test_all_tracked_data_routes_reconcile_under_the_request_lock(self):
        routes = [
            ("GET", "/api/status", None),
            ("GET", "/api/project-identities", None),
            ("POST", "/api/scan", {}),
            ("GET", "/api/stats", None),
            ("GET", "/api/ai/detect", None),
            ("POST", "/api/ai/sessions", {}),
            ("POST", "/api/ai/usage", {}),
            ("POST", "/api/ai/search", {"q": "needle"}),
        ]
        original = self.server.state.reconcile_tracking
        with patch.object(
            self.server.state, "reconcile_tracking", wraps=original
        ) as reconcile:
            for method, path, body in routes:
                with self.subTest(path=path):
                    status, _ = self.request(method, path, body)
                    self.assertEqual(status, 200)
        self.assertEqual(reconcile.call_count, len(routes))

    def test_status_prunes_repository_that_lost_its_github_remote(self):
        workspace, _ = self.select_github_workspace("stale")
        subprocess.run(
            ["git", "-C", str(workspace), "remote", "remove", "origin"],
            check=True,
        )

        status, payload = self.request("GET", "/api/status")

        self.assertEqual(status, 200)
        self.assertEqual(payload["paths"], [])
        self.assertEqual(payload["workspaces"], [])
        self.assertEqual(payload["repositories"], [])
        self.assertEqual(payload["reconciliation"]["removed_paths"], 1)
        status, stats = self.request("GET", "/api/stats")
        self.assertEqual(status, 200)
        self.assertEqual(stats["repositories"], [])
        self.assertEqual(stats["projects"], [])

    def test_selection_endpoint_silently_removes_ineligible_repository(self):
        workspace = self.root / "local-only"
        workspace.mkdir()
        subprocess.run(
            ["git", "-C", str(workspace), "init", "-b", "main"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        status, payload = self.request(
            "POST", "/api/selection", {"paths": [str(workspace)]}
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["paths"], [])
        self.assertEqual(payload["workspaces"], [])
        self.assertEqual(payload["reconciliation"]["removed_paths"], 1)

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
        self.assertEqual(usage["version"], 2)
        self.assertIn("by_repository", usage)
        self.assertIn("by_workspace", usage)

    def test_usage_api_applies_current_project_identity_configuration(self):
        workspace, repository_id = self.select_github_workspace("company")
        cwd = str(workspace)
        status, _ = self.request(
            "POST",
            "/api/project-identities",
            {
                "version": 1,
                "projects": [
                    {
                        "id": "company",
                        "name": "Company",
                        "repositories": [repository_id],
                        "workspace_aliases": [cwd],
                    }
                ],
            },
        )
        self.assertEqual(status, 200)
        transcript = self.root / ".claude" / "projects" / "demo" / "session.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "cwd": cwd,
                    "timestamp": "2026-09-15T12:00:00Z",
                    "message": {
                        "id": "configured-message",
                        "model": "claude-sonnet-4-6",
                        "usage": {"input_tokens": 4, "output_tokens": 2},
                    },
                }
            )
            + "\n"
        )

        status, usage = self.request(
            "POST", "/api/ai/usage", {"sources": ["claude.code"]}
        )

        self.assertEqual(status, 200)
        self.assertEqual(usage["by_project"][0]["project_id"], "company")
        self.assertEqual(usage["by_project"][0]["project_name"], "Company")

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
