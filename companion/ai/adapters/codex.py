"""Codex CLI/Desktop session listing and token usage (~/.codex/sessions)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from .base import clip, project_name, summary


def _first_user_preview(path: Path) -> Optional[str]:
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
                typ = row.get("type")
                payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                if typ in {"user_message", "user"} or (
                    typ == "event_msg" and payload.get("type") == "user_message"
                ):
                    text = (
                        payload.get("message")
                        or payload.get("text")
                        or payload.get("content")
                        or row.get("message")
                        or row.get("text")
                    )
                    if isinstance(text, str) and text.strip():
                        return clip(text)
                # Some rollouts nest content differently
                if typ == "response_item" and payload.get("role") == "user":
                    content = payload.get("content")
                    if isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict) and part.get("text"):
                                return clip(str(part["text"]))
                    elif isinstance(content, str):
                        return clip(content)
    except OSError:
        return None
    return None


def _session_meta(path: Path) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return cwd, session_id, started_at from first session_meta line."""
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for _ in range(20):
                line = handle.readline()
                if not line:
                    break
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("type") != "session_meta":
                    continue
                payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else None
                sid = payload.get("id") or payload.get("session_id")
                started = row.get("timestamp") or payload.get("timestamp")
                return cwd, str(sid) if sid else None, started if isinstance(started, str) else None
    except OSError:
        pass
    return None, None, None


def _mtime_iso(path: Path) -> Optional[str]:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return None


def list_sessions(root: Path, source_id: str = "codex.home") -> list[dict]:
    root = Path(root)
    sessions = root / "sessions"
    if not sessions.is_dir():
        return []
    out = []
    try:
        files = list(sessions.rglob("rollout-*.jsonl"))
    except OSError:
        return []
    for path in files:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        cwd, sid, started = _session_meta(path)
        sid = sid or path.stem
        preview = _first_user_preview(path)
        out.append(
            summary(
                id=sid,
                source_id=source_id,
                tool="codex",
                surface="cli+app",
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


def iter_usage(root: Path, source_id: str = "codex.home") -> Iterator[dict[str, Any]]:
    """Yield per-request usage from token_count.last_token_usage (not cumulative totals)."""
    root = Path(root)
    sessions = root / "sessions"
    if not sessions.is_dir():
        return
    try:
        files = list(sessions.rglob("rollout-*.jsonl"))
    except OSError:
        return
    for path in files:
        session_id = path.stem
        model = "gpt-5.5"
        cwd = None
        project = "unknown"
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
                    typ = row.get("type")
                    payload = (
                        row.get("payload") if isinstance(row.get("payload"), dict) else {}
                    )
                    if typ == "session_meta":
                        sid = payload.get("id") or payload.get("session_id")
                        if sid:
                            session_id = str(sid)
                        if isinstance(payload.get("cwd"), str):
                            cwd = payload["cwd"]
                            project = project_name(cwd) or "unknown"
                        continue
                    if typ == "turn_context":
                        if isinstance(payload.get("model"), str):
                            model = payload["model"]
                        if cwd is None and isinstance(payload.get("cwd"), str):
                            cwd = payload["cwd"]
                            project = project_name(cwd) or project
                        continue
                    if typ != "event_msg" or payload.get("type") != "token_count":
                        continue
                    info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
                    last = info.get("last_token_usage")
                    if not isinstance(last, dict):
                        continue
                    total_in = int(last.get("input_tokens") or 0)
                    cached = int(last.get("cached_input_tokens") or 0)
                    out = int(last.get("output_tokens") or 0)
                    write = int(last.get("cache_write_input_tokens") or 0)
                    # Codex input_tokens includes cached; bill uncached separately.
                    uncached = max(0, total_in - cached)
                    if uncached == 0 and cached == 0 and out == 0 and write == 0:
                        continue
                    yield {
                        "session_id": session_id,
                        "source_id": source_id,
                        "tool": "codex",
                        "surface": "cli+app",
                        "model": model,
                        "project": project,
                        "cwd": cwd,
                        "date": _date_from_ts(row.get("timestamp")),
                        "input_tokens": uncached,
                        "cached_tokens": cached,
                        "output_tokens": out,
                        "cache_write_tokens": write,
                    }
        except OSError:
            continue
