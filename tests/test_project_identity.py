import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "companion"))

from project_identity import (  # noqa: E402
    ProjectIdentityResolver,
    canonicalize_workspace_path,
    load_identity_config,
    normalize_remote_url,
    save_identity_config,
)


def run_git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def init_repo(path, remote=None):
    path.mkdir(parents=True)
    run_git(path, "init")
    run_git(path, "config", "user.email", "identity@example.test")
    run_git(path, "config", "user.name", "Identity Test")
    (path / "README.md").write_text("identity\n")
    run_git(path, "add", "README.md")
    run_git(path, "commit", "-m", "initial")
    if remote:
        run_git(path, "remote", "add", "origin", remote)


class PathAndRemoteTests(unittest.TestCase):
    def test_existing_components_and_symlinks_use_canonical_spelling(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            actual = root / "CamelProject"
            actual.mkdir()
            link = root / "project-link"
            link.symlink_to(actual, target_is_directory=True)

            self.assertEqual(
                canonicalize_workspace_path(str(link)), str(actual.resolve())
            )
            self.assertEqual(
                canonicalize_workspace_path(str(root / "camelproject")),
                str(actual.resolve()),
            )

    def test_missing_path_is_normalized_without_requiring_it_to_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = pathlib.Path(tmp) / "DeletedWorktree"
            self.assertEqual(
                canonicalize_workspace_path(str(missing)), str(missing.resolve())
            )
            with self.assertRaises(ValueError):
                canonicalize_workspace_path(str(missing), must_exist=True)

    def test_remote_forms_normalize_to_one_credential_free_identity(self):
        expected = "github.com/owner/repo"
        values = (
            "git@github.com:Owner/Repo.git",
            "ssh://git@github.com/Owner/Repo.git",
            "https://github.com/Owner/Repo.git",
            "https://user:secret@github.com/Owner/Repo.git?token=secret#fragment",
        )
        self.assertEqual({normalize_remote_url(value) for value in values}, {expected})

    def test_unusable_remote_values_return_none(self):
        for value in ("", "not a remote", "/local/repository", "file:///tmp/repo"):
            with self.subTest(value=value):
                self.assertIsNone(normalize_remote_url(value))


class ConfigTests(unittest.TestCase):
    def test_missing_config_loads_empty_versioned_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = load_identity_config(pathlib.Path(tmp) / "missing.json")
        self.assertEqual(payload, {"version": 1, "projects": []})

    def test_save_is_atomic_owner_only_and_round_trips_normalized_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            path = root / "project-identities.json"
            alias = root / "DeletedWorktree"
            payload = {
                "version": 1,
                "projects": [
                    {
                        "id": "companyq",
                        "name": "CompanyQ",
                        "repositories": ["remote:github.com/owner/companyq"],
                        "primary_workspaces": {
                            "remote:github.com/owner/companyq": str(root / "CompanyQ")
                        },
                        "workspace_aliases": [str(alias)],
                    }
                ],
            }

            saved = save_identity_config(path, payload)

            self.assertEqual(saved, load_identity_config(path))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertFalse(path.with_suffix(".tmp").exists())
            self.assertEqual(
                saved["projects"][0]["workspace_aliases"], [str(alias.resolve())]
            )
            self.assertNotIn("secret", path.read_text())

    def test_invalid_shapes_and_duplicate_ids_are_rejected(self):
        invalid = (
            {},
            {"version": 2, "projects": []},
            {"version": 1, "projects": {}},
            {
                "version": 1,
                "projects": [
                    {"id": "same", "name": "One"},
                    {"id": "same", "name": "Two"},
                ],
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "identities.json"
            for payload in invalid:
                with self.subTest(payload=payload), self.assertRaises(ValueError):
                    save_identity_config(path, payload)

    def test_repository_and_alias_cannot_belong_to_two_projects(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "identities.json"
            alias = str(pathlib.Path(tmp) / "Old")
            base = {
                "version": 1,
                "projects": [
                    {
                        "id": "one",
                        "name": "One",
                        "repositories": ["remote:github.com/owner/repo"],
                        "workspace_aliases": [alias],
                    },
                    {
                        "id": "two",
                        "name": "Two",
                        "repositories": ["remote:github.com/owner/repo"],
                        "workspace_aliases": [alias.upper()],
                    },
                ],
            }
            with self.assertRaisesRegex(ValueError, "repository"):
                save_identity_config(path, base)
            base["projects"][1]["repositories"] = []
            with self.assertRaisesRegex(ValueError, "alias"):
                save_identity_config(path, base)

    def test_load_rejects_malformed_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "identities.json"
            path.write_text("{broken")
            with self.assertRaises(ValueError):
                load_identity_config(path)


class ResolverTests(unittest.TestCase):
    def test_linked_worktrees_share_gitdir_repository_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "main"
            worktree = root / "feature"
            init_repo(repo)
            run_git(repo, "worktree", "add", "-b", "feature", str(worktree))

            resolver = ProjectIdentityResolver({"version": 1, "projects": []})
            main = resolver.resolve(str(repo))
            linked = resolver.resolve(str(worktree))

            self.assertEqual(main["repository_id"], linked["repository_id"])
            self.assertEqual(main["project_id"], linked["project_id"])
            self.assertNotEqual(main["workspace_id"], linked["workspace_id"])
            self.assertEqual(main["identity_confidence"], "git")

    def test_same_remote_merges_separate_clones(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            first = root / "one"
            second = root / "two"
            init_repo(first, "git@github.com:Owner/Product.git")
            init_repo(second, "https://github.com/owner/product.git")
            resolver = ProjectIdentityResolver({"version": 1, "projects": []})

            one = resolver.resolve(str(first))
            two = resolver.resolve(str(second))

            self.assertEqual(one["repository_id"], "remote:github.com/owner/product")
            self.assertEqual(one["repository_id"], two["repository_id"])

    def test_independent_same_basename_repositories_stay_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            first = root / "one" / "product"
            second = root / "two" / "product"
            init_repo(first)
            init_repo(second)
            resolver = ProjectIdentityResolver({"version": 1, "projects": []})

            one = resolver.resolve(str(first))
            two = resolver.resolve(str(second))

            self.assertNotEqual(one["repository_id"], two["repository_id"])
            self.assertNotEqual(one["project_id"], two["project_id"])
            self.assertEqual(one["project_name"], "product")
            self.assertEqual(two["project_name"], "product")

    def test_deleted_workspace_resolves_only_through_explicit_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            deleted = str(pathlib.Path(tmp) / "project-plan-6")
            config = {
                "version": 1,
                "projects": [
                    {
                        "id": "project",
                        "name": "Project",
                        "repositories": ["remote:github.com/owner/project"],
                        "workspace_aliases": [deleted],
                    }
                ],
            }
            configured = ProjectIdentityResolver(config).resolve(deleted)
            unconfigured = ProjectIdentityResolver(
                {"version": 1, "projects": []}
            ).resolve(deleted)

            self.assertEqual(configured["project_id"], "project")
            self.assertEqual(
                configured["repository_id"], "remote:github.com/owner/project"
            )
            self.assertEqual(configured["identity_confidence"], "configured")
            self.assertEqual(unconfigured["project_id"], "unattributed")
            self.assertEqual(unconfigured["identity_confidence"], "unattributed")

    def test_conflicting_unvalidated_aliases_remain_unattributed(self):
        with tempfile.TemporaryDirectory() as tmp:
            deleted = str(pathlib.Path(tmp) / "Old")
            config = {
                "version": 1,
                "projects": [
                    {"id": "one", "name": "One", "workspace_aliases": [deleted]},
                    {
                        "id": "two",
                        "name": "Two",
                        "workspace_aliases": [deleted],
                    },
                ],
            }

            result = ProjectIdentityResolver(config).resolve(deleted)

            self.assertEqual(result["project_id"], "unattributed")
            self.assertEqual(result["project_name"], "Unattributed")
            self.assertEqual(result["identity_confidence"], "unattributed")
            self.assertIn("conflicting_workspace_alias", result["identity_warnings"])

    def test_fingerprint_is_order_stable_and_changes_with_config(self):
        empty = ProjectIdentityResolver({"version": 1, "projects": []})
        same = ProjectIdentityResolver({"projects": [], "version": 1})
        changed = ProjectIdentityResolver(
            {
                "version": 1,
                "projects": [{"id": "one", "name": "One"}],
            }
        )
        self.assertEqual(empty.fingerprint(), same.fingerprint())
        self.assertNotEqual(empty.fingerprint(), changed.fingerprint())


if __name__ == "__main__":
    unittest.main()
