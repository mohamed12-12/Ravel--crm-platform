from __future__ import annotations

import json
import os
import shutil
import sys
import unittest
import uuid
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parent.parent
API_ROOT = ROOT / "apps" / "api"


def _reset_app_modules() -> None:
    for module_name in list(sys.modules):
        if module_name == "app" or module_name.startswith("app."):
            sys.modules.pop(module_name, None)


class Phase7ConversationViewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = ROOT / ".tmp-test-workdirs" / f"phase7-conversations-{uuid.uuid4().hex}"
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.tmpdir / "app.db"
        self.original_env = dict(os.environ)
        os.environ["CRM_AUTH_ENABLED"] = "false"
        os.environ["DATABASE_URL"] = f"sqlite:///{self.db_path.resolve().as_posix()}"
        os.environ["RAHMA_SYSTEM_DB_PATH"] = str(self.db_path)
        os.environ["SHEET_BACKEND"] = "excel"
        os.environ["EXCEL_SOURCE_WORKBOOK"] = str(ROOT / "tests" / "fixtures" / "operational_source_workbook.xlsx")
        os.environ["EXCEL_RUNTIME_WORKBOOK"] = str(self.tmpdir / "runtime.xlsx")
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        if str(API_ROOT) not in sys.path:
            sys.path.insert(0, str(API_ROOT))

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.original_env)
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        _reset_app_modules()

    def _app_with_session(self):
        _reset_app_modules()
        from app import create_app
        from app.extensions import db
        from app.models.lead import Lead
        from app.models.traveler import Traveler

        app = create_app("development")
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        with app.app_context():
            db.drop_all()
            db.create_all()
            db.session.add(Traveler(traveler_id="TR77777", full_name="Transcript Traveler", whatsapp_raw="201000000000"))
            db.session.add(
                Lead(
                    lead_id="LD77777",
                    traveler_id="TR77777",
                    customer_name="Transcript Traveler",
                    lead_stage="New Lead",
                    priority="Medium",
                )
            )
            db.session.execute(
                text(
                    """
                    CREATE TABLE ai_agent_sessions (
                        session_id VARCHAR(64) PRIMARY KEY,
                        schema_version INTEGER NOT NULL DEFAULT 1,
                        payload TEXT NOT NULL,
                        agent_state TEXT,
                        version INTEGER NOT NULL DEFAULT 0,
                        created_at TIMESTAMP,
                        updated_at TIMESTAMP,
                        locked_until TIMESTAMP,
                        lock_owner VARCHAR(80),
                        last_message_key VARCHAR(160),
                        traveler_id VARCHAR(20),
                        lead_id VARCHAR(50),
                        raw_phone VARCHAR(32)
                    )
                    """
                )
            )
            payload = {
                "id": "sess-phase7",
                "stage": "awaiting_trip_type",
                "traveler_id": "TR77777",
                "lead_id": "LD77777",
                "raw_phone": "201000000000",
                "messages": [
                    {"role": "assistant", "text": "Hi, how can I help?"},
                    {"role": "user", "text": "I want a Dahab trip"},
                ],
            }
            db.session.execute(
                text(
                    """
                    INSERT INTO ai_agent_sessions (
                        session_id, payload, created_at, updated_at, traveler_id, lead_id, raw_phone
                    ) VALUES (
                        :session_id, :payload, :created_at, :updated_at, :traveler_id, :lead_id, :raw_phone
                    )
                    """
                ),
                {
                    "session_id": "sess-phase7",
                    "payload": json.dumps(payload),
                    "created_at": "2026-08-19 09:00:00",
                    "updated_at": "2026-08-19 09:05:00",
                    "traveler_id": "TR77777",
                    "lead_id": "LD77777",
                    "raw_phone": "201000000000",
                },
            )
            db.session.commit()
        return app

    def test_interactions_index_and_session_detail_render_real_transcript_messages(self) -> None:
        app = self._app_with_session()
        with app.test_client() as client:
            index_html = client.get("/interactions/").get_data(as_text=True)
            self.assertIn("sess-phase7", index_html)
            self.assertIn("I want a Dahab trip", index_html)
            self.assertIn("2</strong> turns", index_html)

            detail_html = client.get("/interactions/sessions/sess-phase7").get_data(as_text=True)
            self.assertIn("Hi, how can I help?", detail_html)
            self.assertIn("I want a Dahab trip", detail_html)
            self.assertIn("Traveler", detail_html)
            self.assertIn("Agent", detail_html)

    def test_traveler_and_lead_pages_link_to_conversation_without_phantom_interaction_fields(self) -> None:
        app = self._app_with_session()
        with app.test_client() as client:
            traveler_html = client.get("/travelers/TR77777").get_data(as_text=True)
            self.assertIn("AI Agent Conversations", traveler_html)
            self.assertIn("sess-phase7", traveler_html)
            self.assertNotIn("Message received via WhatsApp.", traveler_html)
            self.assertNotIn("interaction.direction", traveler_html)

            lead_html = client.get("/leads/LD77777").get_data(as_text=True)
            self.assertIn("Agent Conversations", lead_html)
            self.assertIn("sess-phase7", lead_html)
            self.assertNotIn("i.message_text", lead_html)
            self.assertNotIn("i.created_at", lead_html)


if __name__ == "__main__":
    unittest.main()
