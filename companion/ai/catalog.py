"""Known AI coding session store roots on this machine."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Optional, Sequence


# Spec: docs/superpowers/specs/2026-10-11-ai-local-history-design.md
SOURCE_DEFS: Sequence[dict] = (
    {
        "id": "antigravity.app",
        "tool": "antigravity",
        "surface": "app",
        "kind": "dir",
        "rel": ".gemini/antigravity",
        "notes": "App conversations + brain transcripts",
    },
    {
        "id": "antigravity.ide",
        "tool": "antigravity",
        "surface": "ide",
        "kind": "dir",
        "rel": ".gemini/antigravity-ide",
        "notes": "IDE conversations + brain transcripts",
    },
    {
        "id": "antigravity.cli",
        "tool": "antigravity",
        "surface": "cli",
        "kind": "dir",
        "rel": ".gemini/antigravity-cli",
        "notes": "agy CLI conversations + brain transcripts",
    },
    {
        "id": "claude.code",
        "tool": "claude-code",
        "surface": "cli",
        "kind": "dir",
        "rel": ".claude",
        "env_root": "CLAUDE_CONFIG_DIR",
        "notes": "Claude Code CLI; sessions under projects/**/*.jsonl",
    },
    {
        "id": "claude.desktop",
        "tool": "claude-desktop",
        "surface": "app",
        "kind": "dir",
        "rel": "Library/Application Support/Claude",
        "notes": "Desktop app local data; many chats also cloud-synced",
    },
    {
        "id": "codex.home",
        "tool": "codex",
        "surface": "cli+app",
        "kind": "dir",
        "rel": ".codex",
        "env_root": "CODEX_HOME",
        "notes": "CLI and Desktop share sessions under sessions/**/rollout-*.jsonl",
    },
    {
        "id": "codex.app-support",
        "tool": "codex",
        "surface": "app-support",
        "kind": "dir",
        "rel": "Library/Application Support/Codex",
        "notes": "Electron app shell; rollouts normally live in CODEX_HOME",
    },
    {
        "id": "cursor.cli",
        "tool": "cursor",
        "surface": "cli",
        "kind": "dir",
        "rel": ".cursor/chats",
        "notes": "CLI chats: <cwd-hash>/<id>/store.db",
    },
    {
        "id": "cursor.ide",
        "tool": "cursor",
        "surface": "ide",
        "kind": "dir",
        "rel": "Library/Application Support/Cursor",
        "notes": "IDE app data; composer state in User/globalStorage/state.vscdb",
    },
    {
        "id": "cursor.agent-transcripts",
        "tool": "cursor",
        "surface": "agent-transcripts",
        "kind": "dir",
        "rel": ".cursor/projects",
        "notes": "Per-project agent-transcripts/*.jsonl",
    },
)


def resolve_sources(
    env: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
) -> list[dict]:
    """Return concrete source entries with absolute paths."""
    env = env if env is not None else os.environ
    home = Path(home) if home is not None else Path.home()
    resolved = []
    for definition in SOURCE_DEFS:
        entry = dict(definition)
        env_key = definition.get("env_root")
        override = (env.get(env_key) or "").strip() if env_key else ""
        if override:
            path = Path(override).expanduser()
            # Claude/Codex overrides replace the tool home root.
            if definition["id"] in {"claude.code", "codex.home"}:
                entry["path"] = str(path)
            else:
                entry["path"] = str(home / definition["rel"])
        else:
            entry["path"] = str(home / definition["rel"])
        entry.pop("rel", None)
        entry.pop("env_root", None)
        resolved.append(entry)
    return resolved
