"""Codex CLI/Desktop session listing and token usage (~/.codex/sessions)."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional

from ..identity import conversation_key, execution_key
from .base import clip, project_name, summary

PARSER_NAME = "codex-rollout-jsonl"
PARSER_VERSION = 2
TOKEN_FIELDS = (
    "input_tokens",
    "cached_tokens",
    "output_tokens",
    "cache_write_tokens",
)


def _rollout_files(root: Path) -> list[Path]:
    sessions = Path(root) / "sessions"
    if not sessions.is_dir():
        return []
    try:
        return sorted(sessions.rglob("rollout-*.jsonl"))
    except OSError:
        return []


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


def _mtime_iso(path: Path) -> Optional[str]:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return None


def _parent_from_nested(value: Any) -> Optional[str]:
    if not isinstance(value, dict):
        return None
    direct = value.get("parent_thread_id") or value.get("parent_id")
    if direct:
        return str(direct)
    for nested in value.values():
        found = _parent_from_nested(nested)
        if found:
            return found
    return None


def _parent_id(payload: Mapping[str, Any]) -> Optional[str]:
    direct = payload.get("parent_thread_id") or payload.get("forked_from_id")
    if direct:
        return str(direct)
    return _parent_from_nested(payload.get("source")) or _parent_from_nested(
        payload.get("thread_source")
    )


def collect_metadata(
    root: Path, source_id: str = "codex.home"
) -> dict[str, dict[str, Any]]:
    """Read rollout metadata before resolving parent relationships."""
    metadata: dict[str, dict[str, Any]] = {}
    for path in _rollout_files(Path(root)):
        payload: dict[str, Any] = {}
        started = None
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if row.get("type") != "session_meta":
                        continue
                    payload = (
                        row.get("payload")
                        if isinstance(row.get("payload"), dict)
                        else {}
                    )
                    stamp = row.get("timestamp") or payload.get("timestamp")
                    started = stamp if isinstance(stamp, str) else None
                    break
        except OSError:
            continue
        execution_id = str(payload.get("id") or payload.get("session_id") or path.stem)
        parent_id = _parent_id(payload)
        kind = (
            "branch"
            if payload.get("forked_from_id")
            else "child"
            if parent_id
            else "root"
        )
        metadata[execution_id] = {
            "id": execution_id,
            "source_id": source_id,
            "path": path,
            "payload": payload,
            "cwd": payload.get("cwd") if isinstance(payload.get("cwd"), str) else None,
            "started_at": started,
            "updated_at": _mtime_iso(path),
            "parent_id": parent_id,
            "kind": kind,
        }

    for execution_id, meta in metadata.items():
        seen: set[str] = set()
        current = execution_id
        warning = None
        while True:
            if current in seen:
                warning = "parent_cycle"
                root_id = execution_id
                break
            seen.add(current)
            current_meta = metadata.get(current)
            if current_meta is None:
                warning = "missing_parent"
                root_id = execution_id
                break
            parent_id = current_meta.get("parent_id")
            if not parent_id:
                root_id = current
                break
            if parent_id not in metadata:
                warning = "missing_parent"
                root_id = execution_id
                break
            current = str(parent_id)
        meta["root_id"] = root_id
        if warning:
            meta["warning"] = warning
    return metadata


def surface_for(
    meta: Mapping[str, Any], all_meta: Mapping[str, Mapping[str, Any]]
) -> str:
    """Derive user-facing surface, inheriting it from a resolved root."""
    root_id = str(meta.get("root_id") or meta.get("id") or "")
    root = all_meta.get(root_id, meta)
    payload = root.get("payload") if isinstance(root.get("payload"), dict) else {}
    originator = str(payload.get("originator") or "").strip().lower()
    if originator == "codex desktop":
        return "desktop"
    if originator == "codex-tui":
        return "terminal"
    if originator == "codex_exec":
        return "exec"
    source = payload.get("source")
    if isinstance(source, str):
        normalized = source.strip().lower()
        return {"vscode": "vscode", "cli": "terminal", "exec": "exec"}.get(
            normalized, "unknown"
        )
    return "unknown"


def list_sessions(root: Path, source_id: str = "codex.home") -> list[dict]:
    all_meta = collect_metadata(Path(root), source_id)
    grouped: dict[str, dict[str, Any]] = {}
    for execution_id, meta in all_meta.items():
        root_id = str(meta.get("root_id") or execution_id)
        root_meta = all_meta.get(root_id, meta)
        root_path = Path(root_meta["path"])
        conv_key = conversation_key("codex", root_id)
        row = grouped.get(conv_key)
        if row is None:
            cwd = root_meta.get("cwd")
            preview = _first_user_preview(root_path)
            row = summary(
                id=root_id,
                source_id=source_id,
                tool="codex",
                surface=surface_for(root_meta, all_meta),
                cwd=cwd,
                project=project_name(cwd),
                title=preview,
                preview=preview,
                started_at=root_meta.get("started_at"),
                updated_at=root_meta.get("updated_at"),
                size_bytes=0,
                path=str(root_path),
                conversation_key=conv_key,
                source_ids=[source_id],
                paths=[],
                execution_count=0,
                executions=[],
                identity_confidence=(
                    "provider" if not root_meta.get("warning") else "store-scoped"
                ),
            )
            grouped[conv_key] = row
        path = Path(meta["path"])
        try:
            row["size_bytes"] += path.stat().st_size
        except OSError:
            pass
        row["paths"].append(str(path))
        row["executions"].append(
            {
                "id": execution_id,
                "provider_execution_id": execution_id,
                "execution_key": execution_key(source_id, execution_id),
                "parent_execution_key": (
                    execution_key(source_id, str(meta["parent_id"]))
                    if meta.get("parent_id")
                    else None
                ),
                "kind": meta.get("kind") or "unknown",
                "path": str(path),
            }
        )
        row["execution_count"] = len(row["executions"])
        updated = meta.get("updated_at")
        if updated and (not row.get("updated_at") or updated > row["updated_at"]):
            row["updated_at"] = updated
    return list(grouped.values())


def _date_from_ts(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except ValueError:
        return value[:10] if len(value) >= 10 else None


def _normalized_tokens(raw: Mapping[str, Any]) -> dict[str, int]:
    total_input = int(raw.get("input_tokens") or 0)
    cached = int(raw.get("cached_input_tokens") or 0)
    return {
        "input_tokens": max(0, total_input - cached),
        "cached_tokens": cached,
        "output_tokens": int(raw.get("output_tokens") or 0),
        "cache_write_tokens": int(raw.get("cache_write_input_tokens") or 0),
    }


def collect_usage(root: Path, source_id: str = "codex.home") -> dict[str, Any]:
    """Collect per-call usage and per-execution cumulative reconciliation."""
    all_meta = collect_metadata(Path(root), source_id)
    events: list[dict[str, Any]] = []
    reconciliation: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for execution_id, meta in all_meta.items():
        path = Path(meta["path"])
        root_id = str(meta.get("root_id") or execution_id)
        model = "unknown"
        cwd = meta.get("cwd")
        project = project_name(cwd) or "unknown"
        event_sum: Counter[str] = Counter()
        latest_total: Optional[dict[str, int]] = None
        ordinal = 0
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                for line_number, line in enumerate(handle, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        warnings.append(
                            {
                                "code": "malformed_json",
                                "source_id": source_id,
                                "execution_id": execution_id,
                                "line": line_number,
                            }
                        )
                        continue
                    typ = row.get("type")
                    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
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
                    total = info.get("total_token_usage")
                    if isinstance(total, dict):
                        latest_total = _normalized_tokens(total)
                    last = info.get("last_token_usage")
                    if not isinstance(last, dict):
                        continue
                    normalized = _normalized_tokens(last)
                    if not any(normalized.values()):
                        continue
                    ordinal += 1
                    event_sum.update(normalized)
                    events.append(
                        {
                            "event_key": f"codex:{execution_id}:token-count:{ordinal}",
                            "session_id": root_id,
                            "conversation_key": conversation_key("codex", root_id),
                            "execution_key": execution_key(source_id, execution_id),
                            "execution_kind": meta.get("kind") or "unknown",
                            "source_id": source_id,
                            "tool": "codex",
                            "surface": surface_for(meta, all_meta),
                            "provider": "openai",
                            "model": model,
                            "project": project,
                            "cwd": cwd,
                            "date": _date_from_ts(row.get("timestamp")),
                            "timestamp": row.get("timestamp"),
                            **normalized,
                            "thinking_tokens": int(
                                last.get("reasoning_output_tokens") or 0
                            ),
                            "parser": PARSER_NAME,
                            "parser_version": PARSER_VERSION,
                            "confidence": "provider",
                        }
                    )
        except OSError:
            warnings.append(
                {
                    "code": "read_error",
                    "source_id": source_id,
                    "execution_id": execution_id,
                }
            )
            continue
        summed = {field: int(event_sum.get(field) or 0) for field in TOKEN_FIELDS}
        if latest_total is None:
            status = "unavailable"
            difference = None
        else:
            difference = {
                field: summed[field] - int(latest_total.get(field) or 0)
                for field in TOKEN_FIELDS
            }
            status = "matched" if not any(difference.values()) else "different"
        reconciliation.append(
            {
                "execution_id": execution_id,
                "execution_key": execution_key(source_id, execution_id),
                "conversation_key": conversation_key("codex", root_id),
                "event_sum": summed,
                "cumulative_total": latest_total,
                "difference": difference,
                "status": status,
            }
        )
        if meta.get("warning"):
            warnings.append(
                {
                    "code": str(meta["warning"]),
                    "source_id": source_id,
                    "execution_id": execution_id,
                }
            )
    return {
        "events": events,
        "reconciliation": reconciliation,
        "warnings": warnings,
    }


def iter_usage(root: Path, source_id: str = "codex.home") -> Iterator[dict[str, Any]]:
    """Yield normalized per-call Codex usage events."""
    yield from collect_usage(root, source_id)["events"]
