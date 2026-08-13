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

    def setUp(self) -> None:
        self._original_env = dict(os.environ)
        # These limits are per MINUTE, so the requests below have to be fast.
        # Without pinning the provider off, an ambient GEMINI_API_KEY (from
        # .env, or left behind by an earlier test file) made every /api/session
        # attempt a real Gemini rewrite of the opening message, time out after
        # ~3s, and the 21 requests then spanned over a minute -- the rate-limit
        # window rolled over mid-test and the final request came back 200
        # instead of 429. Passed alone, failed in full-suite order.
        os.environ["AI_PROVIDER"] = "none"
        os.environ["GEMINI_API_KEY"] = ""

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._original_env)

    def test_session_creation_is_rate_limited(self) -> None:
        from services.ai_agent.ai_agent_app.server import create_app

        app = create_app()
        client = app.test_client()
        statuses = [client.post("/api/session").status_code for _ in range(21)]
        self.assertTrue(all(code != 429 for code in statuses[:20]))
        self.assertEqual(statuses[20], 429)

    def test_webhook_is_rate_limited(self) -> None:
        """Default raised from 60/min to 300/min: this limit is keyed by
        remote address, but every Instagram customer's message arrives via
        Meta's own calling infrastructure, not the customer's own IP -- so
        it's one ceiling shared across every customer combined, and 60/min
        could plausibly throttle a handful of people chatting at once.
        """
        from services.ai_agent.ai_agent_app.server import create_app

        app = create_app()
        client = app.test_client()
        statuses = [client.post("/rahma-agent/webhook", json={}).status_code for _ in range(301)]
        self.assertTrue(all(code != 429 for code in statuses[:300]))
        self.assertEqual(statuses[300], 429)

    def test_webhook_rate_limit_is_adjustable_via_env_var(self) -> None:
        from services.ai_agent.ai_agent_app.server import create_app

        os.environ["WEBHOOK_RATE_LIMIT"] = "5 per minute"
        try:
            app = create_app()
            client = app.test_client()
            statuses = [client.post("/rahma-agent/webhook", json={}).status_code for _ in range(6)]
        finally:
            os.environ.pop("WEBHOOK_RATE_LIMIT", None)
        self.assertTrue(all(code != 429 for code in statuses[:5]))
        self.assertEqual(statuses[5], 429)


if __name__ == "__main__":
    unittest.main()
