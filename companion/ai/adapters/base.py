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
    if extra:
        payload["extra"] = extra
    return payload
