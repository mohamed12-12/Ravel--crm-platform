from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import replace
from pathlib import Path

import pytest
from openpyxl import Workbook

from services.ai_agent.ai_agent_app.agent.crm_api_client import CRMApiError
from services.ai_agent.ai_agent_app.config import load_settings
from services.ai_agent.ai_agent_app.server import _safe_demo_stats, create_app


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
        crm_api_token="stats-test-token",
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


def _patch_stats(app, outcomes):
    gateway = app.config["SHEET_GATEWAY"]
    calls = {"count": 0}
    queue = list(outcomes)

    def fake_get_demo_stats():
        calls["count"] += 1
        outcome = queue.pop(0) if queue else outcomes[-1]
        if isinstance(outcome, Exception):
            raise outcome
        return dict(outcome)

    gateway.get_demo_stats = fake_get_demo_stats
    return calls


def test_session_creation_preserves_successful_crm_stats(agent_app) -> None:
    calls = _patch_stats(
        agent_app,
        [
            {
                "travelerCount": 12,
                "interactionCount": 34,
                "leadCount": 5,
                "bookingDraftCount": 2,
                "tripStatusCounts": {"Open": 3},
            }
        ],
    )

    response = agent_app.test_client().post("/api/session", json={})

    assert response.status_code == 200
    stats = response.get_json()["session"]["stats"]
    assert stats["travelerCount"] == 12
    assert stats["interactionCount"] == 34
    assert stats["tripStatusCounts"] == {"Open": 3}
    assert stats["statsAvailable"] is True
    assert stats["statsStale"] is False
    assert stats["statsError"] == ""
    assert calls["count"] == 1


@pytest.mark.parametrize(
    "exc",
    [
        CRMApiError("CRM API returned HTTP 429: Too Many Requests"),
        CRMApiError("CRM API returned HTTP 500: internal server error"),
        CRMApiError("CRM API request failed: timed out"),
    ],
)
def test_session_creation_survives_unavailable_crm_stats(agent_app, exc: CRMApiError) -> None:
    _patch_stats(agent_app, [exc])

    response = agent_app.test_client().post("/api/session", json={})

    assert response.status_code == 200
    payload = response.get_json()
    stats = payload["session"]["stats"]
    assert payload["session"]["id"]
    assert payload["session"]["stage"] == "awaiting_phone"
    assert stats["statsAvailable"] is False
    assert stats["statsStale"] is False
    assert stats["statsError"] == "stats_unavailable"
    assert stats["travelerCount"] is None
    assert stats["leadCount"] is None
    assert stats["dbSource"] == "unavailable"


def test_bootstrap_survives_unavailable_crm_stats(agent_app) -> None:
    _patch_stats(agent_app, [CRMApiError("CRM API returned HTTP 503: service unavailable")])

    response = agent_app.test_client().get("/api/bootstrap")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["stats"]["statsAvailable"] is False
    assert payload["stats"]["statsError"] == "stats_unavailable"
    assert "runtimeWorkbook" in payload


def test_session_message_survives_unavailable_crm_stats(agent_app) -> None:
    _patch_stats(
        agent_app,
        [
            CRMApiError("CRM API returned HTTP 429: Too Many Requests"),
            CRMApiError("CRM API returned HTTP 500: internal server error"),
        ],
    )
    client = agent_app.test_client()
    session = client.post("/api/session", json={}).get_json()["session"]

    response = client.post(f"/api/session/{session['id']}/message", json={"text": "hello"})

    assert response.status_code == 200
    payload = response.get_json()["session"]
    assert payload["id"] == session["id"]
    assert payload["stats"]["statsAvailable"] is False
    assert payload["stats"]["statsError"] == "stats_unavailable"


def test_crm_stats_success_is_cached_for_session_serializations(agent_app, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_STATS_CACHE_SECONDS", "60")
    calls = _patch_stats(
        agent_app,
        [
            {
                "travelerCount": 7,
                "interactionCount": 8,
                "leadCount": 9,
                "bookingDraftCount": 1,
                "tripStatusCounts": {"Open": 1},
            }
        ],
    )
    client = agent_app.test_client()

    session = client.post("/api/session", json={}).get_json()["session"]
    fetched = client.get(f"/api/session/{session['id']}").get_json()["session"]

    assert fetched["stats"]["travelerCount"] == 7
    assert fetched["stats"]["statsAvailable"] is True
    assert calls["count"] == 1


def test_crm_stats_failure_returns_stale_cache_when_available(agent_app, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_STATS_CACHE_SECONDS", "0")
    _patch_stats(
        agent_app,
        [
            {"travelerCount": 3, "interactionCount": 4, "leadCount": 5, "bookingDraftCount": 1},
            CRMApiError("CRM API returned HTTP 429: Too Many Requests"),
        ],
    )
    client = agent_app.test_client()

    session = client.post("/api/session", json={}).get_json()["session"]
    fetched = client.get(f"/api/session/{session['id']}").get_json()["session"]

    assert fetched["stats"]["travelerCount"] == 3
    assert fetched["stats"]["statsAvailable"] is True
    assert fetched["stats"]["statsStale"] is True
    assert fetched["stats"]["statsError"] == "stats_unavailable"


def test_non_api_stats_failures_still_surface(agent_app) -> None:
    gateway = agent_app.config["SHEET_GATEWAY"]
    gateway.settings = replace(gateway.settings, crm_access_mode="shared_service")

    def broken_stats():
        raise RuntimeError("local stats schema is broken")

    gateway.get_demo_stats = broken_stats

    with pytest.raises(RuntimeError, match="local stats schema is broken"):
        _safe_demo_stats(gateway)
