"""Cursor CLI chats and agent-transcripts listing."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .base import clip, project_name, summary
from ..identity import conversation_key, execution_key


def _mtime_iso(path: Path) -> Optional[str]:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return None


def _meta_json(db: Path) -> dict:
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        row = con.execute("SELECT value FROM meta LIMIT 1").fetchone()
        con.close()
    except sqlite3.Error:
        return {}
    if not row or row[0] is None:
        return {}
    raw = row[0]
    if isinstance(raw, bytes):
        try:
            # Often hex-encoded JSON
            text = bytes.fromhex(raw.decode("ascii")).decode("utf-8")
            return json.loads(text)
        except (ValueError, UnicodeError, json.JSONDecodeError):
            try:
                return json.loads(raw.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                return {}
    if isinstance(raw, str):
        try:
            return json.loads(bytes.fromhex(raw).decode("utf-8"))
        except (ValueError, UnicodeError, json.JSONDecodeError):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {}
    return {}


def _first_user_from_store(db: Path) -> tuple[Optional[str], Optional[str]]:
    """Return display text only; user messages are not workspace evidence."""
    preview = None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = con.execute("SELECT data FROM blobs").fetchall()
        con.close()
    except sqlite3.Error:
        return None, None
    for (data,) in rows:
        if not isinstance(data, (bytes, bytearray, memoryview)):
            continue
        blob = bytes(data)
        if not blob.startswith(b"{"):
            continue
        try:
            row = json.loads(blob.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            continue
        if row.get("role") != "user":
            continue
        content = row.get("content")
        text = None
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(str(part.get("text") or ""))
            text = "\n".join(parts)
        if not text:
            continue
        # Prefer a short user ask, skip pure user_info blocks as title if possible
        cleaned = text
        if "<user_info>" in cleaned and "</user_info>" in cleaned:
            cleaned = cleaned.split("</user_info>", 1)[-1].strip()
        if cleaned and preview is None:
            preview = clip(cleaned)
        if preview:
            break
    return preview, None


def list_cli_sessions(chats_root: Path, source_id: str = "cursor.cli") -> list[dict]:
    root = Path(chats_root)
    if not root.is_dir():
        return []
    out = []
    try:
        stores = list(root.rglob("store.db"))
    except OSError:
        return []
    for db in stores:
        # .../<cwdHash>/<chatId>/store.db
        chat_id = db.parent.name
        meta = _meta_json(db)
        preview, cwd = _first_user_from_store(db)
        name = meta.get("name") if isinstance(meta.get("name"), str) else None
        created = meta.get("createdAt")
        started = None
        if isinstance(created, (int, float)):
            # ms timestamp
            ts = created / 1000.0 if created > 1e12 else float(created)
            started = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        try:
            size = db.stat().st_size
        except OSError:
            size = 0
        out.append(
            summary(
                id=str(meta.get("agentId") or chat_id),
                source_id=source_id,
                tool="cursor",
                surface="cli",
                cwd=cwd,
                project=project_name(cwd),
                title=name if name and name != "New Agent" else preview,
                preview=preview or name,
                started_at=started,
                updated_at=_mtime_iso(db),
                size_bytes=size,
                path=str(db),
                conversation_key=conversation_key(
                    "cursor", str(meta.get("agentId") or chat_id)
                ),
                source_ids=[source_id],
                paths=[str(db)],
                execution_count=1,
                executions=[
                    {
                        "id": str(meta.get("agentId") or chat_id),
                        "provider_execution_id": str(meta.get("agentId") or chat_id),
                        "execution_key": execution_key(
                            source_id, str(meta.get("agentId") or chat_id)
                        ),
                        "parent_execution_key": None,
                        "kind": "root",
                    }
                ],
                identity_confidence="provider",
            )
        )
    return out


def list_agent_transcripts(
    projects_root: Path, source_id: str = "cursor.agent-transcripts"
) -> list[dict]:
    root = Path(projects_root)
    if not root.is_dir():
        return []
    out = []
    try:
        files = list(root.rglob("agent-transcripts/*/*.jsonl"))
    except OSError:
        # fallback broader
        try:
            files = [p for p in root.rglob("*.jsonl") if "agent-transcripts" in p.parts]
        except OSError:
            return []
    for path in files:
        if path.name.startswith("."):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        preview = None
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
                    role = row.get("role") or row.get("type")
                    if role in {"user", "human"}:
                        content = (
                            row.get("content") or row.get("text") or row.get("message")
                        )
                        if isinstance(content, str):
                            preview = clip(content)
                            break
                        if isinstance(content, list):
                            for part in content:
                                if isinstance(part, dict) and part.get("text"):
                                    preview = clip(str(part["text"]))
                                    break
                            if preview:
                                break
        except OSError:
            pass
        # Encode project folder from path: .../projects/<encoded>/agent-transcripts/...
        project = None
        parts = path.parts
        if "projects" in parts:
            idx = parts.index("projects")
            if idx + 1 < len(parts):
                encoded = parts[idx + 1]
                project = (
                    encoded.replace("-Users-", "").split("-Projects-")[-1]
                    if "Projects" in encoded or "projects" in encoded
                    else encoded
                )
        out.append(
            summary(
                id=path.stem,
                source_id=source_id,
                tool="cursor",
                surface="unknown",
                project=project,
                title=preview,
                preview=preview,
                updated_at=_mtime_iso(path),
                size_bytes=size,
                path=str(path),
                conversation_key=conversation_key("cursor", path.stem),
                source_ids=[source_id],
                paths=[str(path)],
                execution_count=1,
                executions=[
                    {
                        "id": path.stem,
                        "provider_execution_id": path.stem,
                        "execution_key": execution_key(source_id, path.stem),
                        "parent_execution_key": None,
                        "kind": "unknown",
                    }
                ],
                identity_confidence="provider",
            )
        )
    return out
