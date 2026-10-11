import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
AI = ROOT / "companion" / "ai"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


catalog = load("ai_catalog", AI / "catalog.py")


class CatalogTests(unittest.TestCase):
    def test_includes_first_class_sources(self):
        ids = {
            item["id"]
            for item in catalog.resolve_sources(env={}, home=pathlib.Path("/tmp/home"))
        }
        for required in (
            "antigravity.app",
            "antigravity.ide",
            "antigravity.cli",
            "claude.code",
            "claude.desktop",
            "codex.home",
            "codex.app-support",
            "cursor.cli",
            "cursor.ide",
            "cursor.agent-transcripts",
        ):
            self.assertIn(required, ids)

    def test_env_overrides_claude_and_codex_roots(self):
        home = pathlib.Path("/tmp/fake-home")
        sources = {
            item["id"]: item
            for item in catalog.resolve_sources(
                env={
                    "CLAUDE_CONFIG_DIR": "/custom/claude",
                    "CODEX_HOME": "/custom/codex",
                },
                home=home,
            )
        }
        self.assertEqual(sources["claude.code"]["path"], "/custom/claude")
        self.assertEqual(sources["codex.home"]["path"], "/custom/codex")
        self.assertEqual(
            sources["antigravity.app"]["path"],
            str(home / ".gemini/antigravity"),
        )


if __name__ == "__main__":
    unittest.main()
