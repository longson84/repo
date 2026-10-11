import importlib.util
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

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

    def test_state_loads_empty_identity_config_and_inventory_keeps_legacy_fields(self):
        api = self.api()
        repo = self.repo("identity-repo")
        state = api.State([self.root], self.root / "settings.json")
        state.select([str(repo)])

        inventory = state.workspace_inventory()

        self.assertEqual(state.identity_path, self.root / "project-identities.json")
        self.assertEqual(state.identity_config, {"version": 1, "projects": []})
        self.assertEqual(len(inventory), 1)
        self.assertEqual(inventory[0]["path"], str(repo.resolve()))
        self.assertEqual(inventory[0]["name"], "identity-repo")
        self.assertEqual(inventory[0]["workspace_id"], str(repo.resolve()))
        self.assertTrue(inventory[0]["repository_id"].startswith("gitdir:"))
        self.assertEqual(inventory[0]["identity_confidence"], "git")

    def test_saving_identity_config_preserves_selection_and_invalidates_only_caches(self):
        api = self.api()
        repo = self.repo("configured")
        settings = self.root / "settings.json"
        state = api.State([self.root], settings)
        state.select([str(repo)])
        repository_id = state.workspace_inventory()[0]["repository_id"]
        state.stats_cache = {"version": 2, "repos": {str(repo): {"old": True}}}
        state.scan_cache = {
            "version": 2,
            "scannedAt": "2026-10-11T00:00:00+00:00",
            "repos": {str(repo): {"old": True}},
        }
        settings_before = settings.read_bytes()
        payload = {
            "version": 1,
            "projects": [
                {
                    "id": "configured",
                    "name": "Configured",
                    "repositories": [repository_id],
                }
            ],
        }

        with mock.patch.object(api, "git", side_effect=AssertionError("git mutation")):
            saved = state.save_identity_config(payload)

        self.assertEqual(saved["projects"][0]["id"], "configured")
        self.assertEqual(settings.read_bytes(), settings_before)
        self.assertEqual(state.paths, [str(repo.resolve())])
        self.assertEqual(state.stats_cache, {"version": 2, "repos": {}})
        self.assertEqual(
            state.scan_cache, {"version": 2, "scannedAt": None, "repos": {}}
        )
        self.assertEqual(state.identity_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(state.workspace_inventory()[0]["project_id"], "configured")

    def test_version_one_derived_caches_are_rebuilt_without_touching_identity(self):
        api = self.api()
        repo = self.repo("migration")
        settings = self.root / "settings.json"
        settings.write_text(json.dumps({"paths": [str(repo.resolve())]}) + "\n")
        identity_path = self.root / "project-identities.json"
        identity_text = json.dumps(
            {
                "version": 1,
                "projects": [{"id": "migration", "name": "Migration"}],
            }
        ) + "\n"
        identity_path.write_text(identity_text)
        (self.root / "stats.json").write_text(
            json.dumps({"version": 1, "repos": {str(repo): {"old": True}}})
        )
        (self.root / "scan.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "scannedAt": "2026-10-11T00:00:00+00:00",
                    "repos": {str(repo): {"old": True}},
                }
            )
        )

        state = api.State([self.root], settings)

        self.assertEqual(state.paths, [str(repo.resolve())])
        self.assertEqual(state.stats_cache, {"version": 2, "repos": {}})
        self.assertEqual(
            state.scan_cache, {"version": 2, "scannedAt": None, "repos": {}}
        )
        self.assertEqual(identity_path.read_text(), identity_text)

    def test_discovery_records_include_legacy_and_canonical_identity_fields(self):
        api = self.api()
        repo = self.repo("discoverable")
        state = api.State([self.root], self.root / "settings.json")

        result = state.discover(str(self.root))

        row = next(item for item in result if item["path"] == str(repo.resolve()))
        self.assertEqual(row["name"], "discoverable")
        self.assertEqual(row["workspace_id"], str(repo.resolve()))
        self.assertIn("repository_id", row)
        self.assertIn("project_id", row)

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
        self.git(
            repo, "remote", "add", "origin", "https://github.com/example/stats-repo.git"
        )
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
        self.assertIsInstance(result["linesTotal"], dict)
        self.assertGreaterEqual(result["linesTotal"]["additions"], 1)
        self.assertEqual(
            result["linesTotal"]["net"],
            result["linesTotal"]["additions"] - result["linesTotal"]["deletions"],
        )
        self.assertIsInstance(result["linesByDate"], dict)
        self.assertGreater(
            sum(item["additions"] for item in result["linesByDate"].values()), 0
        )
        if result.get("locCode") is not None:
            self.assertGreaterEqual(result["locCode"], 1)
            self.assertIsInstance(result["locByLanguage"], list)
        self.assertIsInstance(result["hotspotFiles"], list)
        self.assertTrue(
            any(item["path"].endswith("app.py") for item in result["hotspotFiles"])
        )
        self.assertIsInstance(result["largeBlobs"], list)
        self.assertTrue(
            any(item.get("source") == "HEAD" for item in result["largeBlobs"])
        )
        self.assertIsNone(result["error"])
        self.assertNotIn("fetch", calls)

    def test_parse_numstat_log_sums_commits_and_skips_binary(self):
        api = self.api()
        text = "\n".join(
            [
                "2026-10-01T10:00:00+00:00",
                "10\t2\tapp.py",
                "-\t-\tphoto.png",
                "",
                "2026-10-02T11:00:00+00:00",
                "3\t1\treadme.md",
            ]
        )
        commits = api.parse_numstat_log(text)
        self.assertEqual(len(commits), 2)
        self.assertEqual(commits[0]["additions"], 10)
        self.assertEqual(commits[0]["deletions"], 2)
        self.assertEqual(commits[0]["net"], 8)
        self.assertEqual(commits[1]["additions"], 3)
        summary = api.line_churn_summary(
            commits, now=api.datetime(2026, 10, 10, tzinfo=api.timezone.utc)
        )
        self.assertEqual(summary["linesTotal"]["additions"], 13)
        self.assertEqual(summary["linesTotal"]["deletions"], 3)
        self.assertEqual(summary["linesTotal"]["net"], 10)
        self.assertEqual(summary["linesByDate"]["2026-10-01"]["additions"], 10)

    def test_parse_scc_json_sums_code_comment_blank(self):
        api = self.api()
        payload = [
            {
                "Name": "Python",
                "Lines": 100,
                "Code": 70,
                "Comment": 10,
                "Blank": 20,
                "Count": 2,
                "Complexity": 12,
            },
            {
                "Name": "Markdown",
                "Lines": 50,
                "Code": 40,
                "Comment": 0,
                "Blank": 10,
                "Count": 1,
            },
            {
                "Name": "JSON",
                "Lines": 20,
                "Code": 20,
                "Comment": 0,
                "Blank": 0,
                "Count": 3,
            },
            {
                "Name": "CSS",
                "Lines": 30,
                "Code": 30,
                "Comment": 0,
                "Blank": 0,
                "Count": 1,
            },
            {
                "Name": "XML",
                "Lines": 40,
                "Code": 40,
                "Comment": 0,
                "Blank": 0,
                "Count": 2,
            },
            {
                "Name": "Dockerfile",
                "Lines": 15,
                "Code": 15,
                "Comment": 0,
                "Blank": 0,
                "Count": 1,
            },
        ]
        loc = api.parse_scc_json(payload)
        self.assertEqual(loc["locCode"], 70)
        self.assertEqual(loc["locComment"], 10)
        self.assertEqual(loc["locBlank"], 20)
        self.assertEqual(loc["locLines"], 100)
        self.assertEqual(loc["locFiles"], 2)
        self.assertEqual(loc["locComplexity"], 12)
        self.assertEqual(loc["locByLanguage"][0]["name"], "Python")
        self.assertEqual(loc["locByLanguage"][0]["complexity"], 12)
        self.assertEqual(len(loc["locByLanguage"]), 1)
        self.assertIn("TypeScript Typings", api.SCC_EXCLUDE_LANGUAGES)

    def test_collect_scc_loc_uses_scc_when_available(self):
        api = self.api()
        repo = self.repo("scc-repo")
        (repo / "main.py").write_text("# hi\nprint(1)\n\n")
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-m", "code")
        try:
            loc = api.collect_scc_loc(str(repo))
        except ValueError as error:
            if "not installed" in str(error):
                self.skipTest("scc not installed")
            raise
        self.assertGreaterEqual(loc["locCode"], 1)
        self.assertIsInstance(loc["locByLanguage"], list)

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
            next(
                item["commits"]
                for item in series["commitsByDay"]
                if item["period"] == "2026-10-01"
            ),
            2,
        )
        self.assertEqual(
            next(
                item["commits"]
                for item in series["commitsByMonth"]
                if item["period"] == "2026-09"
            ),
            1,
        )
        self.assertEqual(
            next(
                item["commits"]
                for item in series["commitsByYear"]
                if item["period"] == "2025"
            ),
            1,
        )
        self.assertEqual(
            next(
                item["commits"]
                for item in series["commitsByYear"]
                if item["period"] == "2026"
            ),
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

    def test_last_scan_persists_across_reload(self):
        api = self.api()
        repo = self.repo("scanned")
        config = self.root / "settings.json"
        state = api.State([self.root], config)
        state.select([str(repo)])
        payload = api.inspect_repo(str(repo), False)
        state.remember_scan([payload], "2026-10-11T00:00:00+00:00")
        loaded = api.State([self.root], config)
        snap = loaded.last_scan_payload()
        self.assertEqual(snap["scannedAt"], "2026-10-11T00:00:00+00:00")
        self.assertEqual(len(snap["repositories"]), 1)
        self.assertEqual(snap["repositories"][0]["path"], str(repo.resolve()))
        state.select([])
        empty = api.State([self.root], config)
        self.assertEqual(empty.last_scan_payload()["repositories"], [])
        self.assertIsNone(empty.last_scan_payload()["scannedAt"])

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

            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/index.html"
            ) as response:
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
