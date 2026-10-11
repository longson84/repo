"""Antigravity app / IDE / CLI session listing and token usage."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional
from urllib.parse import unquote, urlparse

from .base import clip, project_name, summary

USER_REQUEST_RE = re.compile(
    r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.S | re.I
)

# Model enum in steps.metadata (top-level field 11 / usage field 1).
MODEL_ENUM = {
    1318: "gemini-3-pro-high",
    1319: "gemini-3-flash",
    1035: "claude-sonnet-4-6",
}

STEP_TYPE_MODEL = 15


def _decode_varint(buf: bytes, index: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while index < len(buf):
        byte = buf[index]
        index += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, index
        shift += 7
        if shift > 63:
            raise ValueError("varint too long")
    raise ValueError("truncated varint")


def parse_protobuf_fields(buf: bytes) -> list[tuple[int, int, Any]]:
    """Return list of (field_number, wire_type, value)."""
    index = 0
    fields: list[tuple[int, int, Any]] = []
    while index < len(buf):
        try:
            key, index = _decode_varint(buf, index)
        except ValueError:
            break
        field = key >> 3
        wire = key & 7
        if wire == 0:
            try:
                value, index = _decode_varint(buf, index)
            except ValueError:
                break
            fields.append((field, 0, value))
        elif wire == 2:
            try:
                length, index = _decode_varint(buf, index)
            except ValueError:
                break
            data = buf[index : index + length]
            index += length
            fields.append((field, 2, data))
        elif wire == 1:
            if index + 8 > len(buf):
                break
            fields.append((field, 1, buf[index : index + 8]))
            index += 8
        elif wire == 5:
            if index + 4 > len(buf):
                break
            fields.append((field, 5, buf[index : index + 4]))
            index += 4
        else:
            break
    return fields


def parse_usage_metadata(meta: bytes) -> Optional[dict[str, Any]]:
    """Parse Antigravity model-step metadata blob into token counts."""
    if not meta:
        return None
    top = parse_protobuf_fields(meta)
    model_id = None
    date = None
    for field, wire, value in top:
        if field == 11 and wire == 0:
            model_id = int(value)
        if field == 1 and wire == 2 and isinstance(value, (bytes, bytearray)):
            ts_fields = {
                f: v for f, w, v in parse_protobuf_fields(bytes(value)) if w == 0
            }
            seconds = ts_fields.get(1)
            if isinstance(seconds, int) and seconds > 0:
                try:
                    date = datetime.fromtimestamp(seconds, tz=timezone.utc).strftime(
                        "%Y-%m-%d"
                    )
                except (OverflowError, OSError, ValueError):
                    date = None

    usage_blob = None
    for field, wire, value in top:
        if field == 9 and wire == 2 and isinstance(value, (bytes, bytearray)):
            usage_blob = bytes(value)
            break
    if usage_blob is None:
        return None

    inner = {
        f: v for f, w, v in parse_protobuf_fields(usage_blob) if w == 0
    }
    if model_id is None and isinstance(inner.get(1), int):
        model_id = int(inner[1])
    input_tokens = int(inner.get(2) or 0)
    output_tokens = int(inner.get(3) or 0)
    cached_tokens = int(inner.get(5) or 0)
    thinking = int(inner.get(9) or 0)
    visible = int(inner.get(10) or 0)
    if input_tokens == 0 and output_tokens == 0 and cached_tokens == 0:
        return None
    return {
        "model_id": model_id,
        "model": MODEL_ENUM.get(model_id or -1, f"antigravity-model-{model_id}"),
        "input_tokens": input_tokens,
        "cached_tokens": cached_tokens,
        "output_tokens": output_tokens,
        "thinking_tokens": thinking,
        "visible_tokens": visible,
        "date": date,
    }


def _workspace_from_uris(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    try:
        uris = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(uris, list) or not uris:
        return None
    uri = uris[0]
    if isinstance(uri, str) and uri.startswith("file://"):
        return unquote(urlparse(uri).path)
    return None


def _iso_from_sqlite(value: Optional[str]) -> Optional[str]:
    if not value or value.startswith("0001-01-01"):
        return None
    try:
        # "2026-09-12 06:47:55.261614+00:00"
        dt = datetime.fromisoformat(value.replace(" ", "T") if "T" not in value else value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except ValueError:
        return value


def _preview_and_cwd_from_transcript(path: Path) -> tuple[Optional[str], Optional[str]]:
    if not path.is_file():
        return None, None
    preview = None
    cwd = None
    path_re = re.compile(
        r"(?:file://|`|/Users/[^/\s\"']+/(?:Projects|projects)/[^/\s\"'`]+)"
    )
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for index, line in enumerate(handle):
                if index > 80 and preview:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                content = row.get("content")
                if preview is None and row.get("type") == "USER_INPUT" and isinstance(content, str):
                    match = USER_REQUEST_RE.search(content)
                    text = match.group(1) if match else content
                    preview = clip(text)
                if cwd is None and isinstance(content, str):
                    for match in path_re.finditer(content):
                        raw = match.group(0)
                        if raw.startswith("file://"):
                            raw = unquote(urlparse(raw).path)
                        elif raw.startswith("`") and raw.endswith("`"):
                            raw = raw[1:-1]
                        if "/Projects/" in raw or "/projects/" in raw:
                            # trim to project root
                            m = re.match(
                                r"(.*/(?:Projects|projects)/[^/]+)", raw
                            )
                            if m:
                                cwd = m.group(1)
                                break
                if preview and cwd:
                    break
    except OSError:
        return None, None
    return preview, cwd


def _mtime_iso(path: Path) -> Optional[str]:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return None


def _size(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def list_sessions(root: Path, source_id: str, surface: str) -> list[dict]:
    root = Path(root)
    if not root.is_dir():
        return []
    by_id: dict[str, dict] = {}
    summaries_db = root / "conversation_summaries.db"
    if summaries_db.is_file():
        try:
            con = sqlite3.connect(f"file:{summaries_db}?mode=ro", uri=True)
            rows = con.execute(
                """
                SELECT conversation_id, title, preview, workspace_uris,
                       last_modified_time, last_user_input_time, step_count
                FROM conversation_summaries
                """
            ).fetchall()
            con.close()
        except sqlite3.Error:
            rows = []
        for (
            cid,
            title,
            preview,
            workspace_uris,
            last_mod,
            last_user,
            step_count,
        ) in rows:
            if not cid:
                continue
            cwd = _workspace_from_uris(workspace_uris)
            conv = root / "conversations" / f"{cid}.db"
            brain = root / "brain" / cid
            transcript = (
                brain / ".system_generated" / "logs" / "transcript_full.jsonl"
            )
            t_preview, t_cwd = _preview_and_cwd_from_transcript(transcript)
            cwd = cwd or t_cwd
            size = _size(conv) + _size(transcript)
            by_id[cid] = summary(
                id=cid,
                source_id=source_id,
                tool="antigravity",
                surface=surface,
                cwd=cwd,
                project=project_name(cwd),
                title=title or preview or t_preview,
                preview=preview or t_preview,
                started_at=None,
                updated_at=_iso_from_sqlite(last_mod) or _iso_from_sqlite(last_user),
                size_bytes=size,
                path=str(transcript if transcript.is_file() else conv),
                extra={"step_count": step_count},
            )

    conv_dir = root / "conversations"
    if conv_dir.is_dir():
        for db in conv_dir.glob("*.db"):
            cid = db.stem
            if cid in by_id:
                continue
            brain = root / "brain" / cid
            transcript = (
                brain / ".system_generated" / "logs" / "transcript_full.jsonl"
            )
            t_preview, t_cwd = _preview_and_cwd_from_transcript(transcript)
            by_id[cid] = summary(
                id=cid,
                source_id=source_id,
                tool="antigravity",
                surface=surface,
                cwd=t_cwd,
                project=project_name(t_cwd),
                preview=t_preview,
                title=t_preview,
                updated_at=_mtime_iso(transcript if transcript.is_file() else db),
                size_bytes=_size(db) + _size(transcript),
                path=str(transcript if transcript.is_file() else db),
            )

    return list(by_id.values())


def iter_usage(
    root: Path, source_id: str, surface: str
) -> Iterator[dict[str, Any]]:
    """Yield per-model-step usage events from conversations/*.db."""
    root = Path(root)
    conv_dir = root / "conversations"
    if not conv_dir.is_dir():
        return
    try:
        dbs = sorted(conv_dir.glob("*.db"))
    except OSError:
        return
    for db in dbs:
        session_id = db.stem
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        except sqlite3.Error:
            continue
        try:
            try:
                rows = con.execute(
                    """
                    SELECT metadata FROM steps
                    WHERE step_type = ? AND metadata IS NOT NULL
                    """,
                    (STEP_TYPE_MODEL,),
                ).fetchall()
            except sqlite3.Error:
                continue
        finally:
            con.close()
        fallback_date = None
        try:
            fallback_date = datetime.fromtimestamp(
                db.stat().st_mtime, tz=timezone.utc
            ).strftime("%Y-%m-%d")
        except OSError:
            pass
        for (meta,) in rows:
            if not isinstance(meta, (bytes, bytearray)):
                continue
            parsed = parse_usage_metadata(bytes(meta))
            if not parsed:
                continue
            yield {
                "session_id": session_id,
                "source_id": source_id,
                "tool": "antigravity",
                "surface": surface,
                "model": parsed["model"],
                "model_id": parsed.get("model_id"),
                "date": parsed.get("date") or fallback_date,
                "input_tokens": parsed["input_tokens"],
                "cached_tokens": parsed["cached_tokens"],
                "output_tokens": parsed["output_tokens"],
                "cache_write_tokens": 0,
                "thinking_tokens": parsed.get("thinking_tokens") or 0,
                "visible_tokens": parsed.get("visible_tokens") or 0,
            }
