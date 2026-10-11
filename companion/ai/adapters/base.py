"""Shared session summary helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional


def project_name(cwd: Optional[str]) -> Optional[str]:
    if not cwd:
        return None
    parts = Path(cwd).parts
    for i, part in enumerate(parts):
        if part.lower() == "projects" and i + 1 < len(parts):
            return parts[i + 1]
    return Path(cwd).name or None


def clip(text: Optional[str], n: int = 160) -> Optional[str]:
    if not text:
        return None
    text = " ".join(str(text).split())
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


def summary(
    *,
    id: str,
    source_id: str,
    tool: str,
    surface: str,
    cwd: Optional[str] = None,
    project: Optional[str] = None,
    title: Optional[str] = None,
    preview: Optional[str] = None,
    started_at: Optional[str] = None,
    updated_at: Optional[str] = None,
    size_bytes: int = 0,
    path: Optional[str] = None,
    conversation_key: Optional[str] = None,
    source_ids: Optional[list[str]] = None,
    paths: Optional[list[str]] = None,
    execution_count: Optional[int] = None,
    executions: Optional[list[dict[str, Any]]] = None,
    identity_confidence: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> dict:
    payload = {
        "id": id,
        "source_id": source_id,
        "tool": tool,
        "surface": surface,
        "cwd": cwd,
        "project": project or project_name(cwd),
        "title": clip(title, 120),
        "preview": clip(preview, 160),
        "started_at": started_at,
        "updated_at": updated_at,
        "size_bytes": size_bytes,
        "path": path,
    }
    if conversation_key is not None:
        payload["conversation_key"] = conversation_key
    if source_ids is not None:
        payload["source_ids"] = source_ids
    if paths is not None:
        payload["paths"] = paths
    if execution_count is not None:
        payload["execution_count"] = execution_count
    if executions is not None:
        payload["executions"] = executions
    if identity_confidence is not None:
        payload["identity_confidence"] = identity_confidence
    if extra:
        payload["extra"] = extra
    return payload
