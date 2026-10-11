"""Claude Code CLI session listing and token usage (~/.claude/projects)."""

from __future__ import annotations

import json
import hashlib
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Optional

from .base import clip, project_name, summary
from ..identity import conversation_key, execution_key


PARSER_NAME = "claude-code-jsonl"
PARSER_VERSION = 2


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
    grouped: dict[str, dict[str, Any]] = {}
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
        preview, cwd, row_session_id, started = _first_user_preview(path)
        is_child = "subagents" in path.parts
        parent_session_id = None
        if is_child:
            subagent_index = path.parts.index("subagents")
            if subagent_index > 0:
                parent_session_id = path.parts[subagent_index - 1]
        sid = parent_session_id or row_session_id or path.stem
        conv_key = conversation_key("claude-code", sid)
        execution_id = path.stem if is_child else sid
        execution = {
            "id": execution_id,
            "execution_key": execution_key(source_id, execution_id),
            "parent_execution_key": (
                execution_key(source_id, sid) if is_child else None
            ),
            "kind": "child" if is_child else "root",
            "path": str(path),
            "started_at": started,
            "updated_at": _mtime_iso(path),
        }
        current = grouped.get(conv_key)
        if current is None:
            current = summary(
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
                size_bytes=0,
                path=str(path),
                conversation_key=conv_key,
                source_ids=[source_id],
                paths=[],
                execution_count=0,
                executions=[],
                identity_confidence="provider",
            )
            grouped[conv_key] = current
        current["size_bytes"] += size
        current["paths"].append(str(path))
        current["executions"].append(execution)
        current["execution_count"] = len(
            {item["execution_key"] for item in current["executions"]}
        )
        if not is_child:
            current.update(
                {
                    "cwd": cwd or current.get("cwd"),
                    "project": project_name(cwd) or current.get("project"),
                    "title": preview or current.get("title"),
                    "preview": preview or current.get("preview"),
                    "started_at": started or current.get("started_at"),
                    "path": str(path),
                }
            )
        updated = _mtime_iso(path)
        if updated and (not current.get("updated_at") or updated > current["updated_at"]):
            current["updated_at"] = updated
    return list(grouped.values())


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


def _file_identity(path: Path) -> tuple[str, str, str]:
    is_child = "subagents" in path.parts
    if is_child:
        subagent_index = path.parts.index("subagents")
        parent_id = path.parts[subagent_index - 1]
        return parent_id, path.stem, "child"
    return path.stem, path.stem, "root"


def _fallback_event_id(path: Path, line_number: int) -> str:
    digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
    return f"{digest}:{line_number}"


def usage_files(root: Path, source_id: str = "claude.code") -> list[dict[str, Any]]:
    projects = Path(root) / "projects"
    if not projects.is_dir():
        return []
    try:
        files = sorted(path for path in projects.rglob("*.jsonl") if not path.name.startswith("."))
    except OSError:
        return []
    descriptors = []
    for path in files:
        try:
            stat = path.stat()
        except OSError:
            continue
        descriptor = {
            "source_id": source_id,
            "path": path,
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "parser": PARSER_NAME,
            "parser_version": PARSER_VERSION,
        }
        descriptor["parse"] = lambda item=descriptor: parse_usage_file(item)
        descriptors.append(descriptor)
    return descriptors


def parse_usage_file(descriptor: Mapping[str, Any]) -> dict[str, Any]:
    path = Path(descriptor["path"])
    source_id = str(descriptor["source_id"])
    conversation_id, execution_id, execution_kind = _file_identity(path)
    cwd = None
    project = _project_from_encoded_dir(path.parent.name)
    events: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line_number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    warnings.append({"code": "malformed_json", "source_id": source_id, "path": str(path), "line": line_number})
                    continue
                if cwd is None and isinstance(row.get("cwd"), str):
                    cwd = row["cwd"]
                    project = project_name(cwd) or project
                if row.get("type") != "assistant" or not isinstance(row.get("message"), dict):
                    continue
                message = row["message"]
                usage = message.get("usage")
                if not isinstance(usage, dict):
                    continue
                inp = int(usage.get("input_tokens") or 0)
                cached = int(usage.get("cache_read_input_tokens") or 0)
                write = int(usage.get("cache_creation_input_tokens") or 0)
                out = int(usage.get("output_tokens") or 0)
                if not any((inp, cached, write, out)):
                    continue
                model = message.get("model") or row.get("model") or "unknown"
                provider_message_id = message.get("id")
                row_uuid = row.get("uuid")
                if provider_message_id:
                    event_key = f"claude-code:message:{provider_message_id}"
                    confidence = "provider"
                elif row_uuid:
                    event_key = f"claude-code:row:{row_uuid}"
                    confidence = "provider"
                else:
                    event_key = f"claude-code:record:{source_id}:{_fallback_event_id(path, line_number)}"
                    confidence = "path-derived"
                events.append(
                    {
                        "event_key": event_key,
                        "session_id": conversation_id,
                        "conversation_key": conversation_key(
                            "claude-code", conversation_id
                        ),
                        "execution_key": execution_key(source_id, execution_id),
                        "execution_kind": execution_kind,
                        "source_id": source_id,
                        "tool": "claude-code",
                        "surface": "cli",
                        "provider": "anthropic",
                        "model": str(model),
                        "project": project or "unknown",
                        "cwd": cwd,
                        "date": _date_from_ts(row.get("timestamp")),
                        "input_tokens": inp,
                        "cached_tokens": cached,
                        "output_tokens": out,
                        "cache_write_tokens": write,
                        "parser": PARSER_NAME,
                        "parser_version": PARSER_VERSION,
                        "confidence": confidence,
                    }
                )
    except OSError:
        warnings.append({"code": "read_error", "source_id": source_id, "path": str(path)})
    return merge_usage_batches(
        [{"events": events, "reconciliation": [], "warnings": warnings}]
    )


def merge_usage_batches(batches: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    events: OrderedDict[str, dict[str, Any]] = OrderedDict()
    warnings: list[dict[str, Any]] = []
    for batch in batches:
        warnings.extend(batch.get("warnings") or [])
        for event in batch.get("events") or []:
            event = dict(event)
            event_key = str(event["event_key"])
            previous = events.get(event_key)
            if previous is not None:
                for key in ("input_tokens", "cached_tokens", "output_tokens", "cache_write_tokens"):
                    event[key] = max(int(previous.get(key) or 0), int(event.get(key) or 0))
                if previous.get("execution_kind") == "child" and event.get("execution_kind") != "child":
                    event["execution_key"] = previous["execution_key"]
                    event["execution_kind"] = "child"
            events[event_key] = event
    return {
        "events": list(events.values()),
        "reconciliation": [],
        "warnings": warnings,
    }


def collect_usage(root: Path, source_id: str = "claude.code") -> dict[str, Any]:
    """Collect unique Claude provider calls plus parser quality warnings."""
    return merge_usage_batches(item["parse"]() for item in usage_files(root, source_id))


def iter_usage(root: Path, source_id: str = "claude.code") -> Iterator[dict[str, Any]]:
    """Yield unique Claude usage events."""
    yield from collect_usage(root, source_id)["events"]
