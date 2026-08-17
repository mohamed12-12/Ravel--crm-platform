"""Database passwords must never leave the process.

Production runs against a managed Postgres whose connection string carries a
real credential. /admin/db-health returned that string verbatim in JSON, and
the operational scripts printed it to stdout -- so the password reached
browser caches, proxy logs, terminal scrollback, and every screenshot or
pasted ticket containing them.

A leak like this is silent: nothing fails, the output just quietly contains a
password. That makes it easy to reintroduce, which is why it is pinned here.
"""
from __future__ import annotations

import os
import shutil
import sys
import unittest
import uuid
from pathlib import Path

SYSTEM_ROOT = Path(__file__).resolve().parent.parent / "apps" / "api"
if str(SYSTEM_ROOT) not in sys.path:
    sys.path.insert(0, str(SYSTEM_ROOT))

# Invented, and it has to stay that way: this file is public. A test that
# proves a password is not leaked is a poor place to check a real one in.
SECRET = "not-a-real-password-J8kQ2wVn"
POSTGRES_URI = (
    f"postgresql+psycopg2://crm_app:{SECRET}"
    "@db.example.rds.amazonaws.com:5432/postgres?sslmode=require"
)


def _create_temp_app():
    for module_name in list(sys.modules):
        if module_name == "app" or module_name.startswith("app."):
            sys.modules.pop(module_name, None)
    from app import create_app

    return create_app("development")


class DatabaseCredentialMaskingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(".tmp-test-workdirs") / f"credmask-{uuid.uuid4().hex}"
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.tmpdir / "app.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{self.db_path.resolve().as_posix()}"
        os.environ["RAHMA_SYSTEM_DB_PATH"] = str(self.db_path)
        os.environ["CRM_AUTH_ENABLED"] = "false"
        self.app = _create_temp_app()
        self.app.config["TESTING"] = True
        from app.extensions import db

        self.db = db
        with self.app.app_context():
            self.db.drop_all()
            self.db.create_all()
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("RAHMA_SYSTEM_DB_PATH", None)

    # -- the helper -------------------------------------------------------

    def test_the_password_is_replaced(self) -> None:
        from services.crm.system_services.db_uri import safe_database_uri

        masked = safe_database_uri(POSTGRES_URI)
        self.assertNotIn(SECRET, masked)
        self.assertIn("***", masked)
        # Everything an operator actually needs survives.
        self.assertIn("crm_app", masked)
        self.assertIn("db.example.rds.amazonaws.com", masked)
        self.assertIn("postgresql", masked)

    def test_a_sqlite_path_is_left_readable(self) -> None:
        from services.crm.system_services.db_uri import safe_database_uri

        self.assertIn("app.db", safe_database_uri("sqlite:////srv/data/app.db"))

    def test_an_empty_or_unparseable_value_never_echoes_itself(self) -> None:
        """An unparseable value could be anything, including a bare password,
        so it is never echoed back on the assumption it is harmless."""
        from services.crm.system_services.db_uri import safe_database_uri

        self.assertEqual(safe_database_uri(""), "(unset)")
        self.assertEqual(safe_database_uri(None), "(unset)")
        self.assertNotIn("hunter2", safe_database_uri("::: not a url ::: hunter2"))

    # -- the live endpoint -------------------------------------------------

    def test_db_health_does_not_publish_the_password(self) -> None:
        self.app.config["SQLALCHEMY_DATABASE_URI"] = POSTGRES_URI
        response = self.client.get("/admin/db-health")
        body = response.get_data(as_text=True)
        self.assertNotIn(SECRET, body)
        payload = response.get_json()
        self.assertNotIn(SECRET, str(payload))
        # Still answers the question it exists to answer.
        self.assertIn("db.example.rds.amazonaws.com", payload["sqlalchemyDatabaseUri"])

    def test_db_health_still_reports_a_sqlite_backend_usefully(self) -> None:
        response = self.client.get("/admin/db-health")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "ok")
        self.assertIn("app.db", payload["sqlalchemyDatabaseUri"])
        self.assertIn("travelers", payload["counts"])

    # -- the operational scripts -------------------------------------------

    def test_the_scripts_mask_before_printing(self) -> None:
        """Each script prints the database it opened so a run against the
        wrong one is obvious. That line must not carry the credential."""
        import importlib

        scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))

        for module_name in ("manage_migrations", "backfill_booking_ledger"):
            with self.subTest(script=module_name):
                module = importlib.import_module(module_name)
                importlib.reload(module)
                self.assertNotIn(SECRET, module.safe_database_uri(POSTGRES_URI))

    def test_no_script_prints_a_raw_connection_string(self) -> None:
        """Guards the pattern rather than one instance: printing the URI
        straight from config is what caused this."""
        scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
        offenders = []
        for path in scripts_dir.glob("*.py"):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                stripped = line.strip()
                if not stripped.startswith(("print(", 'print(f"', "#")) and "print(" not in stripped:
                    continue
                if "SQLALCHEMY_DATABASE_URI" in stripped and "safe_database_uri" not in stripped:
                    offenders.append(f"{path.name}:{number}")
        self.assertEqual(offenders, [], f"connection string printed unmasked: {offenders}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
