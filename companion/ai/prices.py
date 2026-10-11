"""Local retail API price table for token cost estimates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional

DEFAULT_PRICES: dict[str, Any] = {
    "currency": "USD",
    "per_million": {
        "gemini-3-flash": {"input": 0.75, "cached": 0.075, "output": 3.75},
        "gemini-3-pro-high": {"input": 2.0, "cached": 0.2, "output": 12.0},
        "claude-sonnet-4-6": {
            "input": 3.0,
            "cached": 0.30,
            "output": 15.0,
            "cache_write": 3.75,
        },
        "claude-sonnet-4-5": {
            "input": 3.0,
            "cached": 0.30,
            "output": 15.0,
            "cache_write": 3.75,
        },
        "claude-sonnet": {
            "input": 3.0,
            "cached": 0.30,
            "output": 15.0,
            "cache_write": 3.75,
        },
        "claude-opus-4": {
            "input": 15.0,
            "cached": 1.50,
            "output": 75.0,
            "cache_write": 18.75,
        },
        "claude-opus": {
            "input": 15.0,
            "cached": 1.50,
            "output": 75.0,
            "cache_write": 18.75,
        },
        "claude-haiku": {
            "input": 1.0,
            "cached": 0.10,
            "output": 5.0,
            "cache_write": 1.25,
        },
        "claude-fable": {
            "input": 5.0,
            "cached": 0.50,
            "output": 25.0,
            "cache_write": 6.25,
        },
        "gpt-5.5": {"input": 1.25, "cached": 0.125, "output": 10.0},
        "gpt-5": {"input": 1.25, "cached": 0.125, "output": 10.0},
        "gpt-6": {"input": 1.75, "cached": 0.175, "output": 14.0},
        "o3": {"input": 2.0, "cached": 0.5, "output": 8.0},
        "default": {"input": 1.0, "cached": 0.1, "output": 5.0},
    },
    "notes": (
        "Retail API estimates from measured local tokens. "
        "Not Antigravity/Claude/Codex subscription invoices; "
        "cache storage fees omitted. Gemini intro rates may apply through 2026-12-31 — "
        "edit .repo-local/ai/prices.json to override."
    ),
}


def _normalize_model(model: Optional[str]) -> str:
    if not model:
        return "default"
    return str(model).strip().lower()


def rates_for(model: Optional[str], table: Mapping[str, Any]) -> Mapping[str, float]:
    per = table.get("per_million") if isinstance(table.get("per_million"), dict) else {}
    key = _normalize_model(model)
    if key in per and isinstance(per[key], dict):
        return per[key]
    # Longest prefix match: claude-opus-5-5 → claude-opus
    best_key = None
    best_rates = None
    for candidate, rates in per.items():
        if candidate == "default" or not isinstance(rates, dict):
            continue
        name = str(candidate)
        if key.startswith(name) and (best_key is None or len(name) > len(best_key)):
            best_key = name
            best_rates = rates
    if best_rates is not None:
        return best_rates
    default = per.get("default")
    return default if isinstance(default, dict) else {}


def estimate_cost(usage: Mapping[str, Any], table: Mapping[str, Any]) -> float:
    """USD estimate from token counts and per-million rates."""
    rates = rates_for(usage.get("model"), table)
    input_rate = float(rates.get("input") or 0)
    cached_rate = float(rates.get("cached") or 0)
    output_rate = float(rates.get("output") or 0)
    write_rate = rates.get("cache_write")
    if write_rate is None:
        write_rate = input_rate * 1.25
    else:
        write_rate = float(write_rate)

    inp = int(usage.get("input_tokens") or 0)
    cached = int(usage.get("cached_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    write = int(usage.get("cache_write_tokens") or 0)
    return (
        inp * input_rate
        + cached * cached_rate
        + out * output_rate
        + write * write_rate
    ) / 1_000_000.0


def load_prices(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        return json.loads(json.dumps(DEFAULT_PRICES))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return json.loads(json.dumps(DEFAULT_PRICES))
    if not isinstance(data, dict):
        return json.loads(json.dumps(DEFAULT_PRICES))
    merged = json.loads(json.dumps(DEFAULT_PRICES))
    if isinstance(data.get("per_million"), dict):
        merged["per_million"].update(data["per_million"])
    if isinstance(data.get("currency"), str):
        merged["currency"] = data["currency"]
    if isinstance(data.get("notes"), str):
        merged["notes"] = data["notes"]
    return merged


def save_prices(path: Path, data: Mapping[str, Any]) -> dict:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "currency": data.get("currency") or "USD",
        "per_million": data.get("per_million") or DEFAULT_PRICES["per_million"],
        "notes": data.get("notes") or DEFAULT_PRICES["notes"],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return dict(payload)


def ensure_prices(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        return save_prices(path, DEFAULT_PRICES)
    return load_prices(path)


def update_prices(path: Path, patch: Mapping[str, Any]) -> dict:
    current: MutableMapping[str, Any] = ensure_prices(path)
    if "currency" in patch and isinstance(patch["currency"], str):
        current["currency"] = patch["currency"]
    if "notes" in patch and isinstance(patch["notes"], str):
        current["notes"] = patch["notes"]
    if isinstance(patch.get("per_million"), dict):
        per = current.setdefault("per_million", {})
        for key, rates in patch["per_million"].items():
            if isinstance(rates, dict):
                existing = per.get(key) if isinstance(per.get(key), dict) else {}
                merged = dict(existing)
                for rk, rv in rates.items():
                    if isinstance(rv, (int, float)):
                        merged[str(rk)] = float(rv)
                per[str(key)] = merged
    return save_prices(path, current)
