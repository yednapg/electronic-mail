from __future__ import annotations

from contextlib import nullcontext
import unittest
from unittest.mock import patch

from sqlalchemy.pool import NullPool, QueuePool

from app.db import mail_groups, repository


class _DisposableEngine:
    def __init__(self, *, fail: bool = False) -> None:
        self.dispose_calls = 0
        self.fail = fail

    def dispose(self) -> None:
        self.dispose_calls += 1
        if self.fail:
            raise RuntimeError("forced disposal failure")


class _ProbeEngine(_DisposableEngine):
    def __init__(self, connection: object) -> None:
        super().__init__()
        self.connection = connection

    def connect(self):
        return nullcontext(self.connection)


class RepositoryEngineLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        with repository._ENGINES_LOCK:
            self.original_engines = dict(repository._ENGINES)
            self.original_advisory_lock_engines = dict(repository._ADVISORY_LOCK_ENGINES)
            repository._ENGINES.clear()
            repository._ADVISORY_LOCK_ENGINES.clear()

    def tearDown(self) -> None:
        repository.dispose_cached_engines()
        with repository._ENGINES_LOCK:
            repository._ENGINES.update(self.original_engines)
            repository._ADVISORY_LOCK_ENGINES.update(self.original_advisory_lock_engines)

    def test_disposal_clears_cache_and_disposes_each_engine_exactly_once(self) -> None:
        shared = _DisposableEngine()
        second = _DisposableEngine()
        with repository._ENGINES_LOCK:
            repository._ENGINES.update(
                {
                    "postgresql+psycopg://first": shared,
                    "postgresql+psycopg://same-engine-alias": shared,
                }
            )
            repository._ADVISORY_LOCK_ENGINES["postgresql+psycopg://second"] = second

        repository.dispose_cached_engines()
        repository.dispose_cached_engines()

        self.assertEqual(shared.dispose_calls, 1)
        self.assertEqual(second.dispose_calls, 1)
        self.assertEqual(repository._ENGINES, {})
        self.assertEqual(repository._ADVISORY_LOCK_ENGINES, {})

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
        self.assertEqual(repository._ADVISORY_LOCK_ENGINES, {})

    def test_advisory_sessions_use_a_distinct_bounded_pool_engine(self) -> None:
        database_url = "postgresql://localhost/electronic_mail_lock_test"

        main_engine = repository.get_engine(database_url)
        lock_engine = repository.get_advisory_lock_engine(database_url)

        self.assertIsNot(main_engine, lock_engine)
        self.assertIsInstance(lock_engine.pool, QueuePool)
        self.assertIsInstance(main_engine.pool, QueuePool)
        self.assertEqual(lock_engine.pool.size(), repository.ADVISORY_LOCK_POOL_SIZE)
        self.assertEqual(lock_engine.pool._max_overflow, 0)
        self.assertEqual(
            lock_engine.pool._timeout,
            repository.ADVISORY_LOCK_POOL_TIMEOUT_SECONDS,
        )
        self.assertIs(repository.get_advisory_lock_engine(database_url), lock_engine)

    def test_every_postgres_pool_has_a_bounded_connect_timeout(self) -> None:
        database_url = "postgresql://localhost/electronic_mail_timeout_test"
        main_engine = _DisposableEngine()
        advisory_engine = _DisposableEngine()

        with patch.object(
            repository,
            "create_engine",
            side_effect=[main_engine, advisory_engine],
        ) as create_engine:
            self.assertIs(repository.get_engine(database_url), main_engine)
            self.assertIs(
                repository.get_advisory_lock_engine(database_url),
                advisory_engine,
            )

        self.assertEqual(create_engine.call_count, 2)
        for call in create_engine.call_args_list:
            self.assertEqual(
                call.kwargs["connect_args"],
                {"connect_timeout": repository.POSTGRES_CONNECT_TIMEOUT_SECONDS},
            )

    def test_schema_probe_bypasses_cached_pool_and_pre_ping_with_startup_budgets(self) -> None:
        connection = object()
        engine = _ProbeEngine(connection)

        with patch.object(repository, "create_engine", return_value=engine) as create_engine:
            with repository.connect_bounded_schema_probe(
                "postgresql://localhost/electronic_mail_probe_test",
                connect_timeout_seconds=5,
                lock_timeout_ms=5_000,
                statement_timeout_ms=15_000,
                transaction_timeout_ms=20_000,
            ) as actual_connection:
                self.assertIs(actual_connection, connection)

        self.assertEqual(engine.dispose_calls, 1)
        create_engine.assert_called_once()
        arguments = create_engine.call_args
        self.assertIs(arguments.kwargs["poolclass"], NullPool)
        self.assertIs(arguments.kwargs["pool_pre_ping"], False)
        self.assertEqual(
            arguments.kwargs["connect_args"],
            {
                "connect_timeout": 5,
                "options": (
                    "-c lock_timeout=5000 "
                    "-c statement_timeout=15000 "
                    "-c transaction_timeout=20000"
                ),
            },
        )
        self.assertEqual(repository._ENGINES, {})
        self.assertEqual(repository._ADVISORY_LOCK_ENGINES, {})

    def test_draft_session_lock_does_not_consume_the_main_queue_pool(self) -> None:
        with patch.object(
            mail_groups,
            "advisory_session_locks",
            return_value=nullcontext(),
        ) as session_lock, patch.object(
            mail_groups,
            "get_engine",
        ) as main_engine:
            with mail_groups.client_draft_lock(
                "postgresql://example/db",
                user_id="user-1",
                client_draft_id="draft-1",
            ):
                main_engine.assert_not_called()

        session_lock.assert_called_once_with(
            "postgresql://example/db",
            lock_keys=[mail_groups.client_draft_lock_key("user-1", "draft-1")],
        )
