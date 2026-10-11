"""Detect AI discussion stores: existence, size, session estimates."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional


def directory_size(path: Path, *, max_entries: int = 250_000) -> int:
    """Best-effort recursive size. Caps entries so huge trees stay responsive."""
    if not path.exists():
        return 0
    if path.is_file() or path.is_symlink():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    seen = 0
    try:
        for root, dirs, files in os.walk(path, followlinks=False):
            # Skip common bulky caches that are not conversation payloads.
            dirs[:] = [
                name
                for name in dirs
                if name
                not in {
                    "Cache",
                    "Code Cache",
                    "GPUCache",
                    "DawnGraphiteCache",
                    "DawnWebGPUCache",
                    "WasmTtsEngine",
                    "WidevineCdm",
                    "component_crx_cache",
                    "Crashpad",
                    ".tmp",
                    "node_modules",
                }
            ]
            for name in files:
                seen += 1
                if seen > max_entries:
                    return total
                try:
                    total += (Path(root) / name).stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _count_dirs(path: Path, *, exclude: Iterable[str] = ()) -> int:
    if not path.is_dir():
        return 0
    skip = set(exclude)
    try:
        return sum(
            1 for p in path.iterdir() if p.is_dir() and p.name not in skip and not p.name.startswith(".")
        )
    except OSError:
        return 0


def _count_glob(path: Path, pattern: str) -> int:
    if not path.exists():
        return 0
    try:
        return sum(1 for p in path.glob(pattern) if p.is_file())
    except OSError:
        return 0


def _count_rglob(path: Path, pattern: str) -> int:
    if not path.exists():
        return 0
    try:
        return sum(1 for p in path.rglob(pattern) if p.is_file())
    except OSError:
        return 0


def estimate_sessions(source_id: str, path: Path) -> tuple[Optional[int], str]:
    """Return (session_estimate, extra_notes)."""
    if source_id.startswith("antigravity."):
        brains = _count_dirs(path / "brain", exclude=("tempmediaStorage",))
        convs = _count_glob(path / "conversations", "*.db")
        transcripts = _count_rglob(path / "brain", "transcript_full.jsonl")
        return max(brains, convs), (
            f"brains={brains} conversation_dbs={convs} transcript_full={transcripts}"
        )
    if source_id == "claude.code":
        projects = path / "projects"
        jsonl = _count_rglob(projects, "*.jsonl") if projects.is_dir() else 0
        proj_dirs = _count_dirs(projects) if projects.is_dir() else 0
        return jsonl, f"project_dirs={proj_dirs} session_jsonl={jsonl}"
    if source_id == "claude.desktop":
        local_agent = path / "local-agent-mode-sessions"
        agent_n = _count_dirs(local_agent) if local_agent.is_dir() else 0
        indexed = path / "IndexedDB" / "https_claude.ai_0.indexeddb.leveldb"
        note = f"local_agent_sessions≈{agent_n}"
        if indexed.exists():
            note += "; claude.ai IndexedDB cache present"
        return (agent_n or None), note
    if source_id == "codex.home":
        rollouts = _count_rglob(path / "sessions", "rollout-*.jsonl")
        return rollouts, f"rollout_jsonl={rollouts}"
    if source_id == "codex.app-support":
        return 0, "app shell; session rollouts expected under ~/.codex/sessions"
    if source_id == "cursor.cli":
        stores = _count_rglob(path, "store.db")
        return stores, f"store_db={stores}"
    if source_id == "cursor.ide":
        state = path / "User" / "globalStorage" / "state.vscdb"
        return (1 if state.is_file() else 0), (
            "state.vscdb present" if state.is_file() else "state.vscdb missing"
        )
    if source_id == "cursor.agent-transcripts":
        n = 0
        if path.is_dir():
            try:
                for transcripts in path.rglob("agent-transcripts"):
                    if transcripts.is_dir():
                        # Layout: agent-transcripts/<uuid>/<uuid>.jsonl
                        n += _count_rglob(transcripts, "*.jsonl")
            except OSError:
                pass
        return n, f"agent_transcript_jsonl={n}"
    return None, ""


def detect_one(source: dict) -> dict:
    path = Path(source["path"])
    exists = path.exists()
    size = directory_size(path) if exists else 0
    estimate = None
    extra = ""
    if exists:
        estimate, extra = estimate_sessions(source["id"], path)
    notes = source.get("notes") or ""
    if extra:
        notes = f"{notes}; {extra}" if notes else extra
    return {
        "id": source["id"],
        "tool": source["tool"],
        "surface": source["surface"],
        "path": str(path),
        "exists": exists,
        "kind": (
            "symlink"
            if exists and path.is_symlink()
            else "dir"
            if exists and path.is_dir()
            else "file"
            if exists
            else "missing"
        ),
        "size_bytes": size,
        "session_estimate": estimate,
        "notes": notes,
        "supports": {
            "sessions": source["id"]
            in {
                "antigravity.app",
                "antigravity.ide",
                "antigravity.cli",
                "claude.code",
                "codex.home",
                "cursor.cli",
                "cursor.agent-transcripts",
            },
            "usage": source["id"]
            in {
                "antigravity.app",
                "antigravity.ide",
                "antigravity.cli",
                "claude.code",
                "codex.home",
            },
            "search": source["id"]
            in {
                "antigravity.app",
                "antigravity.ide",
                "antigravity.cli",
                "claude.code",
                "codex.home",
                "cursor.cli",
                "cursor.agent-transcripts",
            },
            "export": source["id"]
            in {
                "antigravity.app",
                "antigravity.ide",
                "antigravity.cli",
                "claude.code",
                "codex.home",
                "cursor.cli",
                "cursor.agent-transcripts",
            },
        },
    }


def detect_all(sources: list[dict]) -> dict:
    detected_at = datetime.now(timezone.utc).isoformat()
    results = [detect_one(source) for source in sources]
    return {
        "version": 1,
        "detectedAt": detected_at,
        "sources": results,
        "summary": {
            "total": len(results),
            "found": sum(1 for item in results if item["exists"]),
            "sessions": sum(
                item["session_estimate"] or 0
                for item in results
                if item["exists"] and item["session_estimate"]
            ),
        },
    }


def load_detect_cache(path: Path) -> Optional[dict]:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("sources"), list):
        return None
    return raw


def save_detect_cache(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(path)
