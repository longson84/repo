"""Exact, effective-dated retail API pricing for normalized token usage."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional

DEFAULT_PRICES: dict[str, Any] = {
    "version": 2,
    "currency": "USD",
    "per_million": {
        "gemini-3-flash": {"input": 0.75, "cached": 0.075, "output": 3.75},
        "gemini-3-pro-high": {"input": 2.0, "cached": 0.2, "output": 12.0},
        "claude-sonnet-4-6": {"input": 3.0, "cached": 0.30, "output": 15.0, "cache_write": 3.75},
        "claude-sonnet-4-5": {"input": 3.0, "cached": 0.30, "output": 15.0, "cache_write": 3.75},
        "claude-sonnet": {"input": 3.0, "cached": 0.30, "output": 15.0, "cache_write": 3.75},
        "claude-opus-4": {"input": 15.0, "cached": 1.50, "output": 75.0, "cache_write": 18.75},
        "claude-opus": {"input": 15.0, "cached": 1.50, "output": 75.0, "cache_write": 18.75},
        "claude-haiku": {"input": 1.0, "cached": 0.10, "output": 5.0, "cache_write": 1.25},
        "claude-fable": {"input": 5.0, "cached": 0.50, "output": 25.0, "cache_write": 6.25},
        "gpt-5.5": {"input": 1.25, "cached": 0.125, "output": 10.0},
        "gpt-5": {"input": 1.25, "cached": 0.125, "output": 10.0},
        "gpt-6": {"input": 1.75, "cached": 0.175, "output": 14.0},
        "o3": {"input": 2.0, "cached": 0.5, "output": 8.0},
    },
    "rates": [],
    "aliases": {},
    "notes": (
        "Retail API estimates from measured local tokens. Not subscription invoices; "
        "cache storage fees omitted. Only exact models and explicit aliases are priced."
    ),
}

RATE_FIELDS = ("input", "cached", "output", "cache_write")


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _name(value: Optional[str]) -> str:
    return str(value or "").strip().lower()


def _parse_day(value: Any) -> Optional[date]:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("date must be an ISO string")
    return date.fromisoformat(value[:10])


def _valid_rates(rate: Mapping[str, Any]) -> bool:
    present = False
    for field in RATE_FIELDS:
        if field not in rate:
            continue
        present = True
        value = rate[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            return False
    return present


def _alias_target(provider: str, model: str, table: Mapping[str, Any]) -> tuple[str, str]:
    aliases = table.get("aliases")
    if not isinstance(aliases, Mapping):
        return provider, model
    target = aliases.get(f"{provider}:{model}")
    if target is None:
        target = aliases.get(model)
    if isinstance(target, str):
        return provider, _name(target)
    if isinstance(target, Mapping):
        return _name(target.get("provider")) or provider, _name(target.get("model"))
    return provider, model


def resolve_rate(
    *, provider: str, model: str, event_date: str | None, table: Mapping[str, Any]
) -> tuple[Mapping[str, float] | None, str]:
    """Resolve one explicit exact/alias rate; never infer a family or default."""
    provider_key, model_key = _alias_target(_name(provider), _name(model), table)
    if not model_key or model_key == "unknown":
        return None, "unpriced_model"

    candidates: list[Mapping[str, Any]] = []
    rates = table.get("rates")
    if isinstance(rates, list):
        candidates.extend(
            rate
            for rate in rates
            if isinstance(rate, Mapping)
            and _name(rate.get("provider")) == provider_key
            and _name(rate.get("model")) == model_key
        )
    legacy = table.get("per_million")
    if isinstance(legacy, Mapping):
        rate = legacy.get(model_key)
        if model_key != "default" and isinstance(rate, Mapping):
            candidates.append({"model": model_key, **rate})
    if not candidates:
        return None, "unpriced_model"

    try:
        day = _parse_day(event_date)
    except ValueError:
        day = None
    outside = False
    for candidate in candidates:
        try:
            start = _parse_day(candidate.get("effective_from"))
            end = _parse_day(candidate.get("effective_to"))
        except ValueError:
            return None, "invalid_rate"
        if start and end and start > end:
            return None, "invalid_rate"
        if day is not None and ((start and day < start) or (end and day > end)):
            outside = True
            continue
        if day is None and (start or end):
            outside = True
            continue
        if not _valid_rates(candidate):
            return None, "invalid_rate"
        return candidate, "priced"
    return None, "outside_rate_period" if outside else "unpriced_model"


def price_usage(usage: Mapping[str, Any], table: Mapping[str, Any]) -> dict[str, Any]:
    rate, status = resolve_rate(
        provider=str(usage.get("provider") or ""),
        model=str(usage.get("model") or ""),
        event_date=usage.get("date"),
        table=table,
    )
    if rate is None:
        return {"cost_usd": 0.0, "status": status, "matched_rate": None}
    input_rate = float(rate.get("input") or 0)
    write_rate = rate.get("cache_write")
    if write_rate is None:
        write_rate = input_rate * 1.25
    cost = (
        int(usage.get("input_tokens") or 0) * input_rate
        + int(usage.get("cached_tokens") or 0) * float(rate.get("cached") or 0)
        + int(usage.get("output_tokens") or 0) * float(rate.get("output") or 0)
        + int(usage.get("cache_write_tokens") or 0) * float(write_rate)
    ) / 1_000_000.0
    return {"cost_usd": cost, "status": "priced", "matched_rate": dict(rate)}


def rates_for(model: Optional[str], table: Mapping[str, Any]) -> Mapping[str, float]:
    """Compatibility helper returning only an exact legacy rate."""
    rate, _ = resolve_rate(provider="", model=str(model or ""), event_date=None, table=table)
    return rate or {}


def estimate_cost(usage: Mapping[str, Any], table: Mapping[str, Any]) -> float:
    return float(price_usage(usage, table)["cost_usd"])


def load_prices(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        return _copy(DEFAULT_PRICES)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _copy(DEFAULT_PRICES)
    if not isinstance(data, dict):
        return _copy(DEFAULT_PRICES)
    merged = _copy(DEFAULT_PRICES)
    for key in ("currency", "notes", "version"):
        if key in data:
            merged[key] = data[key]
    for key in ("per_million", "aliases"):
        if isinstance(data.get(key), dict):
            merged[key].update(data[key])
    if isinstance(data.get("rates"), list):
        merged["rates"] = data["rates"]
    return merged


def save_prices(path: Path, data: Mapping[str, Any]) -> dict:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": data.get("version") or 2,
        "currency": data.get("currency") or "USD",
        "per_million": data.get("per_million") or {},
        "rates": data.get("rates") or [],
        "aliases": data.get("aliases") or {},
        "notes": data.get("notes") or DEFAULT_PRICES["notes"],
    }
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return dict(payload)


def ensure_prices(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        return save_prices(path, DEFAULT_PRICES)
    return load_prices(path)


def update_prices(path: Path, patch: Mapping[str, Any]) -> dict:
    current: MutableMapping[str, Any] = ensure_prices(path)
    for key in ("currency", "notes"):
        if isinstance(patch.get(key), str):
            current[key] = patch[key]
    for key in ("per_million", "aliases"):
        if isinstance(patch.get(key), Mapping):
            current.setdefault(key, {}).update(patch[key])
    if isinstance(patch.get("rates"), list):
        current["rates"] = patch["rates"]
    current["version"] = 2
    return save_prices(path, current)
