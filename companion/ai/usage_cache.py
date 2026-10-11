"""Incremental SQLite cache for privacy-safe normalized AI usage metadata."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = 1
EVENT_FIELDS = {
    "event_key",
    "session_id",
    "conversation_key",
    "execution_key",
    "execution_kind",
    "source_id",
    "tool",
    "surface",
    "provider",
    "model",
    "model_id",
    "project",
    "cwd",
    "date",
    "timestamp",
    "input_tokens",
    "cached_tokens",
    "output_tokens",
    "cache_write_tokens",
    "thinking_tokens",
    "visible_tokens",
    "parser",
    "parser_version",
    "confidence",
}
RECONCILIATION_FIELDS = {
    "execution_id",
    "execution_key",
    "conversation_key",
    "event_sum",
    "cumulative_total",
    "difference",
    "status",
}
WARNING_FIELDS = {
    "code",
    "source_id",
    "session_id",
    "execution_id",
    "event_key",
    "line",
    "path",
}


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _sanitize(row: Mapping[str, Any], allowed: set[str]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key in allowed}


def _sanitize_batch(batch: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {
        "events": [
            _sanitize(row, EVENT_FIELDS)
            for row in (batch.get("events") or [])
            if isinstance(row, Mapping)
        ],
        "reconciliation": [
            _sanitize(row, RECONCILIATION_FIELDS)
            for row in (batch.get("reconciliation") or [])
            if isinstance(row, Mapping)
        ],
        "warnings": [
            _sanitize(row, WARNING_FIELDS)
            for row in (batch.get("warnings") or [])
            if isinstance(row, Mapping)
        ],
    }


def _connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=5.0)
    try:
        con.execute("PRAGMA busy_timeout = 5000")
        con.execute("PRAGMA foreign_keys = ON")
        con.executescript(
            """
        CREATE TABLE IF NOT EXISTS cache_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY,
            source_id TEXT NOT NULL,
            path TEXT NOT NULL,
            size INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            parser TEXT NOT NULL,
            parser_version INTEGER NOT NULL,
            dependencies_json TEXT NOT NULL,
            UNIQUE(source_id, path)
        );
        CREATE TABLE IF NOT EXISTS events (
            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL,
            event_key TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY(file_id, ordinal)
        );
        CREATE TABLE IF NOT EXISTS reconciliation (
            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY(file_id, ordinal)
        );
        CREATE TABLE IF NOT EXISTS warnings (
            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY(file_id, ordinal)
        );
            """
        )
        version = con.execute(
            "SELECT value FROM cache_meta WHERE key = 'schema_version'"
        ).fetchone()
        if version is not None and int(version[0]) != SCHEMA_VERSION:
            raise sqlite3.DatabaseError("unsupported cache schema")
        con.execute(
            "INSERT OR REPLACE INTO cache_meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        con.commit()
    except Exception:
        con.close()
        raise
    return con


def _fingerprint(descriptor: Mapping[str, Any]) -> tuple[Any, ...]:
    dependencies = descriptor.get("dependencies") or []
    return (
        int(descriptor.get("size") or 0),
        int(descriptor.get("mtime_ns") or 0),
        str(descriptor.get("parser") or "unknown"),
        int(descriptor.get("parser_version") or 0),
        _json(dependencies),
    )


def _load_rows(
    con: sqlite3.Connection, source_ids: set[str] | None = None
) -> dict[str, list[dict[str, Any]]]:
    result = {"events": [], "reconciliation": [], "warnings": []}
    for table, key in (
        ("events", "events"),
        ("reconciliation", "reconciliation"),
        ("warnings", "warnings"),
    ):
        if source_ids:
            placeholders = ",".join("?" for _ in source_ids)
            rows = con.execute(
                f"""
                SELECT t.payload_json FROM {table} t
                JOIN files f ON f.id = t.file_id
                WHERE f.source_id IN ({placeholders})
                ORDER BY t.file_id, t.ordinal
                """,
                tuple(sorted(source_ids)),
            ).fetchall()
        else:
            rows = con.execute(
                f"SELECT payload_json FROM {table} ORDER BY file_id, ordinal"
            ).fetchall()
        result[key] = [json.loads(row[0]) for row in rows]
    return result


def _direct(descriptors: list[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result = {"events": [], "reconciliation": [], "warnings": []}
    for descriptor in descriptors:
        batch = _sanitize_batch(descriptor["parse"]())
        for key in result:
            result[key].extend(batch[key])
    return result


def _recover_corrupt(path: Path) -> None:
    if not path.exists():
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    try:
        path.replace(path.with_name(f"{path.name}.corrupt-{stamp}"))
    except OSError:
        pass


def refresh_usage_cache(
    cache_path: Path,
    descriptors: Iterable[Mapping[str, Any]],
    *,
    source_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Refresh changed descriptors atomically and return all normalized rows."""
    path = Path(cache_path)
    items = list(descriptors)
    selected_sources = set(source_ids or (str(item["source_id"]) for item in items))
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    try:
        con = _connect(path)
        os.chmod(path, 0o600)
    except (OSError, sqlite3.DatabaseError, ValueError) as cache_error:
        error_name = type(cache_error).__name__
        _recover_corrupt(path)
        try:
            direct = _direct(items)
        except Exception as parse_error:
            direct = {"events": [], "reconciliation": [], "warnings": []}
            error_name = type(parse_error).__name__
        direct["warnings"].append({"code": "cache_unavailable", "detail": error_name})
        return {
            **direct,
            "cache": {
                "status": "bypassed",
                "hits": 0,
                "misses": len(items),
                "reparsed": 0,
                "deleted": 0,
            },
        }

    counters = {"hits": 0, "misses": 0, "reparsed": 0, "deleted": 0}
    previous = _load_rows(con, selected_sources)
    try:
        con.execute("BEGIN IMMEDIATE")
        active_by_source: dict[str, set[str]] = {
            source_id: set() for source_id in selected_sources
        }
        for descriptor in items:
            source_id = str(descriptor["source_id"])
            source_path = str(Path(descriptor["path"]).resolve())
            active_by_source.setdefault(source_id, set()).add(source_path)
            fingerprint = _fingerprint(descriptor)
            row = con.execute(
                """
                SELECT id, size, mtime_ns, parser, parser_version, dependencies_json
                FROM files WHERE source_id = ? AND path = ?
                """,
                (source_id, source_path),
            ).fetchone()
            if row is not None and tuple(row[1:]) == fingerprint:
                counters["hits"] += 1
                continue

            batch = _sanitize_batch(descriptor["parse"]())
            if row is None:
                counters["misses"] += 1
                cursor = con.execute(
                    """
                    INSERT INTO files(
                        source_id, path, size, mtime_ns, parser,
                        parser_version, dependencies_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (source_id, source_path, *fingerprint),
                )
                file_id = int(cursor.lastrowid)
            else:
                counters["reparsed"] += 1
                file_id = int(row[0])
                con.execute(
                    """
                    UPDATE files SET size=?, mtime_ns=?, parser=?, parser_version=?,
                        dependencies_json=? WHERE id=?
                    """,
                    (*fingerprint, file_id),
                )
                for table in ("events", "reconciliation", "warnings"):
                    con.execute(f"DELETE FROM {table} WHERE file_id = ?", (file_id,))
            for table, key in (
                ("events", "events"),
                ("reconciliation", "reconciliation"),
                ("warnings", "warnings"),
            ):
                for ordinal, payload in enumerate(batch[key]):
                    if table == "events":
                        con.execute(
                            "INSERT INTO events VALUES (?, ?, ?, ?)",
                            (
                                file_id,
                                ordinal,
                                payload.get("event_key"),
                                _json(payload),
                            ),
                        )
                    else:
                        con.execute(
                            f"INSERT INTO {table} VALUES (?, ?, ?)",
                            (file_id, ordinal, _json(payload)),
                        )

        for source_id, active in active_by_source.items():
            rows = con.execute(
                "SELECT id, path FROM files WHERE source_id = ?", (source_id,)
            ).fetchall()
            for file_id, old_path in rows:
                if old_path not in active:
                    con.execute("DELETE FROM files WHERE id = ?", (file_id,))
                    counters["deleted"] += 1
        con.commit()
        result = _load_rows(con, selected_sources)
    except Exception as error:
        con.rollback()
        previous["warnings"].append(
            {"code": "cache_refresh_failed", "detail": type(error).__name__}
        )
        result = previous
        result["cache"] = {"status": "stale", **counters}
        con.close()
        return result
    con.close()
    return {
        **result,
        "cache": {
            "status": "rebuilt" if not existed else "ready",
            **counters,
        },
    }
