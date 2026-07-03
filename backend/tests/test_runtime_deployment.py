from __future__ import annotations

import json
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from app.core.config import load_settings
from app.db.jobs import REQUIRED_RUNTIME_QUEUES


class RuntimeDeploymentTests(unittest.TestCase):
    def test_railway_worker_configs_cover_required_runtime_queues(self) -> None:
        backend_dir = Path(__file__).resolve().parents[1]
        configured_queues: set[str] = set()

        for path in backend_dir.glob("railway.worker-*.json"):
            payload = json.loads(path.read_text())
            command = str(payload.get("deploy", {}).get("startCommand", ""))
            match = re.search(r"--queues\s+([^\s]+)", command)
            self.assertIsNotNone(match, f"{path.name} must declare worker queues")
            configured_queues.update(queue.strip() for queue in match.group(1).split(",") if queue.strip())

        self.assertEqual(set(REQUIRED_RUNTIME_QUEUES) - configured_queues, set())

    def test_default_recent_gmail_window_matches_smart_inbox_hot_window(self) -> None:
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://example/db"}, clear=True):
            settings = load_settings()

        self.assertEqual(settings.gmail_recent_days, 30)


if __name__ == "__main__":
    unittest.main()
