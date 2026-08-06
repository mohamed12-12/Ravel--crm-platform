from __future__ import annotations

import hashlib
import hmac
import json
import sys
from pathlib import Path

import pytest
from flask import Flask, jsonify

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.instagram.webhooks import validate_meta_signature, verify_webhook, filter_entries_for_page
from services.instagram.payload_parser import parse_instagram_webhook

APP_SECRET = "test-app-secret-not-a-real-meta-secret"
VERIFY_TOKEN = "test-verify-token"
PAGE_ID = "17841400000000000"

# Realistic Meta Instagram Messaging webhook payload shape (per Meta's
# Instagram Messaging API docs): object=instagram, entry[].messaging[]
# carrying sender/recipient/message with a text body.
SAMPLE_PAYLOAD = {
    "object": "instagram",
    "entry": [
        {
            "id": PAGE_ID,
            "time": 1717000000,
            "messaging": [
                {
                    "sender": {"id": "1234567890123456"},
                    "recipient": {"id": PAGE_ID},
                    "timestamp": 1717000000000,
                    "message": {
                        "mid": "aWdfZAG1faXRlbToxOjEyMzQ1Njc4OTAxMjM0NTY6MzQwMjgyMzY2ODQxNzEwMzAyMTEyNDQ0NTM3NjAwMDAwMA==",
                        "text": "Hi, I'd like to book a trip to Luxor",
                    },
                }
            ],
        }
    ],
}


def _signed_body(body: bytes, secret: str = APP_SECRET) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@pytest.fixture()
def signature_app():
    app = Flask(__name__)

    @app.post("/rahma-agent/webhook")
    @validate_meta_signature(APP_SECRET)
    def _receive():
        return jsonify({"ok": True})

    return app


def test_valid_signature_is_accepted(signature_app):
    client = signature_app.test_client()
    body = json.dumps(SAMPLE_PAYLOAD).encode("utf-8")

    response = client.post(
        "/rahma-agent/webhook",
        data=body,
        content_type="application/json",
        headers={"X-Hub-Signature-256": _signed_body(body)},
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


def test_missing_signature_is_rejected(signature_app):
    client = signature_app.test_client()
    response = client.post("/rahma-agent/webhook", json=SAMPLE_PAYLOAD)

    assert response.status_code == 401
    assert response.get_json()["error"] == "missing_signature"


def test_malformed_signature_format_is_rejected(signature_app):
    client = signature_app.test_client()
    response = client.post(
        "/rahma-agent/webhook",
        json=SAMPLE_PAYLOAD,
        headers={"X-Hub-Signature-256": "not-sha256-prefixed"},
    )

    assert response.status_code == 401
    assert response.get_json()["error"] == "invalid_signature_format"


def test_wrong_signature_is_rejected(signature_app):
    client = signature_app.test_client()
    body = json.dumps(SAMPLE_PAYLOAD).encode("utf-8")

    response = client.post(
        "/rahma-agent/webhook",
        data=body,
        content_type="application/json",
        headers={"X-Hub-Signature-256": _signed_body(body, secret="wrong-secret")},
    )

    assert response.status_code == 401
    assert response.get_json()["error"] == "signature_mismatch"


def test_tampered_body_after_signing_is_rejected(signature_app):
    client = signature_app.test_client()
    body = json.dumps(SAMPLE_PAYLOAD).encode("utf-8")
    signature = _signed_body(body)
    tampered_body = json.dumps({**SAMPLE_PAYLOAD, "object": "tampered"}).encode("utf-8")

    response = client.post(
        "/rahma-agent/webhook",
        data=tampered_body,
        content_type="application/json",
        headers={"X-Hub-Signature-256": signature},
    )

    assert response.status_code == 401
    assert response.get_json()["error"] == "signature_mismatch"


def test_unconfigured_secret_returns_config_error():
    app = Flask(__name__)

    @app.post("/rahma-agent/webhook")
    @validate_meta_signature("")
    def _receive():
        return jsonify({"ok": True})

    client = app.test_client()
    response = client.post("/rahma-agent/webhook", json=SAMPLE_PAYLOAD, headers={"X-Hub-Signature-256": "sha256=whatever"})

    assert response.status_code == 500
    assert response.get_json()["error"] == "config_error"


def test_rejected_signature_attempts_are_logged(signature_app, caplog):
    client = signature_app.test_client()
    with caplog.at_level("WARNING", logger="rahma_webhook"):
        client.post("/rahma-agent/webhook", json=SAMPLE_PAYLOAD)

    assert any("webhook_rejected" in record.message for record in caplog.records)
    assert any("missing_signature" in record.message for record in caplog.records)


def test_verify_webhook_handshake_succeeds():
    app = Flask(__name__)

    @app.get("/rahma-agent/webhook")
    def _verify():
        return verify_webhook(VERIFY_TOKEN)

    client = app.test_client()
    response = client.get(
        "/rahma-agent/webhook",
        query_string={"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "challenge-123"},
    )

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "challenge-123"


def test_verify_webhook_handshake_rejects_wrong_token():
    app = Flask(__name__)

    @app.get("/rahma-agent/webhook")
    def _verify():
        return verify_webhook(VERIFY_TOKEN)

    client = app.test_client()
    response = client.get(
        "/rahma-agent/webhook",
        query_string={"hub.mode": "subscribe", "hub.verify_token": "wrong-token", "hub.challenge": "challenge-123"},
    )

    assert response.status_code == 403


def test_verify_webhook_handshake_missing_params_returns_404():
    app = Flask(__name__)

    @app.get("/rahma-agent/webhook")
    def _verify():
        return verify_webhook(VERIFY_TOKEN)

    client = app.test_client()
    response = client.get("/rahma-agent/webhook")

    assert response.status_code == 404


def test_filter_entries_for_page_keeps_matching_entries():
    accepted, rejected = filter_entries_for_page(SAMPLE_PAYLOAD, PAGE_ID)

    assert len(accepted) == 1
    assert accepted[0]["id"] == PAGE_ID
    assert rejected == []


def test_filter_entries_for_page_drops_mismatched_entries():
    other_page_payload = {"entry": [{**SAMPLE_PAYLOAD["entry"][0], "id": "99999999999999999"}]}

    accepted, rejected = filter_entries_for_page(other_page_payload, PAGE_ID)

    assert accepted == []
    assert len(rejected) == 1


def test_filter_entries_for_page_passthrough_when_unconfigured():
    accepted, rejected = filter_entries_for_page(SAMPLE_PAYLOAD, "")

    assert len(accepted) == 1
    assert rejected == []


def test_parse_instagram_webhook_extracts_realistic_payload():
    events = parse_instagram_webhook(SAMPLE_PAYLOAD)

    assert len(events) == 1
    event = events[0]
    assert event.sender_id == "1234567890123456"
    assert event.recipient_id == PAGE_ID
    assert event.text == "Hi, I'd like to book a trip to Luxor"
    assert event.timestamp == 1717000000000


def test_parse_instagram_webhook_skips_events_without_message_id():
    payload = {
        "entry": [
            {
                "id": PAGE_ID,
                "messaging": [
                    {"sender": {"id": "abc"}, "recipient": {"id": PAGE_ID}, "message": {"text": "no mid here"}}
                ],
            }
        ]
    }

    events = parse_instagram_webhook(payload)

    assert events == []


def test_parse_instagram_webhook_handles_attachments():
    payload = {
        "entry": [
            {
                "id": PAGE_ID,
                "messaging": [
                    {
                        "sender": {"id": "abc"},
                        "recipient": {"id": PAGE_ID},
                        "timestamp": 1717000000000,
                        "message": {
                            "mid": "mid-with-attachment",
                            "attachments": [{"type": "image", "payload": {"url": "https://example.com/photo.jpg"}}],
                        },
                    }
                ],
            }
        ]
    }

    events = parse_instagram_webhook(payload)

    assert len(events) == 1
    assert events[0].attachments[0].attachment_type == "image"
    assert events[0].attachments[0].url == "https://example.com/photo.jpg"
