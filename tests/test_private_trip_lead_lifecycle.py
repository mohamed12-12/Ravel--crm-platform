"""Private-trip lifecycle / data-integrity regressions.

Business invariant under test: **a traveler whose final conversation intent is a
private-trip request must not leave behind an ordinary Lead representing the
same acquisition/intake event.**

`_execute_new_traveler_lead` already refuses to create a Lead when
`private_trip_active` is true at identity-intake time (see
tests/test_phase6_private_trips.py). The residual gap this file covers is the
opposite ordering: identity intake completes as an ORDINARY lead, and only
*afterwards* does the customer say they want a private trip. The Lead is
already persisted by then and nothing reconciled it, leaving an unworked
"New Lead" in the regular pipeline that duplicates the private request.

Reconciliation is deliberately non-destructive: nothing is deleted, the Lead is
moved out of the ordinary pipeline onto `Handoff Needed` (the same stage
apps/api/app/routes/leads.py:899 already uses when a handoff is raised for a
lead) in the SAME write that records the private-trip handoff, and the private
request keeps the lead_id so the two records reference each other.

Scenario letters in the test names match the audit brief.
"""

from __future__ import annotations

from services.ai_agent.ai_agent_app.agent.session_flow import SessionState
from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime
from tests.test_golden_transcript_regressions import NEW_TRAVELER_WRITE, _write_results_by_action
from tests.test_phase12_booking_state import _send, runtime as runtime  # noqa: F401  (pytest fixture)


_INTAKE_LEAD_WRITE = {
    "executed": True,
    "result_id": "LD00003",
    "assistant_message": "Lead saved.",
    "lead_update": {"lead_id": "LD00003"},
    "write_result_contract": {
        "status": "success",
        "record_id": "LD00003",
        "record_type": "lead",
        "executed": True,
    },
}

_PRIVATE_REQUEST_WRITE = {
    "result_id": "PRT-000001",
    "executed": True,
    "write_result": {"private_trip_request": {"request_id": "PRT-000001"}},
    "write_result_contract": {
        "status": "created",
        "executed": True,
        "reused": False,
        "record_type": "private_trip_request",
        "record_id": "PRT-000001",
    },
    "session_update": {"private_trip_request_id": "PRT-000001"},
}

_PRIVATE_REQUEST_WRITE_FAILED = {
    "result_id": "",
    "executed": False,
    "write_result_contract": {
        "status": "failed",
        "executed": False,
        "reused": False,
        "record_type": "private_trip_request",
        "record_id": "",
    },
}

_PRIVATE_HANDOFF_WRITE = {
    "result_id": "H-00000001",
    "executed": True,
    "write_result": {"handoff_case": {"handoff_id": "H-00000001"}},
    "write_result_contract": {
        "status": "created",
        "executed": True,
        "reused": False,
        "record_type": "handoff",
        "record_id": "H-00000001",
    },
    "session_update": {"handoff_state": "handed_off"},
}

_IDENTITY_ANSWERS = ("01270482380", "Maged Samir Adly", "Egyptian", "28/4/2006")
_PRIVATE_ANSWERS = ("Local", "3", "Siwa", "flexible dates", "4 travelers", "around 50000 EGP")


def _payloads_for(runtime: ToolCallingSessionRuntime, action: str) -> list[dict]:
    return [
        call.kwargs["payload"]
        for call in runtime._write_executor.execute.call_args_list
        if call.kwargs["action"] == action
    ]


def _actions(runtime: ToolCallingSessionRuntime) -> list[str]:
    return [call.kwargs["action"] for call in runtime._write_executor.execute.call_args_list]


def _drive_lead_first_then_private(runtime: ToolCallingSessionRuntime) -> SessionState:
    session = runtime.create_session()
    for text in _IDENTITY_ANSWERS:
        session = _send(runtime, text, session)

    # Precondition for this whole file: the ordinary lead really did get
    # created, before any private-trip intent was expressed.
    assert session.private_trip_active is False
    assert "create_lead" in _actions(runtime)
    assert session.intake_lead_id == "LD00003"

    session = _send(runtime, "I want a private trip just for us", session)
    assert session.private_trip_active is True
    for answer in _PRIVATE_ANSWERS:
        session = _send(runtime, answer, session)
    return session


def test_scenario_b_lead_created_then_private_intent_reconciles_that_lead(
    runtime: ToolCallingSessionRuntime,
) -> None:
    runtime._write_executor.execute.side_effect = _write_results_by_action(
        create_traveler=NEW_TRAVELER_WRITE,
        create_lead=_INTAKE_LEAD_WRITE,
        create_private_trip_request=_PRIVATE_REQUEST_WRITE,
        create_handoff=_PRIVATE_HANDOFF_WRITE,
    )
    session = _drive_lead_first_then_private(runtime)

    assert session.private_trip_request_id == "PRT-000001"

    request_payloads = _payloads_for(runtime, "create_private_trip_request")
    assert len(request_payloads) == 1
    # The private request stays linked to the traveler AND to the lead it
    # supersedes, so the records are traceable in both directions.
    assert request_payloads[0]["traveler_id"] == "TR00007"
    assert request_payloads[0]["lead_id"] == "LD00003"

    handoff_payloads = _payloads_for(runtime, "create_handoff")
    assert len(handoff_payloads) == 1
    # The reconciliation itself, carried by the handoff write that already
    # targets this exact lead -- no extra write, no second lead lookup.
    assert handoff_payloads[0]["lead_id"] == "LD00003"
    assert handoff_payloads[0]["lead_stage_override"] == "Handoff Needed"
    assert session.intake_lead_reconciled is True


def test_scenario_a_private_intent_before_lead_creation_is_unchanged(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """No ordinary lead is created at all, so there is nothing to reconcile and
    no stage override may be sent."""

    runtime._write_executor.execute.side_effect = _write_results_by_action(
        create_traveler=NEW_TRAVELER_WRITE,
        create_private_trip_request=_PRIVATE_REQUEST_WRITE,
        create_handoff=_PRIVATE_HANDOFF_WRITE,
    )
    session = runtime.create_session()
    session = _send(runtime, "I want a private trip just for us", session)
    for text in _IDENTITY_ANSWERS:
        session = _send(runtime, text, session)
    for answer in _PRIVATE_ANSWERS:
        session = _send(runtime, answer, session)

    assert "create_lead" not in _actions(runtime)
    assert session.intake_lead_id == ""
    assert session.intake_lead_reconciled is False
    handoff_payloads = _payloads_for(runtime, "create_handoff")
    assert len(handoff_payloads) == 1
    assert not handoff_payloads[0].get("lead_stage_override")


def test_scenario_d_repeated_private_intent_does_not_duplicate_request_or_reconciliation(
    runtime: ToolCallingSessionRuntime,
) -> None:
    runtime._write_executor.execute.side_effect = _write_results_by_action(
        create_traveler=NEW_TRAVELER_WRITE,
        create_lead=_INTAKE_LEAD_WRITE,
        create_private_trip_request=_PRIVATE_REQUEST_WRITE,
        create_handoff=_PRIVATE_HANDOFF_WRITE,
    )
    session = _drive_lead_first_then_private(runtime)
    assert len(_payloads_for(runtime, "create_private_trip_request")) == 1

    for repeat in ("I want a private trip just for us", "private trip please"):
        session = _send(runtime, repeat, session)

    assert len(_payloads_for(runtime, "create_private_trip_request")) == 1
    assert len(_payloads_for(runtime, "create_handoff")) == 1
    assert session.private_trip_request_id == "PRT-000001"
    assert session.intake_lead_reconciled is True


def test_scenario_f_a_resumed_historical_lead_is_never_treated_as_the_intake_lead(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """`intake_lead_id` may only ever name a lead THIS session's own intake
    created. A lead the customer chose to continue is pre-existing CRM data and
    must never be attributed to (or reconciled by) this intake event -- note
    that `_linked_ids` deliberately DOES resolve it, which is exactly why the
    reconciliation target cannot be read from there.
    """

    session = runtime.create_session()
    session.resumed_lead_id = "LD-HISTORICAL-1"

    assert session.intake_lead_id == ""
    assert runtime._linked_ids(session)["lead_id"] == "LD-HISTORICAL-1"
    assert runtime._reconcilable_intake_lead_id(session) == ""


def test_scenario_g_a_failed_private_request_write_never_reconciles_the_lead(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """If the private request does not persist, the ordinary lead must be left
    exactly as it was: no handoff, no stage override, no success marker."""

    runtime._write_executor.execute.side_effect = _write_results_by_action(
        create_traveler=NEW_TRAVELER_WRITE,
        create_lead=_INTAKE_LEAD_WRITE,
        create_private_trip_request=_PRIVATE_REQUEST_WRITE_FAILED,
    )
    session = runtime.create_session()
    for text in _IDENTITY_ANSWERS:
        session = _send(runtime, text, session)
    assert session.intake_lead_id == "LD00003"

    session = _send(runtime, "I want a private trip just for us", session)
    for answer in _PRIVATE_ANSWERS:
        session = _send(runtime, answer, session)

    assert session.private_trip_request_id == ""
    assert session.intake_lead_reconciled is False
    assert _payloads_for(runtime, "create_handoff") == []


def test_the_reconciliation_marker_survives_a_session_store_round_trip(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Both new fields live on the SessionState dataclass, which is what
    session_store's _SESSION_FIELDS is derived from -- so they persist without
    a schema change. Pinned because the reconciliation decision is made on a
    later turn than the one that created the lead, i.e. after a store reload.
    """

    session = runtime.create_session()
    session.intake_lead_id = "LD00042"
    session.intake_lead_reconciled = True
    runtime._session_store.save(session)

    loaded = runtime._session_store.load(session.id)
    assert loaded is not None
    restored, _agent_state, _version = loaded
    assert restored.intake_lead_id == "LD00042"
    assert restored.intake_lead_reconciled is True


_PRIVATE_HANDOFF_REUSED = {
    "result_id": "H-00000001",
    # A deduplicated handoff: an existing handoff_id comes back, but nothing
    # was written this time -- including the lead update the fresh path does.
    "executed": False,
    "write_result": {"handoff_case": {"handoff_id": "H-00000001"}},
    "write_result_contract": {
        "status": "reused",
        "executed": False,
        "reused": True,
        "record_type": "handoff",
        "record_id": "H-00000001",
    },
}

_LEAD_STAGE_UPDATE_WRITE = {
    "executed": True,
    "result_id": "LD00003",
    "lead_update": {"lead_id": "LD00003", "lead_stage": "Handoff Needed"},
    "write_result_contract": {
        "status": "updated",
        "record_id": "LD00003",
        "record_type": "lead",
        "executed": True,
    },
}


def test_a_deduplicated_handoff_still_reconciles_the_lead_explicitly(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Both backends' create_handoff_case return from their dedupe branch
    BEFORE the `if update_lead and lead_id:` block, so a reused handoff carries
    no lead update and the stage override silently does nothing. The stage then
    has to be written explicitly, or the intake lead stays in the ordinary
    pipeline while the session believes it was reconciled.
    """

    runtime._write_executor.execute.side_effect = _write_results_by_action(
        create_traveler=NEW_TRAVELER_WRITE,
        create_lead=_INTAKE_LEAD_WRITE,
        create_private_trip_request=_PRIVATE_REQUEST_WRITE,
        create_handoff=_PRIVATE_HANDOFF_REUSED,
        update_lead_stage=_LEAD_STAGE_UPDATE_WRITE,
    )
    session = _drive_lead_first_then_private(runtime)

    stage_updates = _payloads_for(runtime, "update_lead_stage")
    assert len(stage_updates) == 1
    assert stage_updates[0]["lead_id"] == "LD00003"
    assert stage_updates[0]["requested_stage"] == "Handoff Needed"
    # update_lead_stage overwrites lead.notes rather than appending, so the
    # reconciliation must not send notes and destroy what is already there.
    assert "notes" not in stage_updates[0]
    assert session.intake_lead_reconciled is True


def test_a_fresh_handoff_does_not_also_issue_a_redundant_stage_update(
    runtime: ToolCallingSessionRuntime,
) -> None:
    runtime._write_executor.execute.side_effect = _write_results_by_action(
        create_traveler=NEW_TRAVELER_WRITE,
        create_lead=_INTAKE_LEAD_WRITE,
        create_private_trip_request=_PRIVATE_REQUEST_WRITE,
        create_handoff=_PRIVATE_HANDOFF_WRITE,
        update_lead_stage=_LEAD_STAGE_UPDATE_WRITE,
    )
    session = _drive_lead_first_then_private(runtime)

    assert _payloads_for(runtime, "update_lead_stage") == []
    assert session.intake_lead_reconciled is True


def test_reconciliation_is_not_marked_when_both_writes_fail(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Scenario G at the reconciliation step itself: the private request is
    genuinely saved, so the customer is still told the truth about it, but
    nothing may record a reconciliation that did not persist."""

    runtime._write_executor.execute.side_effect = _write_results_by_action(
        create_traveler=NEW_TRAVELER_WRITE,
        create_lead=_INTAKE_LEAD_WRITE,
        create_private_trip_request=_PRIVATE_REQUEST_WRITE,
        create_handoff=_PRIVATE_HANDOFF_REUSED,
        update_lead_stage={
            "executed": False,
            "result_id": "",
            "write_result_contract": {
                "status": "failed",
                "record_id": "",
                "record_type": "lead",
                "executed": False,
            },
        },
    )
    session = _drive_lead_first_then_private(runtime)

    assert session.private_trip_request_id == "PRT-000001"
    assert session.intake_lead_reconciled is False


def test_a_raising_stage_update_does_not_break_the_saved_private_request(
    runtime: ToolCallingSessionRuntime,
) -> None:
    def _execute(*, action, payload, session_context):
        if action == "create_traveler":
            return NEW_TRAVELER_WRITE
        if action == "create_lead":
            return _INTAKE_LEAD_WRITE
        if action == "create_private_trip_request":
            return _PRIVATE_REQUEST_WRITE
        if action == "create_handoff":
            return _PRIVATE_HANDOFF_REUSED
        if action == "update_lead_stage":
            raise RuntimeError("CRM unreachable")
        raise AssertionError(action)

    runtime._write_executor.execute.side_effect = _execute
    session = _drive_lead_first_then_private(runtime)

    # The request is saved and the customer is told so; only the internal
    # reconciliation marker stays false.
    assert session.private_trip_request_id == "PRT-000001"
    assert session.intake_lead_reconciled is False
    assert session.messages[-1]["role"] == "assistant"


def test_scenario_c_private_intent_in_the_same_turn_as_the_last_identity_field(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """No stale lead may appear when identity completes and private intent
    arrive together.

    handle_message runs _detect_private_trip_intent BEFORE
    _apply_required_step_capture and before the workflow decision, so
    private_trip_active is already true by the time save_new_traveler_lead is
    evaluated -- _execute_new_traveler_lead then takes its identity-only
    branch and no ordinary Lead is ever written.
    """

    runtime._write_executor.execute.side_effect = _write_results_by_action(
        create_traveler=NEW_TRAVELER_WRITE,
        create_private_trip_request=_PRIVATE_REQUEST_WRITE,
        create_handoff=_PRIVATE_HANDOFF_WRITE,
    )
    session = runtime.create_session()
    for text in ("01270482380", "Maged Samir Adly", "Egyptian"):
        session = _send(runtime, text, session)
    # Birthday and private-trip intent in one message.
    session = _send(runtime, "28/4/2006, and I want a private trip just for us", session)

    assert session.private_trip_active is True
    assert "create_lead" not in _actions(runtime)
    assert session.intake_lead_id == ""


def test_scenario_c_private_intent_after_a_failed_lead_write_creates_no_lead(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """The only real window between "identity complete" and "lead persisted" is
    a failed create_lead, which leaves new_traveler_lead_saved false so the next
    turn retries. If private intent arrives on that turn, the retry must take
    the identity-only branch instead of finally creating the stale lead."""

    failed_lead = {
        "executed": False,
        "result_id": "",
        "write_result_contract": {
            "status": "failed",
            "record_id": "",
            "record_type": "lead",
            "executed": False,
        },
    }
    runtime._write_executor.execute.side_effect = _write_results_by_action(
        create_traveler=NEW_TRAVELER_WRITE,
        create_lead=failed_lead,
        create_private_trip_request=_PRIVATE_REQUEST_WRITE,
        create_handoff=_PRIVATE_HANDOFF_WRITE,
    )
    session = runtime.create_session()
    for text in _IDENTITY_ANSWERS:
        session = _send(runtime, text, session)

    # The lead write was attempted and failed, so nothing was recorded.
    assert "create_lead" in _actions(runtime)
    assert session.intake_lead_id == ""
    assert session.new_traveler_lead_saved is False

    session = _send(runtime, "I want a private trip just for us", session)
    for answer in _PRIVATE_ANSWERS:
        session = _send(runtime, answer, session)

    assert session.private_trip_active is True
    assert session.intake_lead_id == ""
    assert session.intake_lead_reconciled is False
    handoff_payloads = _payloads_for(runtime, "create_handoff")
    assert handoff_payloads, "the private request should still raise its handoff"
    assert not handoff_payloads[-1].get("lead_stage_override")


def test_scenario_e_cancelling_after_reconciliation_writes_nothing_further(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Cancellation semantics are deliberately untouched: the cancel path sets
    handoff_state/stage on the session only. A reconciliation that already
    persisted in CRM is NOT rolled back by a chat cancellation -- undoing it
    would be a new workflow, and the private request it reconciles for still
    exists and is still with the team."""

    runtime._write_executor.execute.side_effect = _write_results_by_action(
        create_traveler=NEW_TRAVELER_WRITE,
        create_lead=_INTAKE_LEAD_WRITE,
        create_private_trip_request=_PRIVATE_REQUEST_WRITE,
        create_handoff=_PRIVATE_HANDOFF_WRITE,
    )
    session = _drive_lead_first_then_private(runtime)
    assert session.intake_lead_reconciled is True
    writes_before = len(runtime._write_executor.execute.call_args_list)

    session = _send(runtime, "cancel", session)

    assert len(runtime._write_executor.execute.call_args_list) == writes_before
    assert session.intake_lead_reconciled is True
    assert session.private_trip_request_id == "PRT-000001"


def test_reconciliation_never_targets_a_lead_the_handoff_is_not_updating(
    runtime: ToolCallingSessionRuntime,
) -> None:
    """Belt-and-braces on scenario F: if the lead the private request/handoff
    resolve to is NOT this session's own intake lead, no override is sent and no
    reconciliation is claimed -- the other lead keeps its existing behaviour."""

    runtime._write_executor.execute.side_effect = _write_results_by_action(
        create_traveler=NEW_TRAVELER_WRITE,
        create_lead=_INTAKE_LEAD_WRITE,
        create_private_trip_request=_PRIVATE_REQUEST_WRITE,
        create_handoff=_PRIVATE_HANDOFF_WRITE,
        update_lead_stage=_LEAD_STAGE_UPDATE_WRITE,
    )
    session = runtime.create_session()
    for text in _IDENTITY_ANSWERS:
        session = _send(runtime, text, session)
    assert session.intake_lead_id == "LD00003"
    # Simulate the handoff/request resolving to a different, pre-existing lead.
    session.intake_lead_id = "LD-SOMETHING-ELSE"

    session = _send(runtime, "I want a private trip just for us", session)
    for answer in _PRIVATE_ANSWERS:
        session = _send(runtime, answer, session)

    handoff_payloads = _payloads_for(runtime, "create_handoff")
    assert len(handoff_payloads) == 1
    assert handoff_payloads[0]["lead_id"] == "LD00003"
    assert not handoff_payloads[0].get("lead_stage_override")
    assert _payloads_for(runtime, "update_lead_stage") == []
    assert session.intake_lead_reconciled is False
