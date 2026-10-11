"""Search local AI transcript stores (rg when available, else Python scan)."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence

from .catalog import resolve_sources

WAVE1_SEARCHABLE = {
    "antigravity.app",
    "antigravity.ide",
    "antigravity.cli",
    "claude.code",
    "codex.home",
    "cursor.cli",
    "cursor.agent-transcripts",
}

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
SNIPPET_LEN = 200
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_RG_FILES = 4000


def _snippet(text: str, needle: str, *, width: int = SNIPPET_LEN) -> str:
    compact = " ".join(text.split())
    if not compact:
        return ""
    lower = compact.lower()
    idx = lower.find(needle.lower())
    if idx < 0:
        return compact[:width] + ("…" if len(compact) > width else "")
    half = max(20, width // 2)
    start = max(0, idx - half)
    end = min(len(compact), idx + len(needle) + half)
    out = compact[start:end]
    if start > 0:
        out = "…" + out
    if end < len(compact):
        out = out + "…"
    if len(out) > width + 2:
        out = out[: width - 1] + "…"
    return out


def _session_from_path(source_id: str, path: Path) -> str:
    parts = path.parts
    if source_id.startswith("antigravity."):
        if "brain" in parts:
            i = parts.index("brain")
            if i + 1 < len(parts):
                return parts[i + 1]
        return path.stem
    if source_id == "cursor.cli":
        return path.parent.name
    if source_id == "cursor.agent-transcripts":
        return path.stem
    if source_id == "codex.home":
        return path.stem
    return path.stem


def iter_text_targets(
    source: Mapping[str, Any],
) -> Iterator[dict[str, Any]]:
    """Yield searchable file targets for a source."""
    source_id = source["id"]
    root = Path(source["path"])
    if not root.exists():
        return
    tool = source["tool"]
    surface = source["surface"]

    if source_id.startswith("antigravity."):
        brain = root / "brain"
        if not brain.is_dir():
            return
        try:
            files = brain.rglob("transcript_full.jsonl")
        except OSError:
            return
        for path in files:
            if path.is_file():
                yield {
                    "path": path,
                    "source_id": source_id,
                    "tool": tool,
                    "surface": surface,
                    "session_id": _session_from_path(source_id, path),
                    "kind": "text",
                }
        return

    if source_id == "claude.code":
        projects = root / "projects"
        if not projects.is_dir():
            return
        try:
            files = projects.rglob("*.jsonl")
        except OSError:
            return
        for path in files:
            if path.name.startswith(".") or not path.is_file():
                continue
            yield {
                "path": path,
                "source_id": source_id,
                "tool": tool,
                "surface": surface,
                "session_id": path.stem,
                "kind": "text",
            }
        return

    if source_id == "codex.home":
        sessions = root / "sessions"
        if not sessions.is_dir():
            return
        try:
            files = sessions.rglob("rollout-*.jsonl")
        except OSError:
            return
        for path in files:
            if not path.is_file():
                continue
            sid = path.stem
            # Prefer session id from first meta line when cheap.
            try:
                with path.open(encoding="utf-8", errors="replace") as handle:
                    for _ in range(5):
                        line = handle.readline()
                        if not line:
                            break
                        try:
                            row = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if row.get("type") == "session_meta":
                            payload = row.get("payload") or {}
                            if isinstance(payload, dict):
                                raw = payload.get("id") or payload.get("session_id")
                                if raw:
                                    sid = str(raw)
                            break
            except OSError:
                pass
            yield {
                "path": path,
                "source_id": source_id,
                "tool": tool,
                "surface": surface,
                "session_id": sid,
                "kind": "text",
            }
        return

    if source_id == "cursor.agent-transcripts":
        if not root.is_dir():
            return
        try:
            files = [
                p
                for p in root.rglob("*.jsonl")
                if "agent-transcripts" in p.parts and p.is_file()
            ]
        except OSError:
            return
        for path in files:
            if path.name.startswith("."):
                continue
            yield {
                "path": path,
                "source_id": source_id,
                "tool": tool,
                "surface": surface,
                "session_id": path.stem,
                "kind": "text",
            }
        return

    if source_id == "cursor.cli":
        if not root.is_dir():
            return
        try:
            files = root.rglob("store.db")
        except OSError:
            return
        for path in files:
            if path.is_file():
                yield {
                    "path": path,
                    "source_id": source_id,
                    "tool": tool,
                    "surface": surface,
                    "session_id": path.parent.name,
                    "kind": "sqlite_blobs",
                }


def _hit(
    *,
    target: Mapping[str, Any],
    line: int,
    text: str,
    needle: str,
) -> dict[str, Any]:
    return {
        "source_id": target["source_id"],
        "tool": target["tool"],
        "surface": target["surface"],
        "session_id": target["session_id"],
        "path": str(target["path"]),
        "line": line,
        "snippet": _snippet(text, needle),
    }


def _scan_text_file(
    target: Mapping[str, Any],
    needle: str,
    *,
    remaining: int,
) -> list[dict[str, Any]]:
    path = Path(target["path"])
    hits: list[dict[str, Any]] = []
    try:
        size = path.stat().st_size
    except OSError:
        return hits
    if size > MAX_FILE_BYTES:
        return hits
    needle_l = needle.lower()
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for lineno, line in enumerate(handle, start=1):
                if needle_l in line.lower():
                    hits.append(
                        _hit(target=target, line=lineno, text=line, needle=needle)
                    )
                    if len(hits) >= remaining:
                        break
    except OSError:
        return hits
    return hits


def _scan_sqlite_blobs(
    target: Mapping[str, Any],
    needle: str,
    *,
    remaining: int,
) -> list[dict[str, Any]]:
    path = Path(target["path"])
    hits: list[dict[str, Any]] = []
    needle_l = needle.lower()
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = con.execute("SELECT rowid, data FROM blobs").fetchall()
        except sqlite3.Error:
            return hits
        finally:
            con.close()
    except sqlite3.Error:
        return hits
    for rowid, data in rows:
        if not isinstance(data, (bytes, bytearray, memoryview)):
            continue
        blob = bytes(data)
        try:
            text = blob.decode("utf-8", errors="replace")
        except Exception:
            continue
        if needle_l not in text.lower():
            continue
        # Prefer JSON content field when present.
        display = text
        if blob.startswith(b"{"):
            try:
                row = json.loads(text)
                content = row.get("content")
                if isinstance(content, str):
                    display = content
                elif isinstance(content, list):
                    parts = []
                    for part in content:
                        if isinstance(part, dict) and part.get("text"):
                            parts.append(str(part["text"]))
                        elif isinstance(part, str):
                            parts.append(part)
                    if parts:
                        display = "\n".join(parts)
            except json.JSONDecodeError:
                pass
        hits.append(_hit(target=target, line=int(rowid), text=display, needle=needle))
        if len(hits) >= remaining:
            break
    return hits


def _python_search(
    targets: Sequence[Mapping[str, Any]],
    needle: str,
    limit: int,
) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for target in targets:
        remaining = limit - len(hits)
        if remaining <= 0:
            break
        kind = target.get("kind") or "text"
        if kind == "sqlite_blobs":
            batch = _scan_sqlite_blobs(target, needle, remaining=remaining)
        else:
            batch = _scan_text_file(target, needle, remaining=remaining)
        hits.extend(batch)
    return hits


def _rg_available() -> bool:
    return shutil.which("rg") is not None


def _rg_search(
    targets: Sequence[Mapping[str, Any]],
    needle: str,
    limit: int,
) -> Optional[list[dict[str, Any]]]:
    """Use ripgrep --json on text files. Returns None if rg fails."""
    text_targets = [t for t in targets if t.get("kind") != "sqlite_blobs"]
    if not text_targets:
        return []
    by_path = {str(Path(t["path"]).resolve()): t for t in text_targets}
    paths = list(by_path.keys())[:MAX_RG_FILES]
    if not paths:
        return []
    cmd = [
        "rg",
        "--json",
        "-i",
        "-F",
        "--max-count",
        "3",
        "--",
        needle,
        *paths,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    # Exit 0 = matches, 1 = no matches, 2 = error
    if proc.returncode not in (0, 1):
        return None
    hits: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        if len(hits) >= limit:
            break
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "match":
            continue
        data = event.get("data") or {}
        path_data = data.get("path") or {}
        path_text = path_data.get("text")
        if not path_text:
            continue
        try:
            key = str(Path(path_text).resolve())
        except OSError:
            key = path_text
        target = by_path.get(key) or by_path.get(path_text)
        if not target:
            # Match by string path
            for candidate in text_targets:
                if str(candidate["path"]) == path_text:
                    target = candidate
                    break
        if not target:
            continue
        lines = data.get("lines") or {}
        text = lines.get("text") or ""
        line_num = (data.get("line_number") or 0) or 0
        hits.append(_hit(target=target, line=int(line_num), text=text, needle=needle))
    return hits


def search_transcripts(
    q: str,
    *,
    sources: Optional[Sequence[str]] = None,
    limit: int = DEFAULT_LIMIT,
    env: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
    prefer_rg: bool = True,
) -> dict:
    """Search wave-1 transcript stores. Case-insensitive literal match."""
    query = (q or "").strip()
    if not query:
        raise ValueError("q must be a non-empty string.")
    if len(query) > 200:
        raise ValueError("q must be at most 200 characters.")
    if limit < 1:
        raise ValueError("limit must be >= 1")
    if limit > MAX_LIMIT:
        raise ValueError(f"limit must be <= {MAX_LIMIT}")

    resolved = resolve_sources(env=env, home=home)
    wanted = set(sources) if sources else None
    if wanted is not None:
        unknown = wanted - {s["id"] for s in resolved}
        if unknown:
            raise ValueError(f"Unknown source id(s): {', '.join(sorted(unknown))}")

    targets: list[dict[str, Any]] = []
    scanned: list[str] = []
    for source in resolved:
        sid = source["id"]
        if sid not in WAVE1_SEARCHABLE:
            continue
        if wanted is not None and sid not in wanted:
            continue
        scanned.append(sid)
        targets.extend(list(iter_text_targets(source)))

    engine = "python"
    hits: list[dict[str, Any]] = []
    sqlite_targets = [t for t in targets if t.get("kind") == "sqlite_blobs"]
    text_targets = [t for t in targets if t.get("kind") != "sqlite_blobs"]

    if prefer_rg and _rg_available() and text_targets:
        rg_hits = _rg_search(text_targets, query, limit)
        if rg_hits is not None:
            engine = "rg"
            hits.extend(rg_hits)

    if engine == "python":
        hits = _python_search(targets, query, limit)
    elif sqlite_targets and len(hits) < limit:
        # rg cannot search store.db; fill remaining from sqlite blobs.
        hits.extend(_python_search(sqlite_targets, query, limit - len(hits)))

    truncated = len(hits) >= limit
    hits = hits[:limit]
    return {
        "version": 1,
        "searchedAt": datetime.now(timezone.utc).isoformat(),
        "q": query,
        "sources": scanned,
        "engine": engine,
        "truncated": truncated,
        "count": len(hits),
        "hits": hits,
    }
