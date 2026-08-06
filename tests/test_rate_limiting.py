from __future__ import annotations

import os
import shutil
import sys
import tempfile
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


class LoginRateLimitTests(unittest.TestCase):
    """Regression: /login previously had no rate limit at all -- an attacker
    could brute-force credentials with unlimited attempts per minute. The
    limiter is disabled by default under pytest (RATELIMIT_ENABLED defaults
    to off whenever PYTEST_CURRENT_TEST is set, since app.config["TESTING"]
    is normally set too late for Flask-Limiter to see it), so this test
    opts back in explicitly via the RATELIMIT_ENABLED env var override.
    """

    def setUp(self) -> None:
        self.tmpdir = Path(".tmp-test-workdirs") / f"ratelimit-login-{uuid.uuid4().hex}"
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        db_path = self.tmpdir / "app.db"
        os.environ["RATELIMIT_ENABLED"] = "true"
        os.environ["DATABASE_URL"] = f"sqlite:///{db_path.resolve().as_posix()}"
        os.environ["RAHMA_SYSTEM_DB_PATH"] = str(db_path)
        _reset_app_modules()
        from app import create_app

        self.app = create_app("development")
        self.app.config["TESTING"] = True
        from app.extensions import db

        self.db = db
        with self.app.app_context():
            db.create_all()
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        with self.app.app_context():
            self.db.session.remove()
            self.db.engine.dispose()
        os.environ.pop("RATELIMIT_ENABLED", None)
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("RAHMA_SYSTEM_DB_PATH", None)
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        _reset_app_modules()

    def test_repeated_login_attempts_are_rate_limited(self) -> None:
        statuses = [
            self.client.post("/login", data={"username": "nobody", "password": "wrong"}).status_code
            for _ in range(11)
        ]
        self.assertTrue(all(code == 200 for code in statuses[:10]))
        self.assertEqual(statuses[10], 429)


class AgentApiRateLimitTests(unittest.TestCase):
    """Regression: the ai_agent Flask app's public, unauthenticated
    endpoints (/api/session, /api/session/<id>/message,
    /rahma-agent/webhook) had a TODO acknowledging the missing rate
    limiting but no enforcement --
    unlimited requests could trigger unlimited LLM/DB work per caller.
    Each test builds its own app via create_app(), so the limiter's
    in-memory counters never leak between tests.
    """

    def test_session_creation_is_rate_limited(self) -> None:
        from services.ai_agent.ai_agent_app.server import create_app

        app = create_app()
        client = app.test_client()
        statuses = [client.post("/api/session").status_code for _ in range(21)]
        self.assertTrue(all(code != 429 for code in statuses[:20]))
        self.assertEqual(statuses[20], 429)

    def test_webhook_is_rate_limited(self) -> None:
        from services.ai_agent.ai_agent_app.server import create_app

        app = create_app()
        client = app.test_client()
        statuses = [client.post("/rahma-agent/webhook", json={}).status_code for _ in range(61)]
        self.assertTrue(all(code != 429 for code in statuses[:60]))
        self.assertEqual(statuses[60], 429)


if __name__ == "__main__":
    unittest.main()
