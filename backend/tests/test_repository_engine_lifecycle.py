from __future__ import annotations

import unittest

from app.db import repository


class _DisposableEngine:
    def __init__(self, *, fail: bool = False) -> None:
        self.dispose_calls = 0
        self.fail = fail

    def dispose(self) -> None:
        self.dispose_calls += 1
        if self.fail:
            raise RuntimeError("forced disposal failure")


class RepositoryEngineLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        with repository._ENGINES_LOCK:
            self.original_engines = dict(repository._ENGINES)
            repository._ENGINES.clear()

    def tearDown(self) -> None:
        repository.dispose_cached_engines()
        with repository._ENGINES_LOCK:
            repository._ENGINES.update(self.original_engines)

    def test_disposal_clears_cache_and_disposes_each_engine_exactly_once(self) -> None:
        shared = _DisposableEngine()
        second = _DisposableEngine()
        with repository._ENGINES_LOCK:
            repository._ENGINES.update(
                {
                    "postgresql+psycopg://first": shared,
                    "postgresql+psycopg://same-engine-alias": shared,
                    "postgresql+psycopg://second": second,
                }
            )

        repository.dispose_cached_engines()
        repository.dispose_cached_engines()

        self.assertEqual(shared.dispose_calls, 1)
        self.assertEqual(second.dispose_calls, 1)
        self.assertEqual(repository._ENGINES, {})

    def test_disposal_failure_does_not_leave_or_skip_other_cached_engines(self) -> None:
        broken = _DisposableEngine(fail=True)
        healthy = _DisposableEngine()
        with repository._ENGINES_LOCK:
            repository._ENGINES.update(
                {
                    "postgresql+psycopg://broken": broken,
                    "postgresql+psycopg://healthy": healthy,
                }
            )

        with self.assertLogs("app.db.repository", level="WARNING"):
            repository.dispose_cached_engines()

        self.assertEqual(broken.dispose_calls, 1)
        self.assertEqual(healthy.dispose_calls, 1)
        self.assertEqual(repository._ENGINES, {})
