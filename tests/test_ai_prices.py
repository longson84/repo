import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "companion"))

from ai import prices  # noqa: E402


class PricesTests(unittest.TestCase):
    def test_unknown_model_is_unpriced_instead_of_using_default(self):
        result = prices.price_usage(
            {"provider": "openai", "model": "unknown", "input_tokens": 1_000_000},
            {"per_million": {"default": {"input": 99.0}}},
        )
        self.assertEqual(result["status"], "unpriced_model")
        self.assertEqual(result["cost_usd"], 0.0)

    def test_model_prefix_does_not_match_without_explicit_alias(self):
        result = prices.price_usage(
            {"provider": "anthropic", "model": "claude-opus-5", "input_tokens": 1_000_000},
            {"per_million": {"claude-opus": {"input": 15.0}}},
        )
        self.assertEqual(result["status"], "unpriced_model")

    def test_explicit_alias_resolves_to_exact_rate(self):
        table = {
            "version": 2,
            "aliases": {"openai:gpt-latest": "gpt-6"},
            "rates": [
                {"provider": "openai", "model": "gpt-6", "input": 2.0}
            ],
        }
        result = prices.price_usage(
            {"provider": "openai", "model": "gpt-latest", "input_tokens": 1_000_000},
            table,
        )
        self.assertEqual(result["status"], "priced")
        self.assertEqual(result["cost_usd"], 2.0)
        self.assertEqual(result["matched_rate"]["model"], "gpt-6")

    def test_effective_rate_boundaries_are_inclusive(self):
        table = {
            "rates": [
                {
                    "provider": "openai",
                    "model": "gpt-6",
                    "effective_from": "2026-01-01",
                    "effective_to": "2026-01-31",
                    "input": 2.0,
                }
            ]
        }
        for event_date in ("2026-01-01", "2026-01-31"):
            with self.subTest(event_date=event_date):
                _, status = prices.resolve_rate(
                    provider="openai", model="gpt-6", event_date=event_date, table=table
                )
                self.assertEqual(status, "priced")

    def test_first_day_outside_rate_period_is_unpriced(self):
        table = {
            "rates": [
                {
                    "provider": "openai",
                    "model": "gpt-6",
                    "effective_from": "2026-01-01",
                    "effective_to": "2026-01-31",
                    "input": 2.0,
                }
            ]
        }
        rate, status = prices.resolve_rate(
            provider="openai", model="gpt-6", event_date="2026-02-01", table=table
        )
        self.assertIsNone(rate)
        self.assertEqual(status, "outside_rate_period")

    def test_legacy_per_million_loads_as_undated_exact_rates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "prices.json"
            path.write_text(
                '{"per_million":{"custom-model":{"input":7.0}},"currency":"USD"}'
            )
            table = prices.load_prices(path)
            rate, status = prices.resolve_rate(
                provider="vendor", model="custom-model", event_date=None, table=table
            )
            self.assertEqual(status, "priced")
            self.assertEqual(rate["input"], 7.0)

    def test_estimate_cost_known_rates(self):
        table = {
            "currency": "USD",
            "per_million": {
                "gemini-3-flash": {"input": 1.0, "cached": 0.1, "output": 4.0},
            },
        }
        cost = prices.estimate_cost(
            {
                "model": "gemini-3-flash",
                "input_tokens": 1_000_000,
                "cached_tokens": 1_000_000,
                "output_tokens": 1_000_000,
            },
            table,
        )
        self.assertAlmostEqual(cost, 1.0 + 0.1 + 4.0)

    def test_cache_write_uses_dedicated_or_1_25x_input(self):
        table = {
            "per_million": {
                "claude-sonnet-4-6": {
                    "input": 3.0,
                    "cached": 0.3,
                    "output": 15.0,
                }
            }
        }
        cost = prices.estimate_cost(
            {
                "model": "claude-sonnet-4-6",
                "input_tokens": 1_000_000,
                "cached_tokens": 0,
                "output_tokens": 0,
                "cache_write_tokens": 1_000_000,
            },
            table,
        )
        self.assertAlmostEqual(cost, 3.0 + 3.75)

    def test_ensure_prices_writes_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "prices.json"
            data = prices.ensure_prices(path)
            self.assertTrue(path.is_file())
            self.assertEqual(data["currency"], "USD")
            self.assertIn("gemini-3-flash", data["per_million"])
            again = prices.ensure_prices(path)
            self.assertEqual(again["notes"], data["notes"])

    def test_save_prices_is_owner_only_and_preserves_versioned_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "prices.json"
            data = prices.save_prices(
                path,
                {
                    "version": 2,
                    "currency": "USD",
                    "rates": [{"provider": "openai", "model": "gpt-x", "input": 1.0}],
                    "aliases": {"openai:latest": "gpt-x"},
                },
            )
            self.assertEqual(data["rates"][0]["model"], "gpt-x")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
