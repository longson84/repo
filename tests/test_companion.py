import importlib.util
import json
import pathlib
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "repo_companion", pathlib.Path(__file__).parents[1] / "companion" / "server.py"
)
module = importlib.util.module_from_spec(spec) if spec else None
if module and pathlib.Path(spec.origin).exists():
    spec.loader.exec_module(module)
else:
    module = None


class CompanionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)

    def api(self):
        self.assertIsNotNone(module, "Local companion is not implemented")
        return module

    def git(self, path, *args):
        return (
            subprocess.check_output(
                ["git", "-C", str(path), *args], stderr=subprocess.DEVNULL
            )
            .decode()
            .strip()
        )

    def repo(self, name):
        path = self.root / name
        path.mkdir()
        self.git(path, "init", "-b", "main")
        self.git(path, "config", "user.name", "Test")
        self.git(path, "config", "user.email", "test@example.com")
        (path / "file.txt").write_text("initial")
        self.git(path, "add", ".")
        self.git(path, "commit", "-m", "initial")
        return path

    def test_discovery_and_selection_are_persistent(self):
        api = self.api()
        repo = self.repo("chosen")
        self.repo("excluded")
        state = api.State([self.root], self.root / "settings.json")
        self.assertEqual(len(state.discover(str(self.root))), 2)
        state.select([str(repo)])
        loaded = api.State([self.root], self.root / "settings.json")
        self.assertEqual(loaded.paths, [str(repo.resolve())])

    def test_paths_cannot_escape_approved_roots(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as outside:
            state = api.State([self.root], self.root / "settings.json")
            with self.assertRaises(ValueError):
                state.select([outside])
            (self.root / "escape").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                state.discover(str(self.root / "escape"))

    def test_real_git_counts_and_changed_files(self):
        api = self.api()
        repo = self.repo("working")
        remote = self.root / "remote.git"
        subprocess.run(
            ["git", "init", "--bare", str(remote)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.git(repo, "remote", "add", "origin", str(remote))
        self.git(repo, "push", "-u", "origin", "main")
        (repo / "file.txt").write_text("second")
        self.git(repo, "commit", "-am", "second")
        (repo / "file.txt").write_text("dirty")
        (repo / "new.txt").write_text("untracked")
        result = api.inspect_repo(str(repo), True)
        self.assertEqual((result["ahead"], result["behind"]), (1, 0))
        self.assertEqual((result["modified"], result["untracked"]), (1, 1))
        self.assertEqual(result["upstream"], "origin/main")
        self.assertTrue(result["fresh"])

    def test_fetch_failure_does_not_claim_current_counts(self):
        api = self.api()
        repo = self.repo("offline")
        self.git(repo, "remote", "add", "origin", str(self.root / "missing.git"))
        self.git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.git(repo, "branch", "--set-upstream-to=origin/main")
        result = api.inspect_repo(str(repo), True)
        self.assertFalse(result["fresh"])
        self.assertIsNone(result["ahead"])
        self.assertTrue(result["error"])

    def test_request_auth_rejects_other_origins_hosts_and_tokens(self):
        api = self.api()
        self.assertTrue(
            api.authorized(
                "127.0.0.1:48731",
                "https://repo.theana.online",
                "Bearer secret",
                "secret",
                48731,
            )
        )
        for host, origin, token in [
            ("evil.example:48731", "https://repo.theana.online", "Bearer secret"),
            ("127.0.0.1:48731", "https://evil.example", "Bearer secret"),
            ("127.0.0.1:48731", "https://repo.theana.online", "Bearer wrong"),
        ]:
            self.assertFalse(api.authorized(host, origin, token, "secret", 48731))

    def test_collect_stats_reads_local_history_without_fetch(self):
        api = self.api()
        repo = self.repo("stats-repo")
        (repo / "app.py").write_text("print(1)\n")
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-m", "second")
        self.git(repo, "remote", "add", "origin", "https://github.com/example/stats-repo.git")
        calls = []
        original = api.git

        def tracking_git(path, *args, timeout=35):
            calls.append(args[0] if args else "")
            return original(path, *args, timeout=timeout)

        api.git = tracking_git
        try:
            result = api.collect_stats(str(repo))
        finally:
            api.git = original
        self.assertEqual(result["commitCount"], 2)
        self.assertEqual(result["commits7d"], 2)
        self.assertEqual(result["contributorCount"], 1)
        self.assertEqual(result["topContributors"][0]["name"], "Test")
        self.assertEqual(result["branchCount"], 1)
        self.assertEqual(result["remotes"][0]["name"], "origin")
        self.assertGreater(result["workingTreeBytes"], 0)
        self.assertTrue(any(item["extension"] == ".py" for item in result["languages"]))
        self.assertEqual(len(result["weeklyCommits"]), 12)
        self.assertEqual(len(result["commitsByDay"]), 30)
        self.assertEqual(len(result["commitsByWeek"]), 26)
        self.assertEqual(len(result["commitsByMonth"]), 24)
        self.assertGreaterEqual(len(result["commitsByYear"]), 1)
        self.assertEqual(sum(item["commits"] for item in result["commitsByDay"]), 2)
        self.assertEqual(sum(item["commits"] for item in result["commitsByYear"]), 2)
        self.assertIsInstance(result["commitsByDate"], dict)
        self.assertEqual(sum(result["commitsByDate"].values()), 2)
        self.assertIsNotNone(result["activitySummary"])
        self.assertGreaterEqual(result["activitySummary"]["peakDayCommits"], 1)
        self.assertIsNotNone(result["ageLabel"])
        self.assertIsInstance(result["hotspotFiles"], list)
        self.assertTrue(any(item["path"].endswith("app.py") for item in result["hotspotFiles"]))
        self.assertIsInstance(result["largeBlobs"], list)
        self.assertTrue(any(item.get("source") == "HEAD" for item in result["largeBlobs"]))
        self.assertIsNone(result["error"])
        self.assertNotIn("fetch", calls)

    def test_activity_summary_marks_quiet_and_burst_weeks(self):
        api = self.api()
        series = {
            "commitsByDay": [{"period": "2026-10-01", "commits": 5}],
            "commitsByWeek": [
                {"period": "2026-W39", "commits": 0},
                {"period": "2026-W40", "commits": 8},
                {"period": "2026-W41", "commits": 2},
            ],
        }
        summary = api.activity_summary(series)
        self.assertEqual(summary["peakWeekCommits"], 8)
        self.assertEqual(summary["quietWeeks"], 1)
        self.assertEqual(summary["activeWeeks"], 2)
        self.assertEqual(summary["burstRatio"], 2.4)

    def test_commit_series_buckets_day_week_month_year(self):
        api = self.api()
        series = api.commit_series(
            [
                "2026-10-01T10:00:00+00:00",
                "2026-10-01T12:00:00+00:00",
                "2026-09-15T09:00:00+00:00",
                "2025-01-02T09:00:00+00:00",
            ],
            now=api.datetime(2026, 10, 10, tzinfo=api.timezone.utc),
        )
        self.assertEqual(len(series["commitsByDay"]), 30)
        self.assertEqual(
            next(item["commits"] for item in series["commitsByDay"] if item["period"] == "2026-10-01"),
            2,
        )
        self.assertEqual(
            next(item["commits"] for item in series["commitsByMonth"] if item["period"] == "2026-09"),
            1,
        )
        self.assertEqual(
            next(item["commits"] for item in series["commitsByYear"] if item["period"] == "2025"),
            1,
        )
        self.assertEqual(
            next(item["commits"] for item in series["commitsByYear"] if item["period"] == "2026"),
            3,
        )

    def test_stats_cache_hit_head_invalidation_and_refresh(self):
        api = self.api()
        repo = self.repo("cached")
        state = api.State([self.root], self.root / "settings.json")
        state.select([str(repo)])
        first = state.stats_for_path(str(repo))
        self.assertEqual(first["commitCount"], 1)
        second = state.stats_for_path(str(repo))
        self.assertEqual(second["computedAt"], first["computedAt"])
        (repo / "file.txt").write_text("changed")
        self.git(repo, "commit", "-am", "change")
        third = state.stats_for_path(str(repo))
        self.assertEqual(third["commitCount"], 2)
        self.assertNotEqual(third["head"], first["head"])
        before = third["computedAt"]
        forced = state.stats_for_path(str(repo), refresh=True)
        self.assertEqual(forced["commitCount"], 2)
        self.assertGreaterEqual(forced["computedAt"], before)
        cached = state.cached_stats()
        self.assertEqual(len(cached), 1)
        self.assertEqual(cached[0]["path"], str(repo.resolve()))

    def test_pairing_token_reuses_file_until_rotated(self):
        api = self.api()
        path = self.root / "pairing-token"
        first = api.load_or_create_token(path)
        second = api.load_or_create_token(path)
        self.assertEqual(first, second)
        self.assertEqual(path.read_text(encoding="utf-8").strip(), first)
        rotated = api.load_or_create_token(path, rotate=True)
        self.assertNotEqual(rotated, first)
        self.assertEqual(api.load_or_create_token(path), rotated)

    def test_start_ui_server_serves_dist(self):
        api = self.api()
        dist = self.root / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html>ok</html>", encoding="utf-8")
        ui = api.start_ui_server(dist, port=0)
        self.assertIsNotNone(ui)
        try:
            port = ui.server_address[1]
            import urllib.request

            with urllib.request.urlopen(f"http://127.0.0.1:{port}/index.html") as response:
                self.assertEqual(response.read().decode(), "<html>ok</html>")
        finally:
            ui.shutdown()
            ui.server_close()


class HttpBoundaryTests(unittest.TestCase):
    def test_http_requires_token_and_allowed_origin(self):
        import http.client
        import threading

        with tempfile.TemporaryDirectory() as folder:
            server = module.ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
            server.state = module.State(
                [folder], pathlib.Path(folder) / "settings.json"
            )
            server.token = "test-token"
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for origin, authorization, expected in [
                    ("https://evil.example", "Bearer test-token", 403),
                    ("https://repo.theana.online", "Bearer wrong", 403),
                    ("https://repo.theana.online", "Bearer test-token", 200),
                ]:
                    connection = http.client.HTTPConnection(
                        "127.0.0.1", server.server_port
                    )
                    connection.request(
                        "GET",
                        "/api/status",
                        headers={"Origin": origin, "Authorization": authorization},
                    )
                    response = connection.getresponse()
                    self.assertEqual(response.status, expected)
                    response.read()
                    connection.close()
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
                connection.request(
                    "OPTIONS",
                    "/api/scan",
                    headers={"Origin": "https://repo.theana.online"},
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 204)
                self.assertEqual(
                    response.getheader("Access-Control-Allow-Origin"),
                    "https://repo.theana.online",
                )
                self.assertEqual(
                    response.getheader("Access-Control-Allow-Private-Network"), "true"
                )
                response.read()
                connection.close()
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
                connection.request(
                    "GET",
                    "/api/stats",
                    headers={
                        "Origin": "https://evil.example",
                        "Authorization": "Bearer test-token",
                    },
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                connection.close()
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
                connection.request(
                    "GET",
                    "/api/stats",
                    headers={
                        "Origin": "https://repo.theana.online",
                        "Authorization": "Bearer test-token",
                    },
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                payload = json.loads(response.read().decode())
                self.assertEqual(payload["repositories"], [])
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == "__main__":
    unittest.main()
