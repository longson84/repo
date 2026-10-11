"""List AI sessions across wave-1 adapters."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional, Sequence

from .adapters import antigravity, claude_code, codex, cursor
from .catalog import resolve_sources


WAVE1_LISTABLE = {
    "antigravity.app",
    "antigravity.ide",
    "antigravity.cli",
    "claude.code",
    "codex.home",
    "cursor.cli",
    "cursor.agent-transcripts",
}


def _sort_key(item: dict):
    stamp = item.get("updated_at") or item.get("started_at") or ""
    return stamp


def _matches_project(item: dict, project: str) -> bool:
    needle = project.lower()
    for key in ("project", "cwd", "title", "preview", "path"):
        value = item.get(key)
        if isinstance(value, str) and needle in value.lower():
            return True
    return False


def list_sessions_for_source(source: dict) -> list[dict]:
    source_id = source["id"]
    path = Path(source["path"])
    if source_id.startswith("antigravity."):
        return antigravity.list_sessions(path, source_id, source["surface"])
    if source_id == "claude.code":
        return claude_code.list_sessions(path, source_id)
    if source_id == "codex.home":
        return codex.list_sessions(path, source_id)
    if source_id == "cursor.cli":
        return cursor.list_cli_sessions(path, source_id)
    if source_id == "cursor.agent-transcripts":
        return cursor.list_agent_transcripts(path, source_id)
    return []


def list_sessions(
    *,
    sources: Optional[Sequence[str]] = None,
    project: Optional[str] = None,
    limit: int = 100,
    env: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
) -> dict:
    """Return wave-1 sessions, newest first."""
    if limit < 1:
        raise ValueError("limit must be >= 1")
    if limit > 2000:
        raise ValueError("limit must be <= 2000")
    resolved = resolve_sources(env=env, home=home)
    wanted = set(sources) if sources else None
    if wanted is not None:
        unknown = wanted - {s["id"] for s in resolved}
        if unknown:
            raise ValueError(f"Unknown source id(s): {', '.join(sorted(unknown))}")
    collected: list[dict] = []
    scanned = []
    for source in resolved:
        if source["id"] not in WAVE1_LISTABLE:
            continue
        if wanted is not None and source["id"] not in wanted:
            continue
        scanned.append(source["id"])
        if not Path(source["path"]).exists():
            continue
        collected.extend(list_sessions_for_source(source))
    if project:
        project = project.strip()
        if project:
            collected = [item for item in collected if _matches_project(item, project)]
    collected.sort(key=_sort_key, reverse=True)
    truncated = len(collected) > limit
    collected = collected[:limit]
    return {
        "version": 1,
        "listedAt": datetime.now(timezone.utc).isoformat(),
        "sources": scanned,
        "truncated": truncated,
        "count": len(collected),
        "sessions": collected,
    }
