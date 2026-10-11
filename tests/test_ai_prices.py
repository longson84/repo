import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "companion"))

from ai import prices  # noqa: E402


class PricesTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
