"""Claude Code CLI session listing and token usage (~/.claude/projects)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from .base import clip, project_name, summary


def _text_from_message(message: Any) -> Optional[str]:
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                parts.append(item)
        text = "\n".join(p for p in parts if p)
        return text or None
    return None


def _first_user_preview(path: Path) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Return preview, cwd, session_id, started_at from a Claude jsonl transcript."""
    preview = cwd = session_id = started = None
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if cwd is None and isinstance(row.get("cwd"), str):
                    cwd = row["cwd"]
                if session_id is None and isinstance(row.get("sessionId"), str):
                    session_id = row["sessionId"]
                if started is None and isinstance(row.get("timestamp"), str):
                    started = row["timestamp"]
                if preview is None and row.get("type") == "user":
                    text = _text_from_message(row.get("message"))
                    if text and not text.startswith("<command-"):
                        preview = clip(text)
                if preview and cwd and session_id:
                    break
    except OSError:
        pass
    return preview, cwd, session_id, started


def _mtime_iso(path: Path) -> Optional[str]:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return None


def list_sessions(root: Path, source_id: str = "claude.code") -> list[dict]:
    root = Path(root)
    projects = root / "projects"
    if not projects.is_dir():
        return []
    out = []
    try:
        files = list(projects.rglob("*.jsonl"))
    except OSError:
        return []
    for path in files:
        # Skip side-channel dumps if any; keep uuid-named transcripts.
        if path.name.startswith("."):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        preview, cwd, session_id, started = _first_user_preview(path)
        sid = session_id or path.stem
        out.append(
            summary(
                id=sid,
                source_id=source_id,
                tool="claude-code",
                surface="cli",
                cwd=cwd,
                project=project_name(cwd),
                title=preview,
                preview=preview,
                started_at=started,
                updated_at=_mtime_iso(path),
                size_bytes=size,
                path=str(path),
            )
        )
    return out


def _date_from_ts(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except ValueError:
        return value[:10] if len(value) >= 10 else None


def _project_from_encoded_dir(name: str) -> Optional[str]:
    """Best-effort project from Claude projects/<cwd-encoded>/ folder name."""
    if not name:
        return None
    for sep in ("-Projects-", "-projects-"):
        if sep in name:
            tail = name.split(sep, 1)[1]
            # Encoded path keeps `-` for `/`; project is usually the first segment.
            return tail.split("-")[0] or None
    return None


def iter_usage(root: Path, source_id: str = "claude.code") -> Iterator[dict[str, Any]]:
    """Yield usage from assistant messages that include message.usage."""
    root = Path(root)
    projects = root / "projects"
    if not projects.is_dir():
        return
    try:
        files = list(projects.rglob("*.jsonl"))
    except OSError:
        return
    for path in files:
        if path.name.startswith("."):
            continue
        session_fallback = path.stem
        cwd = None
        project = _project_from_encoded_dir(path.parent.name)
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if cwd is None and isinstance(row.get("cwd"), str):
                        cwd = row["cwd"]
                        project = project_name(cwd) or project
                    if row.get("type") != "assistant":
                        continue
                    message = row.get("message")
                    if not isinstance(message, dict):
                        continue
                    usage = message.get("usage")
                    if not isinstance(usage, dict):
                        continue
                    inp = int(usage.get("input_tokens") or 0)
                    cached = int(usage.get("cache_read_input_tokens") or 0)
                    write = int(usage.get("cache_creation_input_tokens") or 0)
                    out = int(usage.get("output_tokens") or 0)
                    if inp == 0 and cached == 0 and write == 0 and out == 0:
                        continue
                    model = message.get("model") or row.get("model") or "claude-sonnet-4-6"
                    sid = row.get("sessionId") or session_fallback
                    yield {
                        "session_id": str(sid),
                        "source_id": source_id,
                        "tool": "claude-code",
                        "surface": "cli",
                        "model": str(model),
                        "project": project or "unknown",
                        "cwd": cwd,
                        "date": _date_from_ts(row.get("timestamp")),
                        "input_tokens": inp,
                        "cached_tokens": cached,
                        "output_tokens": out,
                        "cache_write_tokens": write,
                    }
        except OSError:
            continue
