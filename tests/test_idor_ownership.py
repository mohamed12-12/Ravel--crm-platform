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


def _fresh_app():
    from app import create_app

    return create_app("development")


class IdorOwnershipTests(unittest.TestCase):
    """Confirms non-admin callers can no longer view records owned by other
    agents by guessing/incrementing an ID (IDOR), and that role can no
    longer be self-declared via the X-CRM-Role header.
    """

    def setUp(self) -> None:
        self.original_env = dict(os.environ)
        self.tmpdir = Path(".tmp-test-workdirs") / f"idor-{uuid.uuid4().hex}"
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.tmpdir / "crm.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{self.db_path.resolve().as_posix()}"
        os.environ["RAHMA_SYSTEM_DB_PATH"] = str(self.db_path)
        os.environ["CRM_AUTH_ENABLED"] = "true"
        self.app = _fresh_app()
        self.app.config.update(
            TESTING=True,
            CRM_AUTH_ENABLED=True,
            SECRET_KEY="test-secret",
            CRM_API_TOKEN="idor-test-token",
        )
        from app.extensions import db
        from app.models.booking import TripBooking
        from app.models.lead import Lead
        from app.models.traveler import Traveler
        from app.models.trip import Trip
        from app.models.user import User

        self.db = db
        self.TripBooking = TripBooking
        self.Lead = Lead
        self.Traveler = Traveler
        self.Trip = Trip
        self.User = User

        with self.app.app_context():
            self.db.drop_all()
            self.db.create_all()

            agent_a = self.User(username="agent-a", full_name="Agent A", password_hash="x", role="agent", is_active=True)
            agent_b = self.User(username="agent-b", full_name="Agent B", password_hash="x", role="agent", is_active=True)
            admin = self.User(username="admin-x", full_name="Admin X", password_hash="x", role="admin", is_active=True)
            self.db.session.add_all([agent_a, agent_b, admin])
            self.db.session.commit()
            self.agent_a_id = agent_a.id
            self.agent_a_username = agent_a.username
            self.agent_b_id = agent_b.id
            self.agent_b_username = agent_b.username
            self.admin_id = admin.id
            self.admin_username = admin.username

            self.db.session.add(self.Traveler(
                traveler_id="TR-IDOR-1", full_name="Owned By B",
                normalized_whatsapp="+201000000001", phone_lookup_key="20:1000000001",
            ))
            self.db.session.add(self.Traveler(
                traveler_id="TR-IDOR-2", full_name="Unclaimed Traveler",
                normalized_whatsapp="+201000000002", phone_lookup_key="20:1000000002",
            ))
            self.db.session.add(self.Trip(
                trip_id="TRIP-IDOR-1", trip_name="IDOR Trip", type="International",
                sales_status="Open", single_total=4, single_remaining=4,
            ))
            self.db.session.add(self.TripBooking(
                booking_id="B-IDOR-B", trip_id="TRIP-IDOR-1", trip_name="IDOR Trip",
                traveler_id="TR-IDOR-1", traveler_name="Owned By B", room_type="Single",
                booking_status="Confirmed", payment_status="Pending", booking_source="Admin",
                passport_required=False, assigned_to_user_id=self.agent_b_id,
            ))
            self.db.session.add(self.TripBooking(
                booking_id="B-IDOR-UNASSIGNED", trip_id="TRIP-IDOR-1", trip_name="IDOR Trip",
                traveler_id="TR-IDOR-2", traveler_name="Unclaimed Traveler", room_type="Single",
                booking_status="Confirmed", payment_status="Pending", booking_source="Admin",
                passport_required=False, assigned_to_user_id=None,
            ))
            self.db.session.add(self.Lead(
                lead_id="L-IDOR-B", customer_name="Owned By B", raw_phone="01000000001",
                traveler_id="TR-IDOR-1", lead_stage="New Lead", assigned_to_user_id=self.agent_b_id,
            ))
            self.db.session.add(self.Lead(
                lead_id="L-IDOR-UNASSIGNED", customer_name="Unclaimed Lead", raw_phone="01000000002",
                traveler_id="TR-IDOR-2", lead_stage="New Lead", assigned_to_user_id=None,
            ))
            self.db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.original_env)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _login(self, *, user_id: int, username: str) -> None:
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["username"] = username
            sess["user_id"] = user_id

    # --- ownership enforcement: bookings ---

    def test_admin_can_view_any_booking(self):
        self._login(user_id=self.admin_id, username=self.admin_username)
        response = self.client.get("/bookings/B-IDOR-B")
        self.assertEqual(response.status_code, 200)

    def test_owning_agent_can_view_their_booking(self):
        self._login(user_id=self.agent_b_id, username=self.agent_b_username)
        response = self.client.get("/bookings/B-IDOR-B")
        self.assertEqual(response.status_code, 200)

    def test_other_agent_is_forbidden_from_viewing_bookings_assigned_elsewhere(self):
        self._login(user_id=self.agent_a_id, username=self.agent_a_username)
        response = self.client.get("/bookings/B-IDOR-B")
        self.assertEqual(response.status_code, 403)

    def test_agent_can_view_unassigned_booking(self):
        self._login(user_id=self.agent_a_id, username=self.agent_a_username)
        response = self.client.get("/bookings/B-IDOR-UNASSIGNED")
        self.assertEqual(response.status_code, 200)

    # --- ownership enforcement: leads ---

    def test_other_agent_is_forbidden_from_viewing_leads_assigned_elsewhere(self):
        self._login(user_id=self.agent_a_id, username=self.agent_a_username)
        response = self.client.get("/leads/L-IDOR-B")
        self.assertEqual(response.status_code, 403)

    def test_owning_agent_can_view_their_lead(self):
        self._login(user_id=self.agent_b_id, username=self.agent_b_username)
        response = self.client.get("/leads/L-IDOR-B")
        self.assertEqual(response.status_code, 200)

    def test_agent_can_view_unassigned_lead(self):
        self._login(user_id=self.agent_a_id, username=self.agent_a_username)
        response = self.client.get("/leads/L-IDOR-UNASSIGNED")
        self.assertEqual(response.status_code, 200)

    # --- ownership enforcement: travelers (derived from their leads) ---

    def test_other_agent_is_forbidden_from_viewing_traveler_owned_via_lead(self):
        self._login(user_id=self.agent_a_id, username=self.agent_a_username)
        response = self.client.get("/travelers/TR-IDOR-1")
        self.assertEqual(response.status_code, 403)

    def test_owning_agent_can_view_traveler_owned_via_lead(self):
        self._login(user_id=self.agent_b_id, username=self.agent_b_username)
        response = self.client.get("/travelers/TR-IDOR-1")
        self.assertEqual(response.status_code, 200)

    def test_agent_can_view_traveler_with_only_unassigned_leads(self):
        self._login(user_id=self.agent_a_id, username=self.agent_a_username)
        response = self.client.get("/travelers/TR-IDOR-2")
        self.assertEqual(response.status_code, 200)

    def test_admin_can_view_any_traveler(self):
        self._login(user_id=self.admin_id, username=self.admin_username)
        response = self.client.get("/travelers/TR-IDOR-1")
        self.assertEqual(response.status_code, 200)

    # --- role can no longer be forged via the client-supplied header ---

    def test_forged_admin_role_header_does_not_grant_admin_access(self):
        response = self.client.post(
            "/api/crm/resolve-identity",
            json={"master_id": "TR-IDOR-1", "alias_ids": ["TR-IDOR-2"]},
            headers={"X-CRM-API-Key": "idor-test-token", "X-CRM-Role": "admin"},
        )
        self.assertEqual(response.status_code, 403)

    def test_forged_admin_role_header_does_not_bypass_booking_ownership(self):
        response = self.client.get(
            "/bookings/B-IDOR-B",
            headers={"X-CRM-API-Key": "idor-test-token", "X-CRM-Role": "admin"},
        )
        self.assertEqual(response.status_code, 403)

    def test_api_token_caller_can_still_view_unassigned_booking(self):
        response = self.client.get(
            "/bookings/B-IDOR-UNASSIGNED",
            headers={"X-CRM-API-Key": "idor-test-token"},
        )
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
