from __future__ import annotations

import os
import shutil
import unittest
import uuid
from pathlib import Path

from services.crm.system_services.phone_normalization import normalize_phone_input
from test_phase11_demo_features import _make_app_with_db


class PhoneNormalizationHelperTests(unittest.TestCase):
    def test_detect_plus_20(self) -> None:
        result = normalize_phone_input("+201234567890", "")
        self.assertEqual(result.country_code, "20")
        self.assertEqual(result.local_number, "1234567890")
        self.assertEqual(result.normalized_e164, "+201234567890")
        self.assertFalse(result.requires_country_confirmation)

    def test_detect_digits_20(self) -> None:
        result = normalize_phone_input("201234567890", "")
        self.assertEqual(result.country_code, "20")
        self.assertEqual(result.normalized_e164, "+201234567890")

    def test_detect_egypt_local(self) -> None:
        result = normalize_phone_input("01012345678", "20", default_country_is_explicit=True)
        self.assertEqual(result.country_code, "20")
        self.assertEqual(result.local_number, "1012345678")
        self.assertEqual(result.normalized_e164, "+201012345678")
        self.assertEqual(result.inferred_nationality, "Egyptian")

    def test_detect_saudi(self) -> None:
        result = normalize_phone_input("+966512345678", "")
        self.assertEqual(result.country_code, "966")
        self.assertEqual(result.local_number, "512345678")
        self.assertEqual(result.normalized_e164, "+966512345678")
        self.assertEqual(result.inferred_country, "Saudi Arabia")
        self.assertEqual(result.inferred_nationality, "")

    def test_unknown_requires_confirmation(self) -> None:
        result = normalize_phone_input("+999123456789", "")
        self.assertTrue(result.requires_country_confirmation)
        self.assertEqual(result.country_code, "")
        self.assertEqual(result.normalized_e164, "")

    def test_lookup_variants_include_backward_compatible_keys(self) -> None:
        result = normalize_phone_input("+201234567890", "")
        self.assertIn("20:1234567890", result.phone_variants_for_lookup)
        self.assertIn("201234567890", result.phone_variants_for_lookup)
        self.assertIn("1234567890", result.phone_variants_for_lookup)


class PhoneNormalizationIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(".tmp-test-phone-normalization") / uuid.uuid4().hex
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.original_env = dict(os.environ)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.original_env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_non_egyptian_number_prefills_country_without_nationality(self) -> None:
        client, _ = _make_app_with_db(self.tmp)
        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "+966512345678"},
        ).get_json()["session"]
        self.assertEqual(session["countryCode"], "966")
        self.assertEqual(session["phoneNormalization"]["country_code"], "966")
        self.assertEqual(session["phoneNormalization"]["inferred_nationality"], "")

    def test_unknown_country_prompts_confirmation(self) -> None:
        client, _ = _make_app_with_db(self.tmp)
        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "+999123456789"},
        ).get_json()["session"]
        self.assertEqual(session["stage"], "awaiting_country_code")
        self.assertTrue(session["phoneNormalization"]["requires_country_confirmation"])

    def test_non_phone_clarification_keeps_phone_stage(self) -> None:
        client, _ = _make_app_with_db(self.tmp)
        session = client.post("/api/session", json={}).get_json()["session"]
        session = client.post(
            f"/api/session/{session['id']}/message",
            json={"text": "what?"},
        ).get_json()["session"]
        self.assertEqual(session["stage"], "awaiting_phone")
        self.assertIn("whatsapp number", session["messages"][-1]["text"].lower())


if __name__ == "__main__":
    unittest.main()
