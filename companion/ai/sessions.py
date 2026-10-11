"""List AI sessions across wave-1 adapters."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

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


def _merge_conversations(rows: Sequence[dict]) -> list[dict]:
    grouped: dict[str, dict[str, Any]] = {}
    confidence_rank = {"path-derived": 0, "store-scoped": 1, "provider": 2}
    for raw in rows:
        row = copy.deepcopy(raw)
        key = row.get("conversation_key")
        if not isinstance(key, str) or not key:
            key = f"{row.get('source_id')}:conversation:{row.get('id')}"
            row["conversation_key"] = key
            row["identity_confidence"] = "store-scoped"
        row.setdefault("source_ids", [row.get("source_id")])
        row.setdefault("paths", [row.get("path")] if row.get("path") else [])
        row.setdefault("executions", [])
        current = grouped.get(key)
        if current is None:
            row["source_ids"] = sorted({item for item in row["source_ids"] if item})
            row["paths"] = sorted({item for item in row["paths"] if item})
            grouped[key] = row
            continue

        current["source_ids"] = sorted(
            set(current.get("source_ids") or []) | set(row.get("source_ids") or [])
        )
        current["paths"] = sorted(
            set(current.get("paths") or []) | set(row.get("paths") or [])
        )
        current["size_bytes"] = int(current.get("size_bytes") or 0) + int(
            row.get("size_bytes") or 0
        )
        executions: dict[str, dict] = {}
        for item in (current.get("executions") or []) + (row.get("executions") or []):
            logical = item.get("provider_execution_id") or item.get("execution_key")
            if logical:
                executions[str(logical)] = item
        current["executions"] = list(executions.values())

        current_confidence = str(current.get("identity_confidence") or "path-derived")
        incoming_confidence = str(row.get("identity_confidence") or "path-derived")
        prefer_incoming = confidence_rank.get(
            incoming_confidence, 0
        ) > confidence_rank.get(current_confidence, 0)
        if current.get("surface") in {None, "unknown", "agent-transcripts"} and row.get(
            "surface"
        ) not in {None, "unknown", "agent-transcripts"}:
            prefer_incoming = True
        if prefer_incoming:
            for field in (
                "id",
                "source_id",
                "tool",
                "surface",
                "cwd",
                "project",
                "title",
                "preview",
                "path",
                "identity_confidence",
            ):
                if row.get(field) is not None:
                    current[field] = row[field]
        else:
            for field in ("cwd", "project", "title", "preview", "path"):
                if not current.get(field) and row.get(field):
                    current[field] = row[field]
        starts = [
            value
            for value in (current.get("started_at"), row.get("started_at"))
            if value
        ]
        updates = [
            value
            for value in (current.get("updated_at"), row.get("updated_at"))
            if value
        ]
        current["started_at"] = min(starts) if starts else None
        current["updated_at"] = max(updates) if updates else None

    merged = []
    for row in grouped.values():
        row["source_ids"] = sorted(set(row.get("source_ids") or []))
        row["paths"] = sorted(set(row.get("paths") or []))
        executions: dict[str, dict] = {}
        for item in row.get("executions") or []:
            logical = item.get("provider_execution_id") or item.get("execution_key")
            if logical:
                executions[str(logical)] = item
        row["executions"] = sorted(
            executions.values(), key=lambda item: str(item.get("execution_key") or "")
        )
        row["execution_count"] = len(row["executions"])
        row["representation_count"] = len(row["paths"])
        merged.append(row)
    return merged


def collect_sessions(
    *,
    sources: Optional[Sequence[str]] = None,
    project: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
) -> list[dict]:
    """Return all matching logical conversations before sorting and limiting."""
    resolved = resolve_sources(env=env, home=home)
    wanted = set(sources) if sources else None
    if wanted is not None:
        unknown = wanted - {s["id"] for s in resolved}
        if unknown:
            raise ValueError(f"Unknown source id(s): {', '.join(sorted(unknown))}")
    representations: list[dict] = []
    for source in resolved:
        if source["id"] not in WAVE1_LISTABLE:
            continue
        if wanted is not None and source["id"] not in wanted:
            continue
        if not Path(source["path"]).exists():
            continue
        representations.extend(list_sessions_for_source(source))
    collected = _merge_conversations(representations)
    if project:
        project = project.strip()
        if project:
            collected = [item for item in collected if _matches_project(item, project)]
    return collected


def resolve_conversation_selectors(
    rows: Sequence[dict],
    *,
    conversation_keys: Optional[Sequence[str]],
    compound_sessions: Optional[Sequence[Mapping[str, str]]],
    legacy_session_ids: Optional[Sequence[str]],
) -> Optional[set[str]]:
    """Resolve canonical, compound, and guarded legacy conversation selectors."""
    if not conversation_keys and not compound_sessions and not legacy_session_ids:
        return None
    known = {str(row.get("conversation_key")): row for row in rows}
    selected: set[str] = set()
    for key in conversation_keys or []:
        if key not in known:
            raise ValueError(f"Unknown conversation key: {key}")
        selected.add(key)
    for selector in compound_sessions or []:
        source_id = selector.get("source_id")
        session_id = selector.get("session_id")
        matches = [
            row
            for row in rows
            if row.get("id") == session_id
            and source_id in (row.get("source_ids") or [])
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Unknown or ambiguous compound session: {source_id}:{session_id}"
            )
        selected.add(str(matches[0]["conversation_key"]))
    for session_id in legacy_session_ids or []:
        matches = [row for row in rows if row.get("id") == session_id]
        if len(matches) > 1:
            candidates = ", ".join(
                sorted(str(row["conversation_key"]) for row in matches)
            )
            raise ValueError(
                f"Ambiguous session id {session_id}; candidates: {candidates}"
            )
        if not matches:
            raise ValueError(f"Unknown session id: {session_id}")
        selected.add(str(matches[0]["conversation_key"]))
    return selected


def list_sessions(
    *,
    sources: Optional[Sequence[str]] = None,
    project: Optional[str] = None,
    limit: int = 100,
    env: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
    include_executions: bool = False,
    conversation_keys: Optional[Sequence[str]] = None,
) -> dict:
    """Return logical conversations, newest first."""
    if limit < 1:
        raise ValueError("limit must be >= 1")
    if limit > 2000:
        raise ValueError("limit must be <= 2000")
    resolved = resolve_sources(env=env, home=home)
    wanted = set(sources) if sources else None
    scanned = [
        source["id"]
        for source in resolved
        if source["id"] in WAVE1_LISTABLE and (wanted is None or source["id"] in wanted)
    ]
    collected = collect_sessions(sources=sources, project=project, env=env, home=home)
    if conversation_keys:
        wanted_keys = set(conversation_keys)
        unknown = wanted_keys - {str(row.get("conversation_key")) for row in collected}
        if unknown:
            raise ValueError(
                f"Unknown conversation key(s): {', '.join(sorted(unknown))}"
            )
        collected = [
            row for row in collected if row.get("conversation_key") in wanted_keys
        ]
    collected.sort(key=_sort_key, reverse=True)
    truncated = len(collected) > limit
    collected = collected[:limit]
    execution_count = sum(int(item.get("execution_count") or 0) for item in collected)
    representation_count = sum(
        int(item.get("representation_count") or 0) for item in collected
    )
    if not include_executions:
        collected = [
            {key: value for key, value in item.items() if key != "executions"}
            for item in collected
        ]
    return {
        "version": 1,
        "listedAt": datetime.now(timezone.utc).isoformat(),
        "sources": scanned,
        "truncated": truncated,
        "count": len(collected),
        "conversation_count": len(collected),
        "execution_count": execution_count,
        "representation_count": representation_count,
        "sessions": collected,
    }
