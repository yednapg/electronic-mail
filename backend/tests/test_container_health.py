from __future__ import annotations

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from app import container_health


class _Response(BytesIO):
    def __init__(self, body: bytes, *, status: int = 200) -> None:
        super().__init__(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class ContainerHealthTests(unittest.TestCase):
    def test_image_start_period_covers_schema_startup_wait(self) -> None:
        repository_root = Path(__file__).resolve().parents[2]
        dockerfile = (repository_root / "backend" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("--start-period=150s", dockerfile)
        self.assertIn("CMD python -m app.container_health", dockerfile)
        self.assertIn("python -m app.schema_check --wait-seconds 120", dockerfile)

    def test_shared_image_recognizes_api_workers_and_poller(self) -> None:
        self.assertEqual(
            container_health.process_role(("/usr/local/bin/python", "/usr/local/bin/uvicorn", "app.main:app")),
            "api",
        )
        for module in ("app.workers.main", "app.workers.gmail_poller"):
            with self.subTest(module=module):
                self.assertEqual(
                    container_health.process_role(("python", "-m", module)),
                    "worker",
                )

    def test_shared_image_rejects_unknown_pid_one_process(self) -> None:
        with self.assertRaisesRegex(container_health.ContainerHealthError, "not a reviewed"):
            container_health.process_role(("python", "unexpected.py"))

    def test_pid_one_arguments_are_read_without_shell_interpretation(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "cmdline"
            path.write_bytes(b"python\0-m\0app.workers.main\0--queues\0critical,default\0")
            self.assertEqual(
                container_health.read_pid_one_arguments(path),
                ("python", "-m", "app.workers.main", "--queues", "critical,default"),
            )

    def test_api_probe_requires_bounded_valid_ok_json(self) -> None:
        calls: list[tuple[str, int]] = []

        def opener(url: str, *, timeout: int):
            calls.append((url, timeout))
            return _Response(b'{"status":"ok","release":"abc"}')

        container_health.check_api(port="3001", opener=opener)
        self.assertEqual(calls, [("http://127.0.0.1:3001/health", 3)])

        for port in ("0", "65536", "3e3", " 3001"):
            with self.subTest(port=port), self.assertRaises(container_health.ContainerHealthError):
                container_health.check_api(port=port, opener=opener)

        with self.assertRaisesRegex(container_health.ContainerHealthError, "not ok"):
            container_health.check_api(
                port="3001",
                opener=lambda *_args, **_kwargs: _Response(b'{"status":"not_ok"}'),
            )

    def test_main_checks_http_only_for_api_role(self) -> None:
        with (
            patch.object(container_health, "read_pid_one_arguments", return_value=("python", "-m", "app.workers.main")),
            patch.object(container_health, "check_api") as check_api,
        ):
            self.assertEqual(container_health.main(), 0)
        check_api.assert_not_called()

        with (
            patch.object(
                container_health,
                "read_pid_one_arguments",
                return_value=("python", "/usr/local/bin/uvicorn", "app.main:app"),
            ),
            patch.object(container_health, "check_api") as check_api,
        ):
            self.assertEqual(container_health.main(), 0)
        check_api.assert_called_once()


if __name__ == "__main__":
    unittest.main()
