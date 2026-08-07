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

    def test_newly_created_employee_can_log_in_with_role_scoped_access(self) -> None:
        """Confirmed bug: /admin/users/create saves a real employee row (with
        its own hashed password and role), but login() only ever checked
        _credentials_match against the single env-var admin identity -- it
        never consulted the users table at all, so no created employee could
        log in under their own credentials no matter how they were created.
        """
        app = self._make_app()
        with app.test_client() as client:
            client.get("/login")
            csrf = _csrf_from_session(client)
            client.post(
                "/login",
                data={"csrf_token": csrf, "username": "admin", "password": self.admin_password},
            )
            create_csrf = _csrf_from_session(client)
            create_response = client.post(
                "/admin/users/create",
                data={
                    "csrf_token": create_csrf,
                    "username": "mariam",
                    "full_name": "Mariam Agent",
                    "role": "agent",
                    "password": "TempPass123!",
                },
                follow_redirects=False,
            )
            self.assertEqual(create_response.status_code, 302)

            logout_csrf = _csrf_from_session(client)
            client.post("/logout", data={"csrf_token": logout_csrf})

        with app.test_client() as employee_client:
            employee_client.get("/login")
            employee_csrf = _csrf_from_session(employee_client)
            login_response = employee_client.post(
                "/login",
                data={"csrf_token": employee_csrf, "username": "mariam", "password": "TempPass123!"},
                follow_redirects=False,
            )
            self.assertEqual(login_response.status_code, 302)
            self.assertIn("/admin/dashboard", login_response.headers.get("Location", ""))

            dashboard = employee_client.get("/admin/dashboard")
            self.assertEqual(dashboard.status_code, 200)

            # Role-scoped: an "agent" has no manage_users permission, unlike admin.
            users_page = employee_client.get("/admin/users")
            self.assertEqual(users_page.status_code, 403)

    def test_wrong_password_for_a_real_employee_is_rejected(self) -> None:
        app = self._make_app()
        from app.extensions import db
        from app.models.user import User
        from werkzeug.security import generate_password_hash

        with app.app_context():
            db.session.add(
                User(username="karim", full_name="Karim Sales", role="sales",
                     is_active=True, password_hash=generate_password_hash("RealPassword123!"))
            )
            db.session.commit()

        with app.test_client() as client:
            client.get("/login")
            csrf = _csrf_from_session(client)
            response = client.post(
                "/login",
                data={"csrf_token": csrf, "username": "karim", "password": "WrongPassword!"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Invalid username or password.", response.data)

    def test_deactivated_employee_cannot_log_in(self) -> None:
        app = self._make_app()
        from app.extensions import db
        from app.models.user import User
        from werkzeug.security import generate_password_hash

        with app.app_context():
            db.session.add(
                User(username="nour", full_name="Nour Manager", role="manager",
                     is_active=False, password_hash=generate_password_hash("RealPassword123!"))
            )
            db.session.commit()

        with app.test_client() as client:
            client.get("/login")
            csrf = _csrf_from_session(client)
            response = client.post(
                "/login",
                data={"csrf_token": csrf, "username": "nour", "password": "RealPassword123!"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Invalid username or password.", response.data)

    def _login_as_admin(self, client) -> None:
        client.get("/login")
        csrf = _csrf_from_session(client)
        client.post(
            "/login",
            data={"csrf_token": csrf, "username": "admin", "password": self.admin_password},
        )

    def test_admin_can_delete_employee_with_no_assignments(self) -> None:
        app = self._make_app()
        from app.extensions import db
        from app.models.user import User

        with app.app_context():
            db.session.add(User(username="hana", full_name="Hana Agent", role="agent",
                                 is_active=True, password_hash="x"))
            db.session.commit()
            hana_id = User.query.filter_by(username="hana").first().id

        with app.test_client() as client:
            self._login_as_admin(client)
            csrf = _csrf_from_session(client)
            response = client.post(
                f"/admin/users/{hana_id}/delete",
                data={"csrf_token": csrf},
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 302)

        with app.app_context():
            self.assertIsNone(db.session.get(User, hana_id))

    def test_admin_cannot_delete_employee_with_assigned_leads_or_bookings(self) -> None:
        app = self._make_app()
        from app.extensions import db
        from app.models.user import User
        from app.models.lead import Lead

        with app.app_context():
            employee = User(username="sami", full_name="Sami Agent", role="agent",
                             is_active=True, password_hash="x")
            db.session.add(employee)
            db.session.flush()
            db.session.add(Lead(lead_id="LD-DEL-1", customer_name="Test Lead", assigned_to_user_id=employee.id))
            db.session.commit()
            sami_id = employee.id

        with app.test_client() as client:
            self._login_as_admin(client)
            csrf = _csrf_from_session(client)
            response = client.post(
                f"/admin/users/{sami_id}/delete",
                data={"csrf_token": csrf},
                follow_redirects=True,
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Reassign", response.data)

        with app.app_context():
            self.assertIsNotNone(db.session.get(User, sami_id))

    def test_admin_cannot_delete_their_own_account(self) -> None:
        app = self._make_app()
        from app.extensions import db
        from app.models.user import User

        with app.test_client() as client:
            self._login_as_admin(client)
            with app.app_context():
                admin_id = User.query.filter_by(username="admin").first().id
            csrf = _csrf_from_session(client)
            response = client.post(
                f"/admin/users/{admin_id}/delete",
                data={"csrf_token": csrf},
                follow_redirects=True,
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"cannot delete your own account", response.data)

        with app.app_context():
            self.assertIsNotNone(db.session.get(User, admin_id))

    def test_deleted_employee_can_no_longer_log_in(self) -> None:
        app = self._make_app()
        from app.extensions import db
        from app.models.user import User
        from werkzeug.security import generate_password_hash

        with app.app_context():
            db.session.add(User(username="omar", full_name="Omar Sales", role="sales",
                                 is_active=True, password_hash=generate_password_hash("RealPassword123!")))
            db.session.commit()
            omar_id = User.query.filter_by(username="omar").first().id

        with app.test_client() as client:
            self._login_as_admin(client)
            csrf = _csrf_from_session(client)
            client.post(f"/admin/users/{omar_id}/delete", data={"csrf_token": csrf})

        with app.test_client() as employee_client:
            employee_client.get("/login")
            login_csrf = _csrf_from_session(employee_client)
            response = employee_client.post(
                "/login",
                data={"csrf_token": login_csrf, "username": "omar", "password": "RealPassword123!"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Invalid username or password.", response.data)


if __name__ == "__main__":
    unittest.main()
