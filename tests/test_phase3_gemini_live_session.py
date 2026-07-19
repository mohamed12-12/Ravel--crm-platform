from __future__ import annotations

import os
import shutil
import sqlite3
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from services.ai_agent.ai_agent_app.agent.gemini_agent import GeminiAgent
from services.ai_agent.ai_agent_app.agent.read_only_tools import ReadOnlyCRMTools

from test_phase11_demo_features import _make_app_with_db
from test_phase2_gemini_tool_loop import (
    FakeCRMService,
    LoopProviderStub,
    function_call_response,
    text_response,
)


class DummyGeminiSessionAgent(GeminiAgent):
    def __init__(self, responses: list[dict] | None = None) -> None:
        self.responses = responses or []
        self.calls: list[dict] = []

    def respond(
        self,
        *,
        user_message: str,
        session_context: dict | None = None,
        conversation_history: list[dict] | None = None,
    ) -> dict:
        self.calls.append(
            {
                "user_message": user_message,
                "session_context": dict(session_context or {}),
                "conversation_history": list(conversation_history or []),
            }
        )
        if self.responses:
            index = min(len(self.calls) - 1, len(self.responses) - 1)
            return dict(self.responses[index])
        return {
            "reply": f"Gemini handled: {user_message}",
            "tool_requests": [],
            "mode": "gemini",
        }


class ContextAwareGeminiSessionAgent(DummyGeminiSessionAgent):
    def respond(
        self,
        *,
        user_message: str,
        session_context: dict | None = None,
        conversation_history: list[dict] | None = None,
    ) -> dict:
        context = dict(session_context or {})
        self.calls.append(
            {
                "user_message": user_message,
                "session_context": context,
                "conversation_history": list(conversation_history or []),
            }
        )
        trip_type = str(context.get("candidate_trip_type") or "").strip().lower()
        lowered = user_message.lower()
        if context.get("candidate_language_question"):
            return {
                "reply": "أيوه، أقدر أساعدك بالعربي. تحب رحلة داخل مصر ولا رحلة خارجية؟",
                "tool_requests": [],
                "mode": "gemini",
            }
        if context.get("candidate_requires_whatsapp_for_crm") and not context.get("raw_phone"):
            return {
                "reply": "Please send your WhatsApp number so I can safely check or link your Rahma CRM record.",
                "tool_requests": [],
                "mode": "gemini",
            }
        if trip_type == "local":
            return {"reply": "Sure, local trips work. Around what date are you thinking, and how many travelers?", "tool_requests": [], "mode": "gemini"}
        if trip_type == "international":
            return {"reply": "Understood, international travel. What date do you prefer, and do you already have a passport attachment ready?", "tool_requests": [], "mode": "gemini"}
        if context.get("candidate_trip_search"):
            return {
                "reply": "Sure. Do you prefer a local or international trip, and around what date?",
                "tool_requests": [],
                "mode": "gemini",
            }
        if context.get("candidate_human_request"):
            return {"reply": "I can hand this over to a human agent for you.", "tool_requests": [], "mode": "gemini"}
        if lowered in {"hi", "hello", "hey"}:
            return {"reply": "Hi, happy to help. Are you thinking about a local or international trip?", "tool_requests": [], "mode": "gemini"}
        return {"reply": "Can you clarify what kind of trip you want?", "tool_requests": [], "mode": "gemini"}


class ExactConversationGeminiSessionAgent(DummyGeminiSessionAgent):
    def respond(
        self,
        *,
        user_message: str,
        session_context: dict | None = None,
        conversation_history: list[dict] | None = None,
    ) -> dict:
        context = dict(session_context or {})
        self.calls.append(
            {
                "user_message": user_message,
                "session_context": context,
                "conversation_history": list(conversation_history or []),
            }
        )
        lowered = user_message.strip().lower()
        known_traveler = context.get("known_traveler") if isinstance(context.get("known_traveler"), dict) else {}
        if lowered in {"هلا", "مرحبا", "السلام عليكم"}:
            return {
                "reply": "هلا بيك. أقدر أساعدك بالعربي. تحب رحلة محلية ولا دولية؟",
                "tool_requests": [],
                "mode": "gemini",
            }
        if lowered == "لا":
            return {
                "reply": "تمام، ولا يهمك. لو حابب نكمل، قولي تحب رحلة محلية ولا دولية.",
                "tool_requests": [],
                "mode": "gemini",
            }
        if "احجز" in user_message:
            return {
                "reply": "أكيد. قبل ما أجهز الحجز، تحب رحلة محلية ولا دولية؟",
                "tool_requests": [],
                "mode": "gemini",
            }
        if context.get("candidate_trip_type") == "international":
            return {
                "reply": "تمام، رحلة دولية. تحب السفر تقريبًا إمتى؟ ومعاك كام شخص؟",
                "tool_requests": [],
                "mode": "gemini",
            }
        if context.get("raw_phone") and known_traveler and lowered.isdigit():
            return {
                "reply": (
                    f"أهلا {known_traveler.get('full_name') or 'بحضرتك'}. "
                    f"لقيت ملفك في Rahma CRM برقم {known_traveler.get('traveler_id') or ''} "
                    f"وحالتك {known_traveler.get('status') or 'Active'}. "
                    "تحب رحلة محلية ولا دولية؟"
                ),
                "tool_requests": [],
                "mode": "gemini",
            }
        return {
            "reply": "ممكن توضّح لي نوع الرحلة أو التاريخ المناسب لك؟",
            "tool_requests": [],
            "mode": "gemini",
        }


class WritableFakeCRMService(FakeCRMService):
    def __init__(self, connection: sqlite3.Connection) -> None:
        super().__init__(connection)
        self.handoffs: list[dict] = []
        self.bookings: list[dict] = []

    def create_handoff_case(self, **kwargs):
        handoff = {
            "handoff_id": "H-00000001",
            "lead_id": kwargs.get("lead_id", ""),
            "traveler_id": kwargs.get("traveler_id", ""),
            "trip_id": kwargs.get("trip_id", ""),
            "reason_code": kwargs.get("reason_code", "manual_handoff"),
            "reason_text": kwargs.get("reason_text", "Manual handoff requested."),
            "priority": kwargs.get("priority", "High"),
            "status": "Pending",
            "package": {},
            "event_id": "",
        }
        self.handoffs.append(handoff)
        return handoff

    def create_booking_draft(self, **kwargs):
        booking = {
            "booking_id": "B-00000001",
            "trip_id": kwargs.get("trip_id", ""),
            "trip_name": "Sinai Trek",
            "traveler_id": kwargs.get("traveler_id", ""),
            "traveler_name": kwargs.get("traveler_name", ""),
            "room_type": kwargs.get("room_type", ""),
            "flight_option": kwargs.get("flight_option", ""),
            "booking_status": "Draft",
            "payment_status": "Pending",
            "passport_required": 0,
            "passport_status": "",
            "write_result": {"booking_draft": {"booking_id": "B-00000001"}},
        }
        self.bookings.append(booking)
        return booking


class TestPhase3GeminiLiveSession(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(".tmp-test-phase3-gemini") / uuid.uuid4().hex
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.original_env = dict(os.environ)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.original_env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _enable_gemini(app, agent: GeminiAgent) -> None:
        app.config["AI_AGENT_MODE"] = "gemini"
        app.config["SESSIONS"].conversation_ai = agent

    def test_deterministic_mode_still_uses_old_flow(self) -> None:
        client, _app = _make_app_with_db(self.tmp)
        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "01012345678"},
        ).get_json()["session"]

        self.assertEqual(session["stage"], "awaiting_intake")
        self.assertEqual(session["agentMode"], "deterministic")
        self.assertEqual(session["toolsUsed"], [])
        self.assertFalse(session["fallbackUsed"])

    def test_gemini_mode_routes_through_gemini_agent(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        agent = DummyGeminiSessionAgent(
            responses=[
                {
                    "reply": "Hello from Gemini mode.",
                    "tool_requests": [],
                    "mode": "gemini",
                }
            ]
        )
        self._enable_gemini(app, agent)

        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "hello"},
        ).get_json()["session"]

        self.assertEqual(session["stage"], "gemini_conversation")
        self.assertEqual(session["messages"][-1]["text"], "Hello from Gemini mode.")
        self.assertEqual(session["agentMode"], "gemini")
        self.assertEqual(session["toolsUsed"], [])
        self.assertFalse(session["fallbackUsed"])
        self.assertEqual(agent.calls[0]["user_message"], "hello")

    def test_gemini_trip_media_tool_result_renders_media_instead_of_url_text(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        media_url = "/trips/media/d00c220b-0110-4d78-a4b3-afc61a41bdd2"
        agent = DummyGeminiSessionAgent(
            responses=[
                {
                    "reply": f"Here is the official picture for the trip:\n\n{media_url}",
                    "tool_requests": [
                        {
                            "name": "get_trip_media",
                            "input": {"trip_id": "DEMO03"},
                            "result": {
                                "status": "found",
                                "media": [
                                    {
                                        "public_url": media_url,
                                        "url": media_url,
                                        "image_type": "cover",
                                        "alt_text": "Official trip cover",
                                    }
                                ],
                            },
                        }
                    ],
                    "mode": "gemini",
                }
            ]
        )
        self._enable_gemini(app, agent)

        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "give me pic of trip"},
        ).get_json()["session"]

        assistant_message = session["messages"][-1]
        self.assertNotIn("/trips/media/", assistant_message["text"])
        self.assertEqual(assistant_message["media"][0]["public_url"], media_url)
        self.assertEqual(session["toolsUsed"], ["get_trip_media"])

    def test_gemini_session_greeting_does_not_force_phone(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        self._enable_gemini(app, ContextAwareGeminiSessionAgent())

        session = client.post("/api/session", json={}).get_json()["session"]

        opening = session["messages"][0]["text"].lower()
        self.assertEqual(session["agentMode"], "gemini")
        self.assertIn("travel assistant", opening)
        self.assertNotIn("whatsapp", opening)
        self.assertNotIn("profile", opening)

    def test_gemini_mode_greeting_without_phone(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        agent = ContextAwareGeminiSessionAgent()
        self._enable_gemini(app, agent)

        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "hi"},
        ).get_json()["session"]

        reply = session["messages"][-1]["text"].lower()
        self.assertIn("local or international", reply)
        self.assertNotIn("whatsapp", reply)

    def test_gemini_mode_arabic_language_question_without_phone(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        agent = ContextAwareGeminiSessionAgent()
        self._enable_gemini(app, agent)

        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "بتحكي عربي؟"},
        ).get_json()["session"]

        reply = session["messages"][-1]["text"]
        self.assertIn("أيوه", reply)
        self.assertNotIn("WhatsApp", reply)

    def test_gemini_mode_trip_search_before_phone_asks_trip_type(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        agent = ContextAwareGeminiSessionAgent()
        self._enable_gemini(app, agent)

        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "what trips do you have?"},
        ).get_json()["session"]

        reply = session["messages"][-1]["text"].lower()
        self.assertIn("local or international", reply)
        self.assertNotIn("whatsapp", reply)
        self.assertTrue(agent.calls[-1]["session_context"]["candidate_trip_search"])

    def test_gemini_mode_requests_whatsapp_only_for_crm_action(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        agent = ContextAwareGeminiSessionAgent()
        self._enable_gemini(app, agent)

        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "check my traveler profile"},
        ).get_json()["session"]

        reply = session["messages"][-1]["text"].lower()
        self.assertIn("whatsapp", reply)
        self.assertTrue(agent.calls[-1]["session_context"]["candidate_requires_whatsapp_for_crm"])
        self.assertEqual(agent.calls[-1]["session_context"]["stage"], "gemini_conversation")

    def test_gemini_mode_exact_arabic_conversation_avoids_deterministic_leakage(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        db_path = Path(os.environ["RAHMA_SYSTEM_DB_PATH"])
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            INSERT INTO travelers (
                traveler_id, status, full_name, local_trips_count, international_trips_count, total_trips,
                phone_code, whatsapp_raw, integrated_whatsapp, normalized_whatsapp, phone_lookup_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "TR00585",
                "VIP",
                "Mina Andrawes",
                1,
                2,
                3,
                "20",
                "01012345678",
                "+201012345678",
                "+201012345678",
                "20:1012345678",
            ),
        )
        conn.commit()
        with patch(
            "services.ai_agent.ai_agent_app.agent.read_only_tools.get_system_service",
            return_value=WritableFakeCRMService(conn),
        ):
            agent = ExactConversationGeminiSessionAgent()
            agent.read_only_tools = ReadOnlyCRMTools(SimpleNamespace(default_country_code="20"))
            self._enable_gemini(app, agent)
            session = client.post("/api/session", json={}).get_json()["session"]
            transcript = []
            for text in ("01012345678", "هلا", "لا", "عايز احجز", "دوليه", "2"):
                session = client.post(
                    f"/api/session/{session['id']}/message",
                    json={"text": text},
                ).get_json()["session"]
                transcript.append(session["messages"][-1]["text"])

        full_log = "\n".join(transcript)
        self.assertIn("Mina Andrawes", transcript[0])
        self.assertIn("TR00585", transcript[0])
        self.assertIn("VIP", transcript[0])
        self.assertIn("هلا بيك", transcript[1])
        self.assertIn("ولا يهمك", transcript[2])
        self.assertIn("تحب رحلة محلية ولا دولية", transcript[3])
        self.assertIn("رحلة دولية", transcript[4])
        self.assertIn("رحلة دولية", transcript[5])
        self.assertNotIn("{", full_log)
        self.assertNotIn("}", full_log)
        self.assertNotIn("Please share your WhatsApp", full_log)
        self.assertNotIn("reply with 1 or local", full_log.lower())
        self.assertNotIn("previous step", full_log.lower())
        self.assertNotIn("I'm back on the previous step", full_log)
        self.assertEqual(agent.calls[0]["session_context"]["known_traveler"]["full_name"], "Mina Andrawes")
        self.assertEqual(agent.calls[0]["session_context"]["known_traveler"]["traveler_id"], "TR00585")
        self.assertTrue(agent.calls[0]["session_context"]["known_traveler"]["vip_status"])
        conn.close()

    def test_gemini_mode_blocks_deterministic_intake_route(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        self._enable_gemini(app, ContextAwareGeminiSessionAgent())

        session = client.post("/api/session", json={}).get_json()["session"]
        response = client.post(
            f"/api/session/{session['id']}/intake",
            json={
                "fullName": "Test User",
                "birthday": "1990-01-01",
                "gender": "Male",
                "nationality": "Egyptian",
                "rawPhone": "01012345678",
            },
        )

        payload = response.get_json()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(payload["error"], "gemini_mode_chat_owned")

    def test_gemini_mode_blocks_deterministic_booking_route(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        self._enable_gemini(app, ContextAwareGeminiSessionAgent())

        session = client.post("/api/session", json={}).get_json()["session"]
        response = client.post(
            f"/api/session/{session['id']}/book",
            json={"tripId": "RT-LOC-26-001", "roomType": "Double"},
        )

        payload = response.get_json()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(payload["error"], "gemini_mode_chat_owned")

    def test_gemini_mode_can_answer_traveler_lookup(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        db_path = Path(os.environ["RAHMA_SYSTEM_DB_PATH"])
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            INSERT INTO travelers (
                traveler_id, status, full_name, phone_code,
                whatsapp_raw, integrated_whatsapp, normalized_whatsapp, phone_lookup_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "TR00088",
                "VIP",
                "Mona Ali",
                "20",
                "1112223333",
                "+201112223333",
                "+201112223333",
                "20:1112223333",
            ),
        )
        conn.commit()
        with patch(
            "services.ai_agent.ai_agent_app.agent.read_only_tools.get_system_service",
            return_value=WritableFakeCRMService(conn),
        ):
            session = client.post("/api/session", json={}).get_json()["session"]
            agent = GeminiAgent(
                settings=SimpleNamespace(
                    ai_agent_mode="gemini",
                    default_country_code="20",
                    ai_agent_system_prompt="You are Rahvel Agent.",
                ),
                provider=LoopProviderStub(
                    [
                        function_call_response(
                            "search_traveler",
                            {"raw_phone": "1112223333", "country_code": "20"},
                        ),
                        text_response("I found Mona Ali in Rahma CRM with traveler ID TR00088."),
                    ]
                ),
            )
            self._enable_gemini(app, agent)
            session = client.post(
                f"/api/session/{session['id']}/message",
                json={"text": "Check traveler 1112223333"},
            ).get_json()["session"]

        self.assertIn("Mona Ali", session["messages"][-1]["text"])
        self.assertEqual(session["agentMode"], "gemini")
        self.assertEqual(session["toolsUsed"], ["search_traveler"])
        self.assertFalse(session["fallbackUsed"])
        conn.close()

    def test_gemini_mode_can_answer_trip_lookup(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        conn = sqlite3.connect(str(Path(os.environ["RAHMA_SYSTEM_DB_PATH"])))
        with patch(
            "services.ai_agent.ai_agent_app.agent.read_only_tools.get_system_service",
            return_value=WritableFakeCRMService(conn),
        ):
            session = client.post("/api/session", json={}).get_json()["session"]
            agent = GeminiAgent(
                settings=SimpleNamespace(
                    ai_agent_mode="gemini",
                    default_country_code="20",
                    ai_agent_system_prompt="You are Rahvel Agent.",
                ),
                provider=LoopProviderStub(
                    [
                        function_call_response(
                            "search_trips",
                            {"trip_type": "local", "query": "Sinai"},
                        ),
                        text_response("The available local option is Sinai Trek."),
                    ]
                ),
            )
            self._enable_gemini(app, agent)
            session = client.post(
                f"/api/session/{session['id']}/message",
                json={"text": "Show me local Sinai trips"},
            ).get_json()["session"]

        self.assertIn("Sinai Trek", session["messages"][-1]["text"])
        self.assertEqual(session["toolsUsed"], ["search_trips"])
        conn.close()

    def test_gemini_failure_falls_back_safely(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        agent = DummyGeminiSessionAgent(
            responses=[
                {
                    "reply": "Automatic CRM writes are disabled in this phase. I can only validate whether the action is allowed.",
                    "tool_requests": [],
                    "mode": "gemini",
                    "error": "provider timeout",
                }
            ]
        )
        self._enable_gemini(app, agent)

        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "hello"},
        ).get_json()["session"]

        self.assertTrue(session["fallbackUsed"])
        self.assertIn("try again", session["messages"][-1]["text"].lower())

    def test_write_request_rejected_in_live_session(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        agent = DummyGeminiSessionAgent(
            responses=[
                {
                    "reply": "Automatic CRM writes are disabled in this phase. I can only validate whether the action is allowed.",
                    "tool_requests": [],
                    "mode": "gemini",
                    "error": "write_request_rejected",
                }
            ]
        )
        self._enable_gemini(app, agent)

        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "create lead for me"},
        ).get_json()["session"]

        self.assertEqual(
            session["messages"][-1]["text"],
            "Automatic CRM writes are disabled in this phase. I can only validate whether the action is allowed.",
        )
        self.assertFalse(session["fallbackUsed"])
        self.assertEqual(session["toolsUsed"], [])

    def test_session_memory_passed_to_gemini(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        agent = DummyGeminiSessionAgent(
            responses=[
                {"reply": "First Gemini reply.", "tool_requests": [], "mode": "gemini"},
                {"reply": "Second Gemini reply.", "tool_requests": [], "mode": "gemini"},
            ]
        )
        self._enable_gemini(app, agent)

        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "hello"},
        ).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "what did I say?"},
        ).get_json()["session"]

        second_call = agent.calls[1]
        self.assertGreaterEqual(len(second_call["conversation_history"]), 3)
        self.assertEqual(second_call["conversation_history"][-1]["text"], "First Gemini reply.")
        self.assertEqual(session["messages"][-1]["text"], "Second Gemini reply.")

    def test_arabic_and_english_responses_work(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        agent = DummyGeminiSessionAgent()

        def respond_for_language(*, user_message: str, session_context=None, conversation_history=None):
            agent.calls.append(
                {
                    "user_message": user_message,
                    "session_context": dict(session_context or {}),
                    "conversation_history": list(conversation_history or []),
                }
            )
            if str((session_context or {}).get("language") or "").startswith("ar"):
                return {"reply": "أهلاً بك، كيف أساعدك اليوم؟", "tool_requests": [], "mode": "gemini"}
            return {"reply": "Hello, how can I help you today?", "tool_requests": [], "mode": "gemini"}

        agent.respond = respond_for_language  # type: ignore[method-assign]
        self._enable_gemini(app, agent)

        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "مرحبا"},
        ).get_json()["session"]
        self.assertEqual(session["messages"][-1]["text"], "أهلاً بك، كيف أساعدك اليوم؟")

        second = client.post("/api/session", json={}).get_json()["session"]
        second = client.post(
            f"/api/session/{second['id']}/message",
            json={"text": "hello"},
        ).get_json()["session"]
        self.assertEqual(second["messages"][-1]["text"], "Hello, how can I help you today?")

    def test_gemini_mode_accepts_natural_local_trip_intent(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        agent = ContextAwareGeminiSessionAgent()
        self._enable_gemini(app, agent)

        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "i need local"},
        ).get_json()["session"]

        self.assertIn("local trips", session["messages"][-1]["text"])
        self.assertNotIn("WhatsApp", session["messages"][-1]["text"])
        self.assertEqual(agent.calls[-1]["session_context"]["candidate_trip_type"], "local")

    def test_gemini_mode_accepts_arabic_local_trip_intent(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        agent = ContextAwareGeminiSessionAgent()
        self._enable_gemini(app, agent)

        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "عايز رحلة داخلية"},
        ).get_json()["session"]

        self.assertIn("local trips", session["messages"][-1]["text"])
        self.assertEqual(agent.calls[-1]["session_context"]["candidate_trip_type"], "local")

    def test_gemini_mode_accepts_english_international_trip_intent(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        agent = ContextAwareGeminiSessionAgent()
        self._enable_gemini(app, agent)

        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "international trip"},
        ).get_json()["session"]

        self.assertIn("international travel", session["messages"][-1]["text"])
        self.assertEqual(agent.calls[-1]["session_context"]["candidate_trip_type"], "international")

    def test_gemini_session_memory_stores_collected_trip_preference(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        agent = ContextAwareGeminiSessionAgent()
        self._enable_gemini(app, agent)

        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "I need a local trip next month"},
        ).get_json()["session"]

        self.assertEqual(session["tripType"], "local")
        self.assertIn("next month", session["preferredDate"].lower())
        self.assertEqual(agent.calls[-1]["session_context"]["trip_type"], "local")

    def test_gemini_mode_does_not_repeat_fixed_phone_sentence(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        agent = ContextAwareGeminiSessionAgent()
        self._enable_gemini(app, agent)

        session = client.post("/api/session", json={}).get_json()["session"]
        first_opening = session["messages"][0]["text"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "hi"},
        ).get_json()["session"]
        first_reply = session["messages"][-1]["text"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "why?"},
        ).get_json()["session"]
        second_reply = session["messages"][-1]["text"]

        fixed = "Please share your WhatsApp number so I can check your profile safely."
        self.assertNotIn(fixed, first_opening)
        self.assertNotIn(fixed, first_reply)
        self.assertNotIn(fixed, second_reply)
        self.assertNotEqual(first_reply, second_reply)

    def test_gemini_mode_unknown_input_gets_clarification(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        agent = ContextAwareGeminiSessionAgent()
        self._enable_gemini(app, agent)

        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "maybe something nice"},
        ).get_json()["session"]

        self.assertIn("clarify", session["messages"][-1]["text"].lower())
        self.assertFalse(session["fallbackUsed"])

    def test_template_variables_never_appear_in_gemini_reply(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        agent = DummyGeminiSessionAgent(
            responses=[
                {
                    "reply": "I found {full_name} with traveler ID {traveler_id}.",
                    "tool_requests": [],
                    "mode": "gemini",
                }
            ]
        )
        self._enable_gemini(app, agent)

        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "check my profile"},
        ).get_json()["session"]

        reply = session["messages"][-1]["text"]
        self.assertNotIn("{full_name}", reply)
        self.assertNotIn("{traveler_id}", reply)
        self.assertNotIn("{", reply)
        self.assertNotIn("}", reply)

    def test_gemini_mode_still_accepts_numeric_choice_message(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        agent = DummyGeminiSessionAgent(
            responses=[
                {"reply": "Here are available local trips.", "tool_requests": [], "mode": "gemini"},
                {"reply": "Choice accepted. I will continue with that option.", "tool_requests": [], "mode": "gemini"},
            ]
        )
        self._enable_gemini(app, agent)

        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "show me local trips"},
        ).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "1"},
        ).get_json()["session"]

        self.assertEqual(session["messages"][-1]["text"], "Choice accepted. I will continue with that option.")
        self.assertFalse(session["fallbackUsed"])

    def test_gemini_mode_human_request_can_create_handoff(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        db_path = Path(os.environ["RAHMA_SYSTEM_DB_PATH"])
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            INSERT INTO travelers (
                traveler_id, status, full_name, phone_code,
                whatsapp_raw, integrated_whatsapp, normalized_whatsapp, phone_lookup_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "TR00999",
                "Active",
                "Handoff Traveler",
                "20",
                "1999888777",
                "+201999888777",
                "+201999888777",
                "20:1999888777",
            ),
        )
        conn.commit()
        with patch(
            "services.ai_agent.ai_agent_app.agent.read_only_tools.get_system_service",
            return_value=WritableFakeCRMService(conn),
        ):
            agent = GeminiAgent(
                settings=SimpleNamespace(
                    ai_agent_mode="gemini",
                    default_country_code="20",
                    ai_agent_system_prompt="You are Rahvel Agent.",
                ),
                provider=LoopProviderStub(
                    [
                        function_call_response(
                            "create_handoff",
                            {
                                "traveler_id": "TR00999",
                                "user_requested_human": True,
                                "reason_text": "Traveler asked for a human agent.",
                            },
                        ),
                        text_response("I created a human handoff for you."),
                    ]
                ),
                write_tools_enabled=True,
            )
            self._enable_gemini(app, agent)
            session = client.post("/api/session", json={}).get_json()["session"]
            session = client.post(
                f"/api/session/{session['id']}/message",
                json={"text": "I want human agent"},
            ).get_json()["session"]

        self.assertEqual(session["handoffState"], "handed_off")
        self.assertEqual(session["stage"], "handed_off")
        self.assertIn("handoff", session["messages"][-1]["text"].lower())
        conn.close()

    def test_gemini_mode_booking_write_still_requires_validator_approval(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        db_path = Path(os.environ["RAHMA_SYSTEM_DB_PATH"])
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            INSERT INTO travelers (
                traveler_id, status, full_name, phone_code,
                whatsapp_raw, integrated_whatsapp, normalized_whatsapp, phone_lookup_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "TR00888",
                "Active",
                "Booking Traveler",
                "20",
                "1888777666",
                "+201888777666",
                "+201888777666",
                "20:1888777666",
            ),
        )
        conn.commit()
        with patch(
            "services.ai_agent.ai_agent_app.agent.read_only_tools.get_system_service",
            return_value=WritableFakeCRMService(conn),
        ):
            agent = GeminiAgent(
                settings=SimpleNamespace(
                    ai_agent_mode="gemini",
                    default_country_code="20",
                    ai_agent_system_prompt="You are Rahvel Agent.",
                ),
                provider=LoopProviderStub(
                    [
                        function_call_response(
                            "create_booking_draft",
                            {
                                "traveler_id": "TR00888",
                                "trip_id": "RT-LOC-26-001",
                                "flight_option": "Without Flight",
                            },
                        ),
                        text_response(""),
                    ]
                ),
                write_tools_enabled=True,
            )
            self._enable_gemini(app, agent)
            session = client.post("/api/session", json={}).get_json()["session"]
            session = client.post(
                f"/api/session/{session['id']}/message",
                json={"text": "book this trip for me"},
            ).get_json()["session"]

        self.assertIn("room type", session["messages"][-1]["text"].lower())
        self.assertNotEqual(session["stage"], "completed")
        conn.close()

    def test_gemini_mode_controlled_write_waits_for_whatsapp_identity(self) -> None:
        client, app = _make_app_with_db(self.tmp)
        conn = sqlite3.connect(str(Path(os.environ["RAHMA_SYSTEM_DB_PATH"])))
        with patch(
            "services.ai_agent.ai_agent_app.agent.read_only_tools.get_system_service",
            return_value=WritableFakeCRMService(conn),
        ):
            agent = GeminiAgent(
                settings=SimpleNamespace(
                    ai_agent_mode="gemini",
                    default_country_code="20",
                    ai_agent_system_prompt="You are Rahvel Agent.",
                ),
                provider=LoopProviderStub(
                    [
                        function_call_response(
                            "create_lead",
                            {
                                "customer_name": "Mina Samir",
                                "preferred_trip_type": "local",
                            },
                        ),
                        text_response(""),
                    ]
                ),
                write_tools_enabled=True,
            )
            self._enable_gemini(app, agent)
            session = client.post("/api/session", json={}).get_json()["session"]
            session = client.post(
                f"/api/session/{session['id']}/message",
                json={"text": "create a lead for Mina Samir interested in local trips"},
            ).get_json()["session"]

        reply = session["messages"][-1]["text"].lower()
        self.assertIn("whatsapp", reply)
        self.assertIn("traveler id", reply)
        self.assertNotEqual(session["leadStatus"], "lead_created")
        conn.close()


if __name__ == "__main__":
    unittest.main()
