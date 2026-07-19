from __future__ import annotations

import os
import shutil
import sys
import unittest
import uuid
from pathlib import Path

from services.api_contracts import build_agent_openapi_contract, build_crm_openapi_contract

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from test_phase11_demo_features import _make_app_with_db  # noqa: E402


class TestApiContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(".tmp-test-api-contracts") / uuid.uuid4().hex
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.original_env = dict(os.environ)
        os.environ["AI_AGENT_MODE"] = "tool_calling"

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.original_env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_agent_contract_contains_public_chat_api_paths(self) -> None:
        contract = build_agent_openapi_contract(base_url="http://127.0.0.1:5001")

        self.assertEqual(contract["openapi"], "3.1.0")
        self.assertIn("/api/session", contract["paths"])
        self.assertIn("/api/session/{session_id}/message", contract["paths"])
        self.assertIn("/api/session/{session_id}/passport_attachment", contract["paths"])
        self.assertIn("Session", contract["components"]["schemas"])

    def test_crm_contract_contains_operational_api_paths(self) -> None:
        contract = build_crm_openapi_contract(base_url="http://127.0.0.1:5000")

        self.assertEqual(contract["openapi"], "3.1.0")
        self.assertIn("/api/crm/agent/read", contract["paths"])
        self.assertIn("/api/crm/agent/write", contract["paths"])
        self.assertIn("/bookings/{booking_id}/status", contract["paths"])
        self.assertIn("CrmApiKey", contract["components"]["securitySchemes"])

    def test_agent_app_serves_openapi_contract(self) -> None:
        client, _app = _make_app_with_db(self.tmp)

        response = client.get("/api/openapi.json")
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["info"]["title"], "Rahma Traveler AI Sales Agent API")
        self.assertIn("/api/session/{session_id}/message", payload["paths"])

    def test_crm_app_serves_openapi_contract(self) -> None:
        os.environ["DATABASE_URL"] = f"sqlite:///{(self.tmp / 'crm.db').resolve()}"
        os.environ["CRM_AUTH_ENABLED"] = "false"
        from app import create_app

        app = create_app("development")
        app.config.update(TESTING=True, CRM_AUTH_ENABLED=False)
        client = app.test_client()

        response = client.get("/api/openapi.json")
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["info"]["title"], "Rahma Traveler CRM API")
        self.assertIn("/api/crm/agent/read", payload["paths"])


if __name__ == "__main__":
    unittest.main()
