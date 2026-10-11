import pathlib
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "companion"))

from commit_stats import (  # noqa: E402
    aggregate_repository_history,
    choose_primary_workspace,
    collect_repository_commits,
    collect_workspace_tip,
    parse_commit_numstat,
)


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


class RepositoryHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.repo = self.root / "main"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.name", "History Test")
        git(self.repo, "config", "user.email", "history@example.test")
        (self.repo / "base.txt").write_text("base\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "base")
        self.base_sha = git(self.repo, "rev-parse", "HEAD")

        self.feature = self.root / "feature"
        git(self.repo, "worktree", "add", "-b", "feature", str(self.feature))
        (self.feature / "feature.txt").write_text("feature\n")
        git(self.feature, "add", ".")
        git(self.feature, "commit", "-m", "feature")
        self.feature_sha = git(self.feature, "rev-parse", "HEAD")

        (self.repo / "main.txt").write_text("main\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "main")
        self.main_sha = git(self.repo, "rev-parse", "HEAD")

        self.detached = self.root / "detached"
        git(
            self.repo,
            "worktree",
            "add",
            "--detach",
            str(self.detached),
            self.base_sha,
        )
        (self.detached / "detached.txt").write_text("detached\n")
        git(self.detached, "add", ".")
        git(self.detached, "commit", "-m", "detached")
        self.detached_sha = git(self.detached, "rev-parse", "HEAD")

    def test_union_deduplicates_ancestry_and_keeps_every_tracked_tip(self):
        paths = [str(self.repo), str(self.feature), str(self.detached)]
        path_sum = sum(int(git(path, "rev-list", "--count", "HEAD")) for path in paths)

        commits = collect_repository_commits(paths)
        shas = {item["sha"] for item in commits}

        self.assertEqual(path_sum, 6)
        self.assertEqual(len(commits), 4)
        self.assertEqual(
            shas,
            {self.base_sha, self.feature_sha, self.main_sha, self.detached_sha},
        )

    def test_collector_uses_only_read_only_git_commands(self):
        calls = []

        def tracking_runner(path, *args):
            calls.append(args)
            return git(path, *args)

        collect_repository_commits(
            [str(self.repo), str(self.feature)], git_runner=tracking_runner
        )

        self.assertTrue(calls)
        self.assertEqual({args[0] for args in calls}, {"log"})
        forbidden = {"fetch", "pull", "checkout", "switch", "commit", "reset"}
        self.assertFalse(any(forbidden.intersection(args) for args in calls))

    def test_inaccessible_workspace_is_reported_without_losing_valid_history(self):
        warnings = []

        commits = collect_repository_commits(
            [str(self.repo), str(self.root / "missing")], warnings=warnings
        )

        self.assertEqual(len(commits), 2)
        self.assertEqual(warnings[0]["code"], "workspace_history_unavailable")
        self.assertEqual(warnings[0]["workspace_id"], str(self.root / "missing"))

    def test_two_clones_of_same_history_do_not_duplicate_commits(self):
        bare = self.root / "origin.git"
        git(self.repo, "clone", "--bare", str(self.repo), str(bare))
        clone = self.root / "clone"
        git(self.repo, "clone", str(bare), str(clone))

        commits = collect_repository_commits([str(self.repo), str(clone)])

        self.assertEqual(len(commits), 2)
        self.assertEqual(len({item["sha"] for item in commits}), 2)

    def test_tip_and_aggregate_expose_sha_author_timeline_and_churn(self):
        self.assertEqual(collect_workspace_tip(str(self.repo)), self.main_sha)
        commits = collect_repository_commits([str(self.repo), str(self.feature)])

        result = aggregate_repository_history(
            "gitdir:test",
            commits,
            now=datetime.now(timezone.utc),
        )

        self.assertEqual(result["repository_id"], "gitdir:test")
        self.assertEqual(result["commitCount"], 3)
        self.assertEqual(result["contributorCount"], 1)
        self.assertEqual(result["topContributors"][0]["name"], "History Test")
        self.assertEqual(result["linesTotal"]["additions"], 3)
        self.assertEqual(sum(result["commitsByDate"].values()), 3)
        self.assertIn(result["lastCommitSubject"], {"base", "main", "feature"})

    def test_parse_commit_numstat_ignores_binary_rows(self):
        text = (
            "\x1eabc\x1f2026-10-01T10:00:00+00:00\x1fA User\x1fa@example.test\x1fOne\n"
            "10\t2\tapp.py\n-\t-\tphoto.png\n"
            "\x1edef\x1f2026-10-02T10:00:00+00:00\x1fB User\x1fb@example.test\x1fTwo\n"
            "3\t1\treadme.md\n"
        )
        parsed = parse_commit_numstat(text)
        self.assertEqual(
            parsed,
            [
                {
                    "sha": "abc",
                    "iso": "2026-10-01T10:00:00+00:00",
                    "author_name": "A User",
                    "author_email": "a@example.test",
                    "subject": "One",
                    "additions": 10,
                    "deletions": 2,
                    "net": 8,
                },
                {
                    "sha": "def",
                    "iso": "2026-10-02T10:00:00+00:00",
                    "author_name": "B User",
                    "author_email": "b@example.test",
                    "subject": "Two",
                    "additions": 3,
                    "deletions": 1,
                    "net": 2,
                },
            ],
        )


class PrimaryWorkspaceTests(unittest.TestCase):
    def test_explicit_existing_workspace_wins_even_when_dirty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            first = root / "a"
            second = root / "b"
            first.mkdir()
            second.mkdir()
            workspaces = [
                {"path": str(first), "dirty": False},
                {"path": str(second), "dirty": True},
            ]
            self.assertEqual(
                choose_primary_workspace(workspaces, str(second)),
                str(second.resolve()),
            )

    def test_main_worktree_precedes_lexicographic_linked_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            linked = root / "a-linked"
            main = root / "z-main"
            linked.mkdir()
            main.mkdir()
            (linked / ".git").write_text("gitdir: elsewhere")
            (main / ".git").mkdir()
            workspaces = [{"path": str(linked)}, {"path": str(main)}]
            self.assertEqual(
                choose_primary_workspace(workspaces, None), str(main.resolve())
            )

    def test_missing_configured_path_falls_back_lexicographically(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            first = root / "a"
            second = root / "b"
            first.mkdir()
            second.mkdir()
            workspaces = [{"path": str(second)}, {"path": str(first)}]
            self.assertEqual(
                choose_primary_workspace(workspaces, str(root / "missing")),
                str(first.resolve()),
            )

    def test_no_existing_workspace_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            workspaces = [{"path": str(root / "missing")}]
            self.assertIsNone(choose_primary_workspace(workspaces, None))


if __name__ == "__main__":
    unittest.main()
