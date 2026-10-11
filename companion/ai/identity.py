"""Canonical identity helpers for normalized AI history records."""

from __future__ import annotations


def conversation_key(
    tool: str,
    provider_id: str,
    *,
    source_id: str | None = None,
    confidence: str = "provider",
) -> str:
    """Return a stable conversation key without merging uncertain stores."""
    tool = str(tool).strip()
    provider_id = str(provider_id).strip()
    if confidence == "provider":
        return f"{tool}:conversation:{provider_id}"
    if not source_id:
        raise ValueError("source_id is required for non-provider conversation identity")
    return f"{tool}:conversation:{source_id}:{provider_id}"


def execution_key(source_id: str, execution_id: str) -> str:
    """Return a source-scoped execution key."""
    return f"{str(source_id).strip()}:execution:{str(execution_id).strip()}"
