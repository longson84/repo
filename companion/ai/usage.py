"""Aggregate token usage and cost estimates across wave-1 AI stores."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from .adapters import antigravity, claude_code, codex
from .catalog import resolve_sources
from . import prices as prices_mod
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
    "Estimates use retail API rates on measured local tokens — not subscription invoices.",
    "Per-call usage is summed; cache storage fees are omitted.",
    "Cursor CLI / agent transcripts / IDE do not expose structured usage yet.",
    "Edit .repo-local/ai/prices.json to override rates.",
]


def _empty_bucket() -> dict[str, Any]:
    return {
        "input_tokens": 0,
        "cached_tokens": 0,
        "output_tokens": 0,
        "cache_write_tokens": 0,
        "cost_usd": 0.0,
        "events": 0,
    }


def _empty_token_coverage() -> dict[str, int]:
    return {
        "input_tokens": 0,
        "cached_tokens": 0,
        "output_tokens": 0,
        "cache_write_tokens": 0,
        "total": 0,
    }


def _add_token_coverage(bucket: dict[str, int], event: Mapping[str, Any]) -> None:
    for field in (
        "input_tokens",
        "cached_tokens",
        "output_tokens",
        "cache_write_tokens",
    ):
        value = int(event.get(field) or 0)
        bucket[field] += value
        bucket["total"] += value


def _add(bucket: dict[str, Any], event: Mapping[str, Any], cost: float) -> None:
    bucket["input_tokens"] += int(event.get("input_tokens") or 0)
    bucket["cached_tokens"] += int(event.get("cached_tokens") or 0)
    bucket["output_tokens"] += int(event.get("output_tokens") or 0)
    bucket["cache_write_tokens"] += int(event.get("cache_write_tokens") or 0)
    bucket["cost_usd"] += cost
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


def _sorted_cost(rows: dict[str, dict[str, Any]], key: str) -> list[dict[str, Any]]:
    items = list(rows.values())
    items.sort(
        key=lambda row: (-float(row.get("cost_usd") or 0), str(row.get(key) or ""))
    )
    for row in items:
        row["cost_usd"] = round(float(row["cost_usd"]), 6)
    return items


def _period_bucket(store: dict[str, dict[str, Any]], period: str) -> dict[str, Any]:
    bucket = store.get(period)
    if bucket is None:
        bucket = {
            **_empty_bucket(),
            "period": period,
            "_models": {},
            "_projects": {},
        }
        store[period] = bucket
    return bucket


def _serialize_periods(store: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for period in sorted(store.keys(), reverse=True):
        bucket = store[period]
        models = _sorted_cost(bucket.get("_models") or {}, "model")
        projects = _sorted_cost(bucket.get("_projects") or {}, "project")
        rows.append(
            {
                "period": period,
                "date": period,  # backward-compatible alias for day rows
                "input_tokens": bucket["input_tokens"],
                "cached_tokens": bucket["cached_tokens"],
                "output_tokens": bucket["output_tokens"],
                "cache_write_tokens": bucket["cache_write_tokens"],
                "cost_usd": round(float(bucket["cost_usd"]), 6),
                "events": bucket["events"],
                "by_model": models,
                "by_project": projects,
            }
        )
    return rows


def _finalize_model_pricing(store: Mapping[str, dict[str, Any]]) -> None:
    for bucket in store.values():
        statuses = bucket.pop("_pricing_statuses", set())
        bucket["pricing_status"] = (
            next(iter(statuses)) if len(statuses) == 1 else "partial"
        )


def aggregate_usage(
    *,
    sources: Optional[Sequence[str]] = None,
    session_ids: Optional[Sequence[str]] = None,
    conversation_keys: Optional[Sequence[str]] = None,
    compound_sessions: Optional[Sequence[Mapping[str, str]]] = None,
    prices_path: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
    cache_path: Optional[Path] = None,
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

    price_file = Path(prices_path) if prices_path else None
    table = (
        prices_mod.ensure_prices(price_file)
        if price_file is not None
        else prices_mod.DEFAULT_PRICES
    )

    totals = _empty_bucket()
    by_model: dict[str, dict[str, Any]] = {}
    by_project: dict[str, dict[str, Any]] = {}
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
    priced_tokens = _empty_token_coverage()
    unpriced_tokens = _empty_token_coverage()
    pricing_status_counts: dict[str, int] = {}

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
            unique_events[event_key] = event

    for event in unique_events.values():
        pricing = prices_mod.price_usage(event, table)
        cost = float(pricing["cost_usd"])
        pricing_status = str(pricing["status"])
        pricing_status_counts[pricing_status] = (
            pricing_status_counts.get(pricing_status, 0) + 1
        )
        if pricing_status == "priced":
            _add_token_coverage(priced_tokens, event)
        else:
            _add_token_coverage(unpriced_tokens, event)
        _add(totals, event, cost)

        model_key = str(event.get("model") or "unknown")
        provider_key = str(event.get("provider") or "unknown")
        model_group_key = f"{provider_key}\0{model_key}"
        model_bucket = by_model.setdefault(
            model_group_key,
            {
                **_empty_bucket(),
                "provider": provider_key,
                "model": model_key,
                "_pricing_statuses": set(),
            },
        )
        _add(model_bucket, event, cost)
        model_bucket["_pricing_statuses"].add(pricing_status)

        project_key = str(event.get("project") or "unknown")
        project_bucket = by_project.setdefault(
            project_key, {**_empty_bucket(), "project": project_key}
        )
        _add(project_bucket, event, cost)

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
        _add(surface_bucket, event, cost)

        tool_key = str(event.get("tool") or "unknown")
        tool_bucket = by_tool.setdefault(
            tool_key, {**_empty_bucket(), "tool": tool_key}
        )
        _add(tool_bucket, event, cost)

        src_key = str(event.get("source_id") or "unknown")
        src_bucket = by_source.setdefault(
            src_key, {**_empty_bucket(), "source_id": src_key}
        )
        _add(src_bucket, event, cost)

        keys = period_keys(event.get("date"))
        if keys is None:
            continue
        for grain, period in keys.items():
            bucket = _period_bucket(period_stores[grain], period)
            _add(bucket, event, cost)
            models = bucket["_models"]
            mb = models.setdefault(
                model_group_key,
                {
                    **_empty_bucket(),
                    "provider": provider_key,
                    "model": model_key,
                    "_pricing_statuses": set(),
                },
            )
            _add(mb, event, cost)
            mb["_pricing_statuses"].add(pricing_status)
            projects = bucket["_projects"]
            pb = projects.setdefault(
                project_key, {**_empty_bucket(), "project": project_key}
            )
            _add(pb, event, cost)

    totals["cost_usd"] = round(float(totals["cost_usd"]), 6)
    _finalize_model_pricing(by_model)
    for period_store in period_stores.values():
        for period_bucket in period_store.values():
            _finalize_model_pricing(period_bucket["_models"])
    by_day_rows = _serialize_periods(by_day)
    by_week_rows = _serialize_periods(by_week)
    by_month_rows = _serialize_periods(by_month)
    by_year_rows = _serialize_periods(by_year)
    reconciliation_counts = {
        status: sum(1 for row in reconciliation if row.get("status") == status)
        for status in ("matched", "different", "unavailable")
    }
    priced_events = pricing_status_counts.get("priced", 0)
    total_events = len(unique_events)
    unpriced_events = total_events - priced_events
    total_token_count = priced_tokens["total"] + unpriced_tokens["total"]
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
    pricing_coverage = {
        "priced_events": priced_events,
        "unpriced_events": unpriced_events,
        "total_events": total_events,
        "event_percentage": round(
            100.0 if total_events == 0 else priced_events * 100.0 / total_events, 2
        ),
        "priced_tokens": priced_tokens,
        "unpriced_tokens": unpriced_tokens,
        "total_tokens": {
            field: priced_tokens[field] + unpriced_tokens[field]
            for field in priced_tokens
        },
        "token_percentage": round(
            100.0
            if total_token_count == 0
            else priced_tokens["total"] * 100.0 / total_token_count,
            2,
        ),
        "statuses": pricing_status_counts,
    }

    return {
        "version": 1,
        "aggregatedAt": datetime.now(timezone.utc).isoformat(),
        "sources": scanned,
        "usage_supported": sorted(WAVE1_USAGE_SUPPORTED),
        "usage_unsupported": unsupported_notes,
        "caveats": CAVEATS,
        "quality": {
            "warnings": warnings,
            "warning_count": len(warnings),
            "reconciliation": reconciliation_counts,
        },
        "reconciliation": reconciliation,
        "cache": cache_metadata,
        "pricing_coverage": pricing_coverage,
        "prices": {
            "currency": table.get("currency") or "USD",
            "notes": table.get("notes") or "",
            "path": str(price_file) if price_file else None,
        },
        "totals": totals,
        "conversation_count": conversation_count,
        "execution_count": execution_count,
        "event_count": len(unique_events),
        "by_date": by_day_rows,
        "by_day": by_day_rows,
        "by_week": by_week_rows,
        "by_month": by_month_rows,
        "by_year": by_year_rows,
        "by_model": _sorted_cost(by_model, "model"),
        "by_project": _sorted_cost(by_project, "project"),
        "by_surface": _sorted_cost(by_surface, "label"),
        "by_tool": _sorted_cost(by_tool, "tool"),
        "by_source": _sorted_cost(by_source, "source_id"),
    }
