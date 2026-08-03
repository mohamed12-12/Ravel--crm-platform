from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
API_ROOT = ROOT / "apps" / "api"


def _reset_app_modules() -> None:
    for module_name in list(sys.modules):
        if module_name == "app" or module_name.startswith("app."):
            sys.modules.pop(module_name, None)


def _csrf_from_session(client) -> str:
    with client.session_transaction() as sess:
        return str(sess.get("csrf_token") or "")


class AdminLoginProtectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = ROOT / ".tmp-test-workdirs" / f"admin-login-{uuid.uuid4().hex}"
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.tmpdir / "app.db"
        self.runtime_workbook = self.tmpdir / "runtime.xlsx"
        self.admin_password = f"pw-{uuid.uuid4().hex}"
        self.original_env = dict(os.environ)
        os.environ["CRM_AUTH_ENABLED"] = "true"
        os.environ["ADMIN_USERNAME"] = "admin"
        os.environ["ADMIN_PASSWORD"] = self.admin_password
        os.environ["DATABASE_URL"] = f"sqlite:///{self.db_path.resolve().as_posix()}"
        os.environ["RAHMA_SYSTEM_DB_PATH"] = str(self.db_path.resolve())
        os.environ["SHEET_BACKEND"] = "excel"
        os.environ["EXCEL_SOURCE_WORKBOOK"] = str(ROOT / "tests" / "fixtures" / "operational_source_workbook.xlsx")
        os.environ["EXCEL_RUNTIME_WORKBOOK"] = str(self.runtime_workbook)
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        if str(API_ROOT) not in sys.path:
            sys.path.insert(0, str(API_ROOT))
        _reset_app_modules()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.original_env)
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        _reset_app_modules()

    def _make_app(self):
        from app import create_app
        from app.extensions import db

        app = create_app("development")
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        with app.app_context():
            db.drop_all()
            db.create_all()
        return app

    def test_login_page_loads(self) -> None:
        app = self._make_app()
        with app.test_client() as client:
            response = client.get("/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Username", response.data)
        self.assertIn(b"Password", response.data)

    def test_invalid_login_is_rejected(self) -> None:
        app = self._make_app()
        with app.test_client() as client:
            client.get("/login")
            csrf = _csrf_from_session(client)
            response = client.post(
                "/login",
                data={"csrf_token": csrf, "username": "admin", "password": "wrong"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Invalid username or password.", response.data)

    def test_dashboard_is_blocked_without_login(self) -> None:
        app = self._make_app()
        with app.test_client() as client:
            response = client.get("/admin/dashboard")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers.get("Location", ""))

    def test_valid_login_redirects_to_dashboard_and_logout_blocks_again(self) -> None:
        app = self._make_app()
        from app.extensions import db
        from app.models.user_audit import UserAuditLog

        with app.test_client() as client:
            client.get("/login")
            csrf = _csrf_from_session(client)
            response = client.post(
                "/login",
                data={
                    "csrf_token": csrf,
                    "username": "admin",
                    "password": self.admin_password,
                },
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 302)
            self.assertIn("/admin/dashboard", response.headers.get("Location", ""))

            dashboard = client.get("/admin/dashboard")
            self.assertEqual(dashboard.status_code, 200)
            self.assertIn(b"Admin Dashboard", dashboard.data)
            self.assertIn(b"Welcome back, admin.", dashboard.data)
            self.assertIn(b"Recent Sign-ins", dashboard.data)
            self.assertIn(b"admin", dashboard.data)

            with app.app_context():
                login_events = UserAuditLog.query.filter_by(action="login").all()
                self.assertEqual(len(login_events), 1)
                self.assertEqual(login_events[0].target.username, "admin")

            logout_csrf = _csrf_from_session(client)
            logout = client.post(
                "/logout",
                data={"csrf_token": logout_csrf},
                follow_redirects=False,
            )
            self.assertEqual(logout.status_code, 302)
            self.assertIn("/login", logout.headers.get("Location", ""))

            blocked = client.get("/admin/dashboard")
            self.assertEqual(blocked.status_code, 302)
            self.assertIn("/login", blocked.headers.get("Location", ""))

            with app.app_context():
                db.session.remove()
                db.engine.dispose()


if __name__ == "__main__":
    unittest.main()
