from __future__ import annotations

import os
import shutil
import sys
import unittest
import uuid
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent / "apps" / "api"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def _reset_app_modules() -> None:
    for module_name in list(sys.modules):
        if module_name == "app" or module_name.startswith("app."):
            sys.modules.pop(module_name, None)


class RedisSharedStateTests(unittest.TestCase):
    """Confirmed live (2026-08-08): rahma-crm-api actually runs 2 plain
    gunicorn workers with no shared state at all -- Socket.IO handoff
    notifications and the rate limiter's counters both default to living
    in one process's memory, so a notification fired on the process that
    isn't holding the target admin's websocket silently never arrives.
    REDIS_URL gives every worker a shared backend for both; this only
    checks the wiring picks it up correctly, not real Redis pub/sub
    behavior (no Redis server available in this environment) -- both
    Flask-Limiter and Flask-SocketIO connect lazily, so pointing REDIS_URL
    at an address with nothing listening is enough to prove the code path
    is taken without needing a real server up.
    """

    def setUp(self) -> None:
        self.original_env = dict(os.environ)
        self.tmpdir = Path(".tmp-test-workdirs") / f"redis-wiring-{uuid.uuid4().hex}"
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.tmpdir / "app.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{self.db_path.resolve().as_posix()}"
        os.environ["RAHMA_SYSTEM_DB_PATH"] = str(self.db_path)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.original_env)
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        _reset_app_modules()

    def test_without_redis_url_state_stays_in_process_memory(self) -> None:
        os.environ.pop("REDIS_URL", None)
        _reset_app_modules()
        from app import create_app

        create_app("development")
        from app.extensions import limiter, socketio

        self.assertEqual(limiter._storage_uri, "memory://")
        import socketio as socketio_pkg

        self.assertIsInstance(socketio.server.manager, socketio_pkg.base_manager.BaseManager)
        self.assertNotIsInstance(socketio.server.manager, socketio_pkg.redis_manager.RedisManager)

    def test_redis_url_wires_a_shared_backend_for_both(self) -> None:
        os.environ["REDIS_URL"] = "redis://127.0.0.1:6399/0"
        _reset_app_modules()
        from app import create_app

        create_app("development")
        from app.extensions import limiter, socketio
        import socketio as socketio_pkg

        self.assertEqual(limiter._storage_uri, "redis://127.0.0.1:6399/0")
        self.assertIsInstance(socketio.server.manager, socketio_pkg.redis_manager.RedisManager)


if __name__ == "__main__":
    unittest.main()
