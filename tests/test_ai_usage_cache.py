import pathlib
import sqlite3
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "companion"))

from ai.usage_cache import refresh_usage_cache  # noqa: E402


class UsageCacheTests(unittest.TestCase):
    def descriptor(self, path, parser, *, version=1):
        stat = path.stat()
        return {
            "source_id": "fixture.source",
            "path": path,
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "parser": "fixture-parser",
            "parser_version": version,
            "parse": parser,
        }

    @staticmethod
    def batch(value=1, secret=None):
        event = {
            "event_key": "fixture:event",
            "source_id": "fixture.source",
            "tool": "fixture",
            "model": "fixture-model",
            "input_tokens": value,
            "cached_tokens": 0,
            "output_tokens": 0,
            "cache_write_tokens": 0,
        }
        if secret is not None:
            event["transcript"] = secret
        return {"events": [event], "reconciliation": [], "warnings": []}

    def test_unchanged_fingerprint_reuses_cached_events_without_reparse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            source = root / "usage.jsonl"
            source.write_text("one\n")
            cache = root / "usage.db"
            calls = []

            def parse():
                calls.append(1)
                return self.batch()

            first = refresh_usage_cache(cache, [self.descriptor(source, parse)])
            second = refresh_usage_cache(cache, [self.descriptor(source, parse)])
            self.assertEqual(len(calls), 1)
            self.assertEqual(first["cache"]["misses"], 1)
            self.assertEqual(second["cache"]["hits"], 1)
            self.assertEqual(second["events"][0]["input_tokens"], 1)

    def test_size_change_invalidates_when_mtime_ns_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            source = root / "usage.jsonl"
            source.write_text("one\n")
            cache = root / "usage.db"
            calls = []

            def parse():
                calls.append(1)
                return self.batch(len(calls))

            before = source.stat().st_mtime_ns
            refresh_usage_cache(cache, [self.descriptor(source, parse)])
            source.write_text("one plus more\n")
            source.touch()
            import os

            os.utime(source, ns=(before, before))
            result = refresh_usage_cache(cache, [self.descriptor(source, parse)])
            self.assertEqual(len(calls), 2)
            self.assertEqual(result["cache"]["reparsed"], 1)
            self.assertEqual(result["events"][0]["input_tokens"], 2)

    def test_deleted_file_removes_cached_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            one = root / "one"
            two = root / "two"
            one.write_text("1")
            two.write_text("2")
            cache = root / "usage.db"

            def parser(key):
                return lambda: {
                    **self.batch(),
                    "events": [{**self.batch()["events"][0], "event_key": key}],
                }

            refresh_usage_cache(
                cache,
                [
                    self.descriptor(one, parser("one")),
                    self.descriptor(two, parser("two")),
                ],
            )
            result = refresh_usage_cache(cache, [self.descriptor(one, parser("one"))])
            self.assertEqual(
                [event["event_key"] for event in result["events"]], ["one"]
            )
            self.assertEqual(result["cache"]["deleted"], 1)

    def test_parser_version_change_invalidates_cached_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            source = root / "usage"
            source.write_text("x")
            cache = root / "usage.db"
            calls = []

            def parse():
                calls.append(1)
                return self.batch(len(calls))

            refresh_usage_cache(cache, [self.descriptor(source, parse, version=1)])
            result = refresh_usage_cache(
                cache, [self.descriptor(source, parse, version=2)]
            )
            self.assertEqual(len(calls), 2)
            self.assertEqual(result["cache"]["reparsed"], 1)

    def test_malformed_changed_file_keeps_refresh_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            source = root / "usage"
            source.write_text("valid")
            cache = root / "usage.db"
            refresh_usage_cache(cache, [self.descriptor(source, lambda: self.batch(7))])
            source.write_text("malformed changed")

            def fail():
                raise ValueError("malformed")

            result = refresh_usage_cache(cache, [self.descriptor(source, fail)])
            self.assertEqual(result["events"][0]["input_tokens"], 7)
            self.assertEqual(result["cache"]["status"], "stale")
            self.assertEqual(result["warnings"][-1]["code"], "cache_refresh_failed")

    def test_corrupt_cache_falls_back_to_direct_aggregation_and_reports_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            source = root / "usage"
            source.write_text("valid")
            cache = root / "usage.db"
            cache.write_bytes(b"not sqlite")
            wal = root / "usage.db-wal"
            shm = root / "usage.db-shm"
            wal.write_bytes(b"stale wal")
            shm.write_bytes(b"stale shm")
            result = refresh_usage_cache(
                cache, [self.descriptor(source, lambda: self.batch(3))]
            )
            self.assertEqual(result["events"][0]["input_tokens"], 3)
            self.assertEqual(result["cache"]["status"], "bypassed")
            self.assertEqual(result["warnings"][-1]["code"], "cache_unavailable")
            self.assertTrue(list(root.glob("usage.db.corrupt-*")))
            self.assertFalse(wal.exists())
            self.assertFalse(shm.exists())

    def test_cache_never_persists_transcript_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            source = root / "usage"
            source.write_text("vendor data")
            cache = root / "usage.db"
            secret = "private transcript words must not persist"
            refresh_usage_cache(
                cache, [self.descriptor(source, lambda: self.batch(1, secret))]
            )
            self.assertNotIn(secret.encode(), cache.read_bytes())
            con = sqlite3.connect(cache)
            payload = con.execute("SELECT payload_json FROM events").fetchone()[0]
            con.close()
            self.assertNotIn("transcript", payload)


if __name__ == "__main__":
    unittest.main()
