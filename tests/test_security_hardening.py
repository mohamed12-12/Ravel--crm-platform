from __future__ import annotations

import io
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path

from services.ai_agent.ai_agent_app.server import _allowed_attachment
from services.instagram.webhooks import verify_webhook

APP_ROOT = Path(__file__).resolve().parent.parent / "apps" / "api"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


class SecurityHardeningTests(unittest.TestCase):
    def test_crm_write_requires_auth_when_enabled(self):
        from app import create_app

        original = os.environ.get("DATABASE_URL")
        original_system_db = os.environ.get("RAHMA_SYSTEM_DB_PATH")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                db_path = Path(tmp) / "security-hardening.db"
                os.environ["DATABASE_URL"] = f"sqlite:///{db_path.resolve().as_posix()}"
                os.environ["RAHMA_SYSTEM_DB_PATH"] = str(db_path.resolve())
                app = create_app("development")
                app.config.update(CRM_AUTH_ENABLED=True, CRM_API_TOKEN="test-token")
                from app.extensions import db
                with app.app_context():
                    db.create_all()
                client = app.test_client()
                denied = client.post("/interactions/", json={"customer_name": "x"})
                self.assertEqual(denied.status_code, 401)
                allowed = client.post(
                    "/interactions/",
                    json={"customer_name": "x"},
                    headers={"X-CRM-API-Key": "test-token"},
                )
                self.assertEqual(allowed.status_code, 201)
                with app.app_context():
                    db.session.remove()
                    db.engine.dispose()
        finally:
            if original is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = original
            if original_system_db is None:
                os.environ.pop("RAHMA_SYSTEM_DB_PATH", None)
            else:
                os.environ["RAHMA_SYSTEM_DB_PATH"] = original_system_db

    def test_admin_write_requires_admin_role(self):
        from app import create_app

        app = create_app("development")
        app.config.update(CRM_AUTH_ENABLED=True, CRM_API_TOKEN="test-token")
        client = app.test_client()
        response = client.post(
            "/api/crm/resolve-identity",
            json={"master_id": "TR00001", "alias_ids": ["TR00002"]},
            headers={"X-CRM-API-Key": "test-token", "X-CRM-Role": "agent"},
        )
        self.assertEqual(response.status_code, 403)

    def test_attachment_mime_and_extension_are_both_checked(self):
        self.assertTrue(_allowed_attachment("passport.jpg", "image/jpeg"))
        self.assertFalse(_allowed_attachment("passport.exe", "application/octet-stream"))
        self.assertFalse(_allowed_attachment("passport.jpg", "application/javascript"))

    def test_crm_debug_is_disabled_by_default(self):
        from app import create_app

        app = create_app("development")
        self.assertFalse(app.debug)

    def test_webhook_verification_does_not_log_token(self):
        from flask import Flask

        app = Flask(__name__)
        app.secret_key = "test"
        with app.test_request_context(
            "/webhook?hub.mode=subscribe&hub.verify_token=customer-secret&hub.challenge=1"
        ):
            with self.assertLogs("rahma_webhook", level=logging.WARNING) as captured:
                verify_webhook("server-secret")
        self.assertNotIn("customer-secret", "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()
