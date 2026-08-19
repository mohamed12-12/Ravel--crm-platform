"""Systematic bilingual coverage for ConversationWorkflowPolicy.evaluate().

Follow-up to the field-capture audit: that audit fixed customer *input*
capture (Arabic answers getting silently rejected). This covers the other
half -- customer *output* -- by walking every reachable required_step with
an Arabic session and asserting the assistant_message is genuinely Arabic,
not an English string that slipped through because some branch forgot the
`if arabic else` split every other branch in this file already uses.

Two real bugs were caught by building this: two `assistant_message` literals
with no Arabic branch at all (private_request_ready,
human_review/private_trip_consultation), and -- independent of language --
the human_review/private_trip_consultation branch also unpacked
`reason="private_trip_consultation"` *and* `**common` (which already carries
"reason"), which raised `TypeError: got multiple values for keyword argument
'reason'` on every single hit, regardless of language. All are fixed in
workflow_policy.py; this file pins both the bilingual behavior and the
non-crash.
"""
from __future__ import annotations

import re

import pytest

from services.ai_agent.ai_agent_app.agent.workflow_policy import ConversationWorkflowPolicy

_ARABIC_RE = re.compile(r"[؀-ۿ]")

_TRIP = {
    "trip_id": "RT-1",
    "trip_name": "Test Trip",
    "type": "International",
    "trip_type": "international",
    "available_single": 5,
    "boys_double": 3,
    "girls_double": 3,
    "boys_triple": 2,
    "girls_triple": 2,
}

_VERIFIED = {
    "workflow": {"lookup_status": "found", "verified_traveler": {"traveler_id": "TR100", "status": "Active"}},
    "known_traveler": {"traveler_id": "TR100", "status": "Active"},
    "raw_phone": "01112223333",
}


def _ctx(**overrides) -> dict:
    return {"language": "ar", **overrides}


SCENARIOS = {
    "collect_whatsapp_number": _ctx(),
    "run_traveler_lookup": _ctx(raw_phone="01112223333"),
    "collect_valid_whatsapp_number": _ctx(workflow={"lookup_status": "invalid_phone"}),
    "human_review:duplicate_phone": _ctx(workflow={"lookup_status": "duplicate"}),
    "human_review:restricted_status": _ctx(
        workflow={"lookup_status": "found", "verified_traveler": {"traveler_id": "TR1", "status": "Blacklisted"}}
    ),
    "collect_trip_type": _ctx(**_VERIFIED),
    "collect_duplicate_lead_choice": _ctx(**_VERIFIED, open_lead_id="LD1"),
    "collect_guardian_name": _ctx(**_VERIFIED, birthday="2015-01-01"),
    "collect_guardian_phone": _ctx(**_VERIFIED, birthday="2015-01-01", guardian_name="Parent Name"),
    "collect_private_service_type": _ctx(**_VERIFIED, private_trip_active=True, trip_type="local"),
    "collect_private_destination": _ctx(
        **_VERIFIED, private_trip_active=True, trip_type="local", private_service_type="full_package"
    ),
    "collect_private_dates": _ctx(
        **_VERIFIED, private_trip_active=True, trip_type="local",
        private_service_type="full_package", private_destination="Sharm",
    ),
    "collect_private_party_size": _ctx(
        **_VERIFIED, private_trip_active=True, trip_type="local",
        private_service_type="full_package", private_destination="Sharm", private_dates_flexible=True,
    ),
    "collect_private_budget": _ctx(
        **_VERIFIED, private_trip_active=True, trip_type="local",
        private_service_type="full_package", private_destination="Sharm",
        private_dates_flexible=True, private_party_size=2,
    ),
    "create_private_trip_request": _ctx(
        **_VERIFIED, private_trip_active=True, trip_type="local",
        private_service_type="full_package", private_destination="Sharm",
        private_dates_flexible=True, private_party_size=2, private_budget_currency="EGP",
    ),
    "human_review:private_trip_consultation": _ctx(
        **_VERIFIED, private_trip_active=True, trip_type="local",
        private_service_type="full_package", private_destination="Sharm",
        private_dates_flexible=True, private_party_size=2, private_budget_currency="EGP",
        private_trip_request_id="PRT-1",
    ),
    "search_matching_trips": _ctx(**_VERIFIED, trip_type="international"),
    "handle_empty_trip_results": _ctx(
        **_VERIFIED, trip_type="international", trip_result={"open_trips": [], "date_tbd_trips": []}
    ),
    "select_trip": _ctx(
        **_VERIFIED, trip_type="international", trip_result={"open_trips": [_TRIP], "date_tbd_trips": []}
    ),
    "collect_traveler_gender": _ctx(
        **_VERIFIED, trip_type="international", trip_result={"open_trips": [_TRIP]},
        selected_trip_id="RT-1", selected_trip=_TRIP,
    ),
    "collect_gender_counts": _ctx(
        **_VERIFIED, trip_type="international", trip_result={"open_trips": [_TRIP]},
        selected_trip_id="RT-1", selected_trip=_TRIP, room_group="mixed",
    ),
    "collect_family_units": _ctx(
        **_VERIFIED, trip_type="international", trip_result={"open_trips": [_TRIP]},
        selected_trip_id="RT-1", selected_trip=_TRIP, room_group="mixed",
        boys_count=2, girls_count=2,
    ),
    "collect_room_type:mixed": _ctx(
        **_VERIFIED, trip_type="international", trip_result={"open_trips": [_TRIP]},
        selected_trip_id="RT-1", selected_trip=_TRIP, room_group="mixed",
        boys_count=2, girls_count=2, collection_state={"family_units": True}, family_units=0,
    ),
    "collect_group_nationality_type": _ctx(
        **_VERIFIED, trip_type="international", trip_result={"open_trips": [_TRIP]},
        selected_trip_id="RT-1", selected_trip=_TRIP, room_group="mixed",
        boys_count=2, girls_count=2, collection_state={"family_units": True}, family_units=0,
        room_requirements={"requirements": [
            {"room_type": "Double", "room_group": "boys", "rooms": 1},
            {"room_type": "Double", "room_group": "girls", "rooms": 1},
        ]},
    ),
    "collect_group_nationality_counts": _ctx(
        **_VERIFIED, trip_type="international", trip_result={"open_trips": [_TRIP]},
        selected_trip_id="RT-1", selected_trip=_TRIP, room_group="mixed",
        boys_count=2, girls_count=2, collection_state={"family_units": True}, family_units=0,
        room_requirements={"requirements": [
            {"room_type": "Double", "room_group": "boys", "rooms": 1},
            {"room_type": "Double", "room_group": "girls", "rooms": 1},
        ]},
        group_nationality_type="mixed",
    ),
    "collect_flight_preference": _ctx(
        **_VERIFIED, trip_type="international", trip_result={"open_trips": [_TRIP]},
        selected_trip_id="RT-1", selected_trip=_TRIP, room_group="mixed",
        boys_count=2, girls_count=2, collection_state={"family_units": True}, family_units=0,
        room_requirements={"requirements": [
            {"room_type": "Double", "room_group": "boys", "rooms": 1},
            {"room_type": "Double", "room_group": "girls", "rooms": 1},
        ]},
        group_nationality_type="mixed", group_nationality_counts={"egyptian": 2, "foreigner": 2},
    ),
    "collect_passport_attachment": _ctx(
        **_VERIFIED, trip_type="international", trip_result={"open_trips": [_TRIP]},
        selected_trip_id="RT-1", selected_trip=_TRIP, room_group="mixed",
        boys_count=2, girls_count=2,
        collection_state={"family_units": True, "flight_option": True}, family_units=0,
        room_requirements={"requirements": [
            {"room_type": "Double", "room_group": "boys", "rooms": 1},
            {"room_type": "Double", "room_group": "girls", "rooms": 1},
        ]},
        group_nationality_type="mixed", group_nationality_counts={"egyptian": 2, "foreigner": 2},
        flight_option="With Flight",
    ),
    "collect_payment_currency": _ctx(
        **_VERIFIED, trip_type="international", trip_result={"open_trips": [_TRIP]},
        selected_trip_id="RT-1", selected_trip=_TRIP, room_group="mixed",
        boys_count=2, girls_count=2,
        collection_state={"family_units": True, "flight_option": True}, family_units=0,
        room_requirements={"requirements": [
            {"room_type": "Double", "room_group": "boys", "rooms": 1},
            {"room_type": "Double", "room_group": "girls", "rooms": 1},
        ]},
        group_nationality_type="mixed", group_nationality_counts={"egyptian": 2, "foreigner": 2},
        flight_option="With Flight", passport_attachment_ref="doc123",
    ),
    "create_booking_draft": _ctx(
        **_VERIFIED, trip_type="international", trip_result={"open_trips": [_TRIP]},
        selected_trip_id="RT-1", selected_trip=_TRIP, room_group="mixed",
        boys_count=2, girls_count=2,
        collection_state={"family_units": True, "flight_option": True}, family_units=0,
        room_requirements={"requirements": [
            {"room_type": "Double", "room_group": "boys", "rooms": 1},
            {"room_type": "Double", "room_group": "girls", "rooms": 1},
        ]},
        group_nationality_type="mixed", group_nationality_counts={"egyptian": 2, "foreigner": 2},
        flight_option="With Flight", passport_attachment_ref="doc123", currency="EGP",
    ),
    "collect_group_size": _ctx(
        **_VERIFIED, trip_type="international", trip_result={"open_trips": [_TRIP]},
        selected_trip_id="RT-1", selected_trip=_TRIP, room_group="boys",
        collection_state={"room_type": True}, room_type="Single",
    ),
    "create_capacity_handoff": _ctx(
        **_VERIFIED, trip_type="international", trip_result={"open_trips": [_TRIP]},
        selected_trip_id="RT-1", selected_trip=_TRIP, room_group="boys",
        collection_state={"room_type": True, "group_size": True}, room_type="Single", group_size=100,
    ),
    "collect_new_traveler_name": _ctx(workflow={"lookup_status": "not_found"}),
    "collect_nationality": _ctx(workflow={"lookup_status": "not_found"}, customer_name="Test Name"),
    "collect_birthday": _ctx(
        workflow={"lookup_status": "not_found"}, customer_name="Test Name", nationality="Egyptian"
    ),
    "save_new_traveler_lead": _ctx(
        workflow={"lookup_status": "not_found"}, customer_name="Test Name",
        nationality="Egyptian", birthday="1990-01-01",
    ),
}


@pytest.mark.parametrize("label", sorted(SCENARIOS))
def test_required_step_reply_is_arabic_for_an_arabic_session(label: str) -> None:
    decision = ConversationWorkflowPolicy().evaluate(SCENARIOS[label])
    assert _ARABIC_RE.search(decision.assistant_message), (
        f"{label}: expected an Arabic assistant_message, got {decision.assistant_message!r}"
    )


def test_private_trip_consultation_handoff_does_not_crash_on_reason_kwarg_collision() -> None:
    """Pins the independent (language-agnostic) crash caught while building
    the bilingual coverage above: passing reason= alongside **common (which
    already sets "reason") raised TypeError on every hit of this branch,
    English or Arabic, before workflow_policy.py's fix.
    """
    decision = ConversationWorkflowPolicy().evaluate(SCENARIOS["human_review:private_trip_consultation"])
    assert decision.reason == "private_trip_consultation"
    assert decision.handoff_required is True
