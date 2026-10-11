"""Aggregate token usage across wave-1 AI stores."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from .adapters import antigravity, claude_code, codex
from .catalog import resolve_sources
from .sessions import collect_sessions, resolve_conversation_selectors
from .usage_cache import refresh_usage_cache

WAVE1_USAGE_SUPPORTED = {
    "antigravity.app",
    "antigravity.ide",
    "antigravity.cli",
    "claude.code",
    "codex.home",
}

WAVE1_USAGE_UNSUPPORTED = {
    "cursor.cli": "No structured token fields in CLI store.db",
    "cursor.agent-transcripts": "Transcript JSONL has no usage counters",
    "cursor.ide": "IDE state.vscdb is not a usage ledger (wave 2)",
    "claude.desktop": "Wave 2 — Desktop store layout differs",
}

CAVEATS = [
    "Per-call token usage is summed from counters exposed by each local store.",
    "Cursor CLI / agent transcripts / IDE do not expose structured usage yet.",
]


def _empty_bucket() -> dict[str, Any]:
    return {
        "input_tokens": 0,
        "cached_tokens": 0,
        "output_tokens": 0,
        "cache_write_tokens": 0,
        "events": 0,
    }


def _add(bucket: dict[str, Any], event: Mapping[str, Any]) -> None:
    bucket["input_tokens"] += int(event.get("input_tokens") or 0)
    bucket["cached_tokens"] += int(event.get("cached_tokens") or 0)
    bucket["output_tokens"] += int(event.get("output_tokens") or 0)
    bucket["cache_write_tokens"] += int(event.get("cache_write_tokens") or 0)
    bucket["events"] += 1


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value or value == "unknown":
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _iso_week_key(day: date) -> str:
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def period_keys(date_str: Optional[str]) -> Optional[dict[str, str]]:
    """Return day/week/month/year keys for a YYYY-MM-DD string."""
    day = _parse_date(date_str)
    if day is None:
        return None
    return {
        "day": day.isoformat(),
        "week": _iso_week_key(day),
        "month": f"{day.year:04d}-{day.month:02d}",
        "year": f"{day.year:04d}",
    }


def _collect_source_usage(source: Mapping[str, Any]) -> dict[str, Any]:
    source_id = source["id"]
    path = Path(source["path"])
    if not path.exists():
        return {"events": [], "reconciliation": [], "warnings": []}
    if source_id.startswith("antigravity."):
        return antigravity.collect_usage(path, source_id, source["surface"])
    if source_id == "claude.code":
        return claude_code.collect_usage(path, source_id)
    if source_id == "codex.home":
        return codex.collect_usage(path, source_id)
    return {"events": [], "reconciliation": [], "warnings": []}


def _usage_descriptors(source: Mapping[str, Any]) -> list[dict[str, Any]]:
    source_id = source["id"]
    path = Path(source["path"])
    if not path.exists():
        return []
    if source_id.startswith("antigravity."):
        return antigravity.usage_files(path, source_id, source["surface"])
    if source_id == "claude.code":
        return claude_code.usage_files(path, source_id)
    if source_id == "codex.home":
        return codex.usage_files(path, source_id)
    return []


def _sorted_usage(rows: dict[str, dict[str, Any]], key: str) -> list[dict[str, Any]]:
    items = list(rows.values())
    items.sort(
        key=lambda row: (
            -sum(
                int(row.get(field) or 0)
                for field in (
                    "input_tokens",
                    "cached_tokens",
                    "output_tokens",
                    "cache_write_tokens",
                )
            ),
            str(row.get(key) or ""),
        )
    )
    return items


def _period_bucket(store: dict[str, dict[str, Any]], period: str) -> dict[str, Any]:
    bucket = store.get(period)
    if bucket is None:
        bucket = {
            **_empty_bucket(),
            "period": period,
            "_models": {},
            "_projects": {},
            "_repositories": {},
            "_workspaces": {},
        }
        store[period] = bucket
    return bucket


def _serialize_periods(store: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for period in sorted(store.keys(), reverse=True):
        bucket = store[period]
        models = {}
        for model_group_key, model_bucket in (bucket.get("_models") or {}).items():
            models[model_group_key] = {
                **{
                    key: value
                    for key, value in model_bucket.items()
                    if key != "_projects"
                },
                "by_project": _sorted_usage(
                    model_bucket.get("_projects") or {}, "project_name"
                ),
            }
        projects = {}
        for project_key, project_bucket in (bucket.get("_projects") or {}).items():
            projects[project_key] = {
                **{
                    key: value
                    for key, value in project_bucket.items()
                    if key != "_models"
                },
                "by_model": _sorted_usage(project_bucket.get("_models") or {}, "model"),
            }
        repositories = _sorted_usage(
            bucket.get("_repositories") or {}, "repository_name"
        )
        workspaces = _sorted_usage(
            bucket.get("_workspaces") or {}, "workspace_name"
        )
        rows.append(
            {
                "period": period,
                "date": period,  # backward-compatible alias for day rows
                "input_tokens": bucket["input_tokens"],
                "cached_tokens": bucket["cached_tokens"],
                "output_tokens": bucket["output_tokens"],
                "cache_write_tokens": bucket["cache_write_tokens"],
                "events": bucket["events"],
                "by_model": _sorted_usage(models, "model"),
                "by_project": _sorted_usage(projects, "project_name"),
                "by_repository": repositories,
                "by_workspace": workspaces,
            }
        )
    return rows


def _unattributed_identity(code: str) -> dict[str, Any]:
    return {
        "workspace_id": "unattributed",
        "workspace_name": "Unattributed",
        "repository_id": "unattributed",
        "repository_name": "Unattributed",
        "project_id": "unattributed",
        "project_name": "Unattributed",
        "identity_confidence": "unattributed",
        "identity_warnings": [code],
    }


def _legacy_identity(event: Mapping[str, Any]) -> dict[str, Any]:
    """Compatibility for direct callers; the server always supplies the resolver."""
    project_name = str(event.get("project") or "unknown")
    cwd = str(event.get("cwd") or "")
    key = project_name.casefold() or "unknown"
    return {
        "workspace_id": cwd or f"legacy:{key}",
        "workspace_name": Path(cwd).name if cwd else project_name,
        "repository_id": f"legacy:{key}",
        "repository_name": project_name,
        "project_id": f"legacy:{key}",
        "project_name": project_name,
        "identity_confidence": "path",
        "identity_warnings": [],
    }


def aggregate_usage(
    *,
    sources: Optional[Sequence[str]] = None,
    session_ids: Optional[Sequence[str]] = None,
    conversation_keys: Optional[Sequence[str]] = None,
    compound_sessions: Optional[Sequence[Mapping[str, str]]] = None,
    env: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
    cache_path: Optional[Path] = None,
    identity_resolver: Optional[Any] = None,
) -> dict:
    """Aggregate usage by date / model / surface / tool and period grains."""
    resolved = resolve_sources(env=env, home=home)
    wanted = set(sources) if sources else None
    if wanted is not None:
        unknown = wanted - {s["id"] for s in resolved}
        if unknown:
            raise ValueError(f"Unknown source id(s): {', '.join(sorted(unknown))}")

    session_filter = set(session_ids) if session_ids else None
    if session_filter is not None:
        for item in session_filter:
            if not isinstance(item, str):
                raise ValueError("session_ids must be strings.")
    if conversation_keys is not None:
        for item in conversation_keys:
            if not isinstance(item, str):
                raise ValueError("conversation_keys must be strings.")
    if compound_sessions is not None:
        for item in compound_sessions:
            if not isinstance(item, Mapping):
                raise ValueError("sessions must contain objects.")
            if not isinstance(item.get("source_id"), str) or not isinstance(
                item.get("session_id"), str
            ):
                raise ValueError("sessions require string source_id and session_id.")

    canonical_filter = None
    if conversation_keys or compound_sessions or session_ids:
        inventory = collect_sessions(sources=sources, env=env, home=home)
        canonical_filter = resolve_conversation_selectors(
            inventory,
            conversation_keys=conversation_keys,
            compound_sessions=compound_sessions,
            legacy_session_ids=session_ids,
        )

    totals = _empty_bucket()
    by_model: dict[str, dict[str, Any]] = {}
    by_project: dict[str, dict[str, Any]] = {}
    by_repository: dict[str, dict[str, Any]] = {}
    by_workspace: dict[str, dict[str, Any]] = {}
    by_surface: dict[str, dict[str, Any]] = {}
    by_tool: dict[str, dict[str, Any]] = {}
    by_source: dict[str, dict[str, Any]] = {}
    by_day: dict[str, dict[str, Any]] = {}
    by_week: dict[str, dict[str, Any]] = {}
    by_month: dict[str, dict[str, Any]] = {}
    by_year: dict[str, dict[str, Any]] = {}
    scanned: list[str] = []
    unsupported_notes: list[dict[str, str]] = []
    warnings: list[dict[str, Any]] = []
    reconciliation: list[dict[str, Any]] = []
    unique_events: dict[str, dict[str, Any]] = {}
    identity_warnings: list[dict[str, Any]] = []
    identity_warning_keys: set[tuple[str, str]] = set()
    attribution = {"git": 0, "configured": 0, "path": 0, "unattributed": 0}
    period_stores = {
        "day": by_day,
        "week": by_week,
        "month": by_month,
        "year": by_year,
    }

    selected_supported = [
        source
        for source in resolved
        if source["id"] in WAVE1_USAGE_SUPPORTED
        and (wanted is None or source["id"] in wanted)
    ]
    cached_batch = None
    cache_metadata = {
        "status": "disabled",
        "hits": 0,
        "misses": 0,
        "reparsed": 0,
        "deleted": 0,
    }
    if cache_path is not None:
        descriptors = [
            descriptor
            for source in selected_supported
            for descriptor in _usage_descriptors(source)
        ]
        cached_batch = refresh_usage_cache(
            Path(cache_path),
            descriptors,
            source_ids=[source["id"] for source in selected_supported],
        )
        cache_metadata = cached_batch["cache"]
    cached_consumed = False

    for source in resolved:
        sid = source["id"]
        if wanted is not None and sid not in wanted:
            continue
        if sid in WAVE1_USAGE_UNSUPPORTED:
            if wanted is None or sid in wanted:
                unsupported_notes.append(
                    {"source_id": sid, "reason": WAVE1_USAGE_UNSUPPORTED[sid]}
                )
            continue
        if sid not in WAVE1_USAGE_SUPPORTED:
            continue
        scanned.append(sid)
        if cached_batch is not None:
            if cached_consumed:
                continue
            batch = cached_batch
            cached_consumed = True
        else:
            batch = _collect_source_usage(source)
        warnings.extend(batch.get("warnings") or [])
        reconciliation.extend(
            row
            for row in (batch.get("reconciliation") or [])
            if canonical_filter is None
            or row.get("conversation_key") in canonical_filter
        )
        for index, event in enumerate(batch.get("events") or []):
            if (
                canonical_filter is not None
                and event.get("conversation_key") not in canonical_filter
            ):
                continue
            event_key = str(
                event.get("event_key")
                or f"{sid}:{event.get('session_id')}:{event.get('date')}:{index}"
            )
            if event_key in unique_events:
                previous = unique_events[event_key]
                for field in (
                    "input_tokens",
                    "cached_tokens",
                    "output_tokens",
                    "cache_write_tokens",
                ):
                    previous[field] = max(
                        int(previous.get(field) or 0), int(event.get(field) or 0)
                    )
                continue
            unique_events[event_key] = dict(event)

    resolved_cwds: dict[str, dict[str, Any]] = {}
    for event in unique_events.values():
        cwd = event.get("cwd")
        if identity_resolver is None:
            identity = _legacy_identity(event)
        elif not isinstance(cwd, str) or not cwd.strip():
            identity = _unattributed_identity("workspace_unavailable")
        else:
            cwd_key = cwd.strip()
            identity = resolved_cwds.get(cwd_key)
            if identity is None:
                try:
                    identity = identity_resolver.resolve(cwd_key)
                except (OSError, TypeError, ValueError):
                    identity = _unattributed_identity("identity_resolution_failed")
                resolved_cwds[cwd_key] = identity
        event.update(identity)
        event["project"] = identity["project_name"]
        confidence = str(identity.get("identity_confidence") or "unattributed")
        if confidence not in attribution:
            confidence = "unattributed"
        attribution[confidence] += 1
        for code in identity.get("identity_warnings") or []:
            warning_key = (str(code), str(identity.get("workspace_id") or ""))
            if warning_key in identity_warning_keys:
                continue
            identity_warning_keys.add(warning_key)
            identity_warnings.append(
                {
                    "code": warning_key[0],
                    "workspace_id": warning_key[1],
                    "project_id": identity.get("project_id"),
                    "repository_id": identity.get("repository_id"),
                }
            )

    for event in unique_events.values():
        _add(totals, event)

        model_key = str(event.get("model") or "unknown")
        provider_key = str(event.get("provider") or "unknown")
        model_group_key = f"{provider_key}\0{model_key}"
        model_bucket = by_model.setdefault(
            model_group_key,
            {
                **_empty_bucket(),
                "provider": provider_key,
                "model": model_key,
            },
        )
        _add(model_bucket, event)

        project_key = str(event.get("project_id") or "unattributed")
        project_bucket = by_project.setdefault(
            project_key,
            {
                **_empty_bucket(),
                "project_id": project_key,
                "project_name": event.get("project_name") or "Unattributed",
                "project": event.get("project_name") or "Unattributed",
            },
        )
        _add(project_bucket, event)

        repository_key = str(event.get("repository_id") or "unattributed")
        repository_bucket = by_repository.setdefault(
            repository_key,
            {
                **_empty_bucket(),
                "repository_id": repository_key,
                "repository_name": event.get("repository_name") or "Unattributed",
            },
        )
        _add(repository_bucket, event)

        workspace_key = str(event.get("workspace_id") or "unattributed")
        workspace_bucket = by_workspace.setdefault(
            workspace_key,
            {
                **_empty_bucket(),
                "workspace_id": workspace_key,
                "workspace_name": event.get("workspace_name") or "Unattributed",
                "project_id": project_key,
                "project_name": event.get("project_name") or "Unattributed",
                "repository_id": repository_key,
                "repository_name": event.get("repository_name") or "Unattributed",
                "identity_confidence": event.get("identity_confidence")
                or "unattributed",
            },
        )
        _add(workspace_bucket, event)

        surface_key = f"{event.get('tool')}:{event.get('surface')}"
        surface_bucket = by_surface.setdefault(
            surface_key,
            {
                **_empty_bucket(),
                "tool": event.get("tool"),
                "surface": event.get("surface"),
                "label": surface_key,
            },
        )
        _add(surface_bucket, event)

        tool_key = str(event.get("tool") or "unknown")
        tool_bucket = by_tool.setdefault(
            tool_key, {**_empty_bucket(), "tool": tool_key}
        )
        _add(tool_bucket, event)

        src_key = str(event.get("source_id") or "unknown")
        src_bucket = by_source.setdefault(
            src_key, {**_empty_bucket(), "source_id": src_key}
        )
        _add(src_bucket, event)

        keys = period_keys(event.get("date"))
        if keys is None:
            continue
        for grain, period in keys.items():
            bucket = _period_bucket(period_stores[grain], period)
            _add(bucket, event)
            models = bucket["_models"]
            mb = models.setdefault(
                model_group_key,
                {
                    **_empty_bucket(),
                    "provider": provider_key,
                    "model": model_key,
                    "_projects": {},
                },
            )
            _add(mb, event)
            model_projects = mb["_projects"]
            model_project = model_projects.setdefault(
                project_key,
                {
                    **_empty_bucket(),
                    "project_id": project_key,
                    "project_name": event.get("project_name") or "Unattributed",
                    "project": event.get("project_name") or "Unattributed",
                },
            )
            _add(model_project, event)
            projects = bucket["_projects"]
            pb = projects.setdefault(
                project_key,
                {
                    **_empty_bucket(),
                    "project_id": project_key,
                    "project_name": event.get("project_name") or "Unattributed",
                    "project": event.get("project_name") or "Unattributed",
                    "_models": {},
                },
            )
            _add(pb, event)
            project_models = pb["_models"]
            project_model = project_models.setdefault(
                model_group_key,
                {
                    **_empty_bucket(),
                    "provider": provider_key,
                    "model": model_key,
                },
            )
            _add(project_model, event)
            period_repositories = bucket["_repositories"]
            period_repository = period_repositories.setdefault(
                repository_key,
                {
                    **_empty_bucket(),
                    "repository_id": repository_key,
                    "repository_name": event.get("repository_name")
                    or "Unattributed",
                },
            )
            _add(period_repository, event)
            period_workspaces = bucket["_workspaces"]
            period_workspace = period_workspaces.setdefault(
                workspace_key,
                {
                    **_empty_bucket(),
                    "workspace_id": workspace_key,
                    "workspace_name": event.get("workspace_name") or "Unattributed",
                    "project_id": project_key,
                    "project_name": event.get("project_name") or "Unattributed",
                    "repository_id": repository_key,
                    "repository_name": event.get("repository_name")
                    or "Unattributed",
                    "identity_confidence": event.get("identity_confidence")
                    or "unattributed",
                },
            )
            _add(period_workspace, event)

    by_day_rows = _serialize_periods(by_day)
    by_week_rows = _serialize_periods(by_week)
    by_month_rows = _serialize_periods(by_month)
    by_year_rows = _serialize_periods(by_year)
    reconciliation_counts = {
        status: sum(1 for row in reconciliation if row.get("status") == status)
        for status in ("matched", "different", "unavailable")
    }
    conversation_count = len(
        {
            str(event["conversation_key"])
            for event in unique_events.values()
            if event.get("conversation_key")
        }
    )
    execution_count = len(
        {
            str(event["execution_key"])
            for event in unique_events.values()
            if event.get("execution_key")
        }
    )
    return {
        "version": 2,
        "aggregatedAt": datetime.now(timezone.utc).isoformat(),
        "sources": scanned,
        "usage_supported": sorted(WAVE1_USAGE_SUPPORTED),
        "usage_unsupported": unsupported_notes,
        "caveats": CAVEATS,
        "quality": {
            "warnings": [*warnings, *identity_warnings],
            "warning_count": len(warnings) + len(identity_warnings),
            "reconciliation": reconciliation_counts,
            "attribution": attribution,
            "identity_warnings": identity_warnings,
        },
        "reconciliation": reconciliation,
        "cache": cache_metadata,
        "totals": totals,
        "conversation_count": conversation_count,
        "execution_count": execution_count,
        "event_count": len(unique_events),
        "by_date": by_day_rows,
        "by_day": by_day_rows,
        "by_week": by_week_rows,
        "by_month": by_month_rows,
        "by_year": by_year_rows,
        "by_model": _sorted_usage(by_model, "model"),
        "by_project": _sorted_usage(by_project, "project_name"),
        "by_repository": _sorted_usage(by_repository, "repository_name"),
        "by_workspace": _sorted_usage(by_workspace, "workspace_name"),
        "by_surface": _sorted_usage(by_surface, "label"),
        "by_tool": _sorted_usage(by_tool, "tool"),
        "by_source": _sorted_usage(by_source, "source_id"),
    }
