from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook

from services.ai_agent.ai_agent_app.agent.crm_api_client import CRMApiError, CRMApiRateLimitError
from services.ai_agent.ai_agent_app.config import load_settings
from services.ai_agent.ai_agent_app.server import _safe_demo_stats, create_app
from services.instagram.payload_parser import parse_instagram_webhook
from services.instagram.webhooks import filter_entries_for_page, validate_meta_signature


def _minimal_workbook(path: Path) -> None:
    wb = Workbook()
    wb.active.title = "Travelers"
    for sheet in ("Trips", "Interactions", "Leads", "Booking Event Trail", "Trip Bookings"):
        wb.create_sheet(sheet)
    wb.save(path)
    wb.close()


@pytest.fixture()
def agent_app(tmp_path: Path):
    original_env = dict(os.environ)
    source = tmp_path / "source.xlsx"
    runtime = tmp_path / "runtime.xlsx"
    _minimal_workbook(source)
    os.environ["AI_PROVIDER"] = "none"
    os.environ["GEMINI_API_KEY"] = ""
    settings = replace(
        load_settings(),
        app_env="development",
        ai_agent_mode="deterministic",
        ai_provider="none",
        gemini_api_key="",
        crm_access_mode="api",
        crm_api_base_url="http://127.0.0.1:5002",
        crm_api_token="test-token",
        sheet_backend="excel",
        excel_source_workbook=source,
        excel_runtime_workbook=runtime,
    )
    app = create_app(settings=settings)
    try:
        yield app
    finally:
        os.environ.clear()
        os.environ.update(original_env)
        shutil.rmtree(tmp_path, ignore_errors=True)


# ============================================================================
# INCIDENT 1: CRM API Rate Limiting & Resilience Tests
# ============================================================================

def test_crm_healthy_stats_returned(agent_app) -> None:
    gateway = agent_app.config["SHEET_GATEWAY"]
    expected_stats = {
        "travelerCount": 10,
        "interactionCount": 20,
        "leadCount": 5,
        "bookingDraftCount": 1,
        "tripStatusCounts": {"Open": 2},
    }
    gateway.get_demo_stats = lambda: dict(expected_stats)

    res = agent_app.test_client().post("/api/session", json={})
    assert res.status_code == 200
    stats = res.get_json()["session"]["stats"]
    assert stats["travelerCount"] == 10
    assert stats["statsAvailable"] is True


def test_crm_429_agent_response_succeeds(agent_app) -> None:
    gateway = agent_app.config["SHEET_GATEWAY"]

    def rate_limited_stats():
        raise CRMApiRateLimitError("CRM API returned HTTP 429: Too Many Requests", retry_after=30)

    gateway.get_demo_stats = rate_limited_stats

    res = agent_app.test_client().post("/api/session", json={})
    assert res.status_code == 200
    payload = res.get_json()
    assert payload["session"]["id"]
    stats = payload["session"]["stats"]
    assert stats["statsAvailable"] is False
    assert stats["statsError"] in {"stats_unavailable", "stats_cooldown"}


def test_crm_429_recovery_response_no_recursive_call(agent_app) -> None:
    gateway = agent_app.config["SHEET_GATEWAY"]
    call_count = {"count": 0}

    def failing_stats():
        call_count["count"] += 1
        raise CRMApiRateLimitError("CRM API returned HTTP 429: Too Many Requests")

    gateway.get_demo_stats = failing_stats

    client = agent_app.test_client()
    res1 = client.post("/api/session", json={})
    assert res1.status_code == 200

    session_id = res1.get_json()["session"]["id"]
    res2 = client.post(f"/api/session/{session_id}/message", json={"text": "test error"})
    assert res2.status_code == 200
    # Because of cooldown, the second call didn't re-trigger get_demo_stats!
    assert call_count["count"] == 1


def test_crm_timeout_and_5xx_graceful(agent_app) -> None:
    gateway = agent_app.config["SHEET_GATEWAY"]
    gateway.get_demo_stats = lambda: (_ for _ in ()).throw(CRMApiError("CRM API returned HTTP 500: Server Error"))

    res = agent_app.test_client().post("/api/session", json={})
    assert res.status_code == 200
    assert res.get_json()["session"]["stats"]["statsAvailable"] is False


def test_critical_crm_operation_failure_not_swallowed(agent_app) -> None:
    from services.ai_agent.ai_agent_app.agent.crm_api_client import CRMApiClient

    client = CRMApiClient(base_url="http://127.0.0.1:9999", token="bad-token")
    with pytest.raises(CRMApiError):
        client.write("create_booking", {}, {})


def test_rate_limit_cooldown_prevents_retry_storm(agent_app) -> None:
    gateway = agent_app.config["SHEET_GATEWAY"]
    calls = {"count": 0}

    def rate_limited():
        calls["count"] += 1
        raise CRMApiRateLimitError("CRM API returned HTTP 429: Too Many Requests")

    gateway.get_demo_stats = rate_limited

    for _ in range(5):
        _safe_demo_stats(gateway)

    # get_demo_stats was only called ONCE due to cooldown negative caching!
    assert calls["count"] == 1


# ============================================================================
# INCIDENT 2: Instagram Webhook Crashes & Entry Normalization Tests
# ============================================================================

def test_valid_instagram_message() -> None:
    payload = {
        "object": "instagram",
        "entry": [
            {
                "id": "17841480645273321",
                "time": 1700000000,
                "messaging": [
                    {
                        "sender": {"id": "user123"},
                        "recipient": {"id": "17841480645273321"},
                        "timestamp": 1700000000,
                        "message": {"mid": "mid123", "text": "Hello Rahma"},
                    }
                ],
            }
        ],
    }

    accepted, rejected = filter_entries_for_page(payload, "17841480645273321")
    assert len(accepted) == 1
    assert len(rejected) == 0

    events = parse_instagram_webhook({"entry": accepted})
    assert len(events) == 1
    assert events[0].event_id == "mid123"
    assert events[0].sender_id == "user123"
    assert events[0].text == "Hello Rahma"


def test_dict_entry_object_not_list() -> None:
    # Meta payload where "entry" is a dict instead of a list
    payload = {
        "object": "instagram",
        "entry": {
            "id": "17841480645273321",
            "messaging": [
                {
                    "sender": {"id": "user456"},
                    "recipient": {"id": "17841480645273321"},
                    "message": {"mid": "mid456", "text": "Dict entry test"},
                }
            ],
        },
    }

    accepted, rejected = filter_entries_for_page(payload, "17841480645273321")
    assert len(accepted) == 1
    assert isinstance(accepted[0], dict)

    events = parse_instagram_webhook({"entry": accepted})
    assert len(events) == 1
    assert events[0].text == "Dict entry test"


def test_string_entry_in_payload() -> None:
    # Payload where entry is a list containing stringified JSON
    entry_json = json.dumps({
        "id": "17841480645273321",
        "messaging": [
            {
                "sender": {"id": "user789"},
                "message": {"mid": "mid789", "text": "String entry test"},
            }
        ],
    })
    payload = {"entry": [entry_json]}

    accepted, rejected = filter_entries_for_page(payload, "17841480645273321")
    assert len(accepted) == 1
    assert isinstance(accepted[0], dict)  # Strictly converted to dict!

    events = parse_instagram_webhook({"entry": accepted})
    assert len(events) == 1
    assert events[0].text == "String entry test"


def test_mixed_valid_and_invalid_entries() -> None:
    valid_entry = {
        "id": "17841480645273321",
        "messaging": [
            {
                "sender": {"id": "user_valid"},
                "message": {"mid": "mid_valid", "text": "Valid message"},
            }
        ],
    }
    payload = {
        "entry": [
            "invalid string non-json",
            12345,
            valid_entry,
        ]
    }

    accepted, rejected = filter_entries_for_page(payload, "17841480645273321")
    assert len(accepted) == 1
    assert accepted[0]["id"] == "17841480645273321"

    events = parse_instagram_webhook({"entry": accepted})
    assert len(events) == 1
    assert events[0].event_id == "mid_valid"


def test_missing_fields_and_empty_entry() -> None:
    assert parse_instagram_webhook({}) == []
    assert parse_instagram_webhook({"entry": []}) == []
    assert parse_instagram_webhook({"entry": [None, {}]}) == []


# ============================================================================
# INCIDENT 3: META_APP_SECRET Security & Configuration Tests
# ============================================================================

def test_missing_meta_app_secret_warning(agent_app) -> None:
    @validate_meta_signature("")
    def dummy_route():
        return "OK", 200

    with agent_app.test_request_context("/rahma-agent/webhook"):
        resp, code = dummy_route()
        assert code == 200
        assert resp == "OK"
