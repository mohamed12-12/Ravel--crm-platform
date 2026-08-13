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


def _create_temp_app():
    for module_name in list(sys.modules):
        if module_name == "app" or module_name.startswith("app."):
            sys.modules.pop(module_name, None)
    from app import create_app
    return create_app("development")


class HandoffDeleteTests(unittest.TestCase):
    """The Handoff Queue board (apps/api/app/templates/admin/handoffs.html)
    only supported Assign/Resolve per card -- an employee had no way to
    remove a stale or duplicate handoff, which also made the column keep
    growing without bound. Adds a Delete action backed by DELETE
    /admin/handoffs/<id>.
    """

    def setUp(self) -> None:
        self.tmpdir = Path(".tmp-test-workdirs") / f"handoff-delete-{uuid.uuid4().hex}"
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.tmpdir / "app.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{self.db_path.resolve().as_posix()}"
        os.environ["RAHMA_SYSTEM_DB_PATH"] = str(self.db_path)
        os.environ["CRM_AUTH_ENABLED"] = "false"
        self.app = _create_temp_app()
        from app.extensions import db
        from app.models.handoff import HandoffQueue

        self.db = db
        self.HandoffQueue = HandoffQueue
        self.app.config["TESTING"] = True
        self.app.config["WTF_CSRF_ENABLED"] = False
        with self.app.app_context():
            self.db.drop_all()
            self.db.create_all()
            self.db.session.add(self.HandoffQueue(
                handoff_id="H-DEL001",
                traveler_id=None,
                reason="The traveler explicitly requested a human agent.",
                priority="High",
                channel="WhatsApp",
                status="Pending",
            ))
            self.db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("RAHMA_SYSTEM_DB_PATH", None)
        os.environ.pop("CRM_AUTH_ENABLED", None)

    def test_delete_route_removes_the_handoff(self) -> None:
        response = self.client.delete("/admin/handoffs/H-DEL001")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "success"})
        with self.app.app_context():
            self.assertIsNone(self.db.session.get(self.HandoffQueue, "H-DEL001"))

    def test_delete_route_404s_for_unknown_id(self) -> None:
        response = self.client.delete("/admin/handoffs/H-DOES-NOT-EXIST")
        self.assertEqual(response.status_code, 404)

    def test_handoffs_board_renders_a_delete_button_per_card(self) -> None:
        response = self.client.get("/admin/handoffs/")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("deleteHandoff('H-DEL001')", body)
        self.assertIn("function deleteHandoff(", body)


if __name__ == "__main__":
    unittest.main()
