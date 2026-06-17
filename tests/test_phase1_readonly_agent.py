from __future__ import annotations

import shutil
import unittest
import uuid
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook

from scripts.phase1_readonly_agent import build_agent_response, names_are_compatible


class Phase1ReadonlyAgentTests(unittest.TestCase):
    def test_names_are_compatible_requires_more_than_common_first_name(self) -> None:
        self.assertTrue(names_are_compatible("Mona Ali", "Mona Ali"))
        self.assertTrue(names_are_compatible("Abdulrahman Ashour", "Abdulrahman Ashur"))
        self.assertFalse(names_are_compatible("Mona", "Mona Ali"))
        self.assertFalse(names_are_compatible("Ahmed", "Ahmed Selim"))
        self.assertFalse(names_are_compatible("Malak Yasser Mohamed", "Ahmed Selim"))

    def test_build_agent_response_handles_phase1_cases(self) -> None:
        tmp_root = Path(".tmp-test-workdirs")
        tmp_root.mkdir(exist_ok=True)
        tmp_path = tmp_root / f"phase1-{uuid.uuid4().hex}"
        tmp_path.mkdir()
        try:
            workbook_path = tmp_path / "phase1.xlsx"

            wb = Workbook()
            travelers = wb.active
            travelers.title = "Travelers"
            travelers.append(
                [
                    "Status",
                    "Traveler ID",
                    "Full Name",
                    "Code",
                    "WhatsApp",
                    "Loc. Trips",
                    "Int. Trips",
                    "Total trips",
                    "Integrated WhatsApp",
                    "Phone Lookup Key",
                    "Data Audit",
                ]
            )
            travelers.append(["Blacklisted", "TR00001", "Jane Doe", "20", "1005828000", 1, 0, 1, "+201005828000", "20:1005828000", ""])
            travelers.append(["", "TR00002", "John Doe", "20", "1277445335", 0, 1, 1, "+201277445335", "20:1277445335", "DUPLICATE_PHONE"])
            travelers.append(["", "TR00003", "Johnny Doe", "20", "1277445335", 1, 1, 2, "+201277445335", "20:1277445335", "DUPLICATE_PHONE"])
            travelers.append(["VIP", "TR00004", "Mona Ali", "20", "1112223333", 2, 1, 3, "+201112223333", "20:1112223333", ""])

            trips = wb.create_sheet("Trips")
            trips["A2"] = "Trip ID"
            trips["B2"] = "Trip Name"
            trips["C2"] = "Type"
            trips["D2"] = "Year"
            trips["F2"] = "Start Date"
            trips["G2"] = "End Date"
            trips["Z2"] = "Sales Status"
            trips["AA2"] = "Data Audit"

            trips["A3"] = "RT-LOC-26-010"
            trips["B3"] = "Siwa Summer"
            trips["C3"] = "Local"
            trips["D3"] = 2026
            future_start = date.today() + timedelta(days=30)
            trips["F3"] = future_start.isoformat()
            trips["G3"] = (future_start + timedelta(days=2)).isoformat()
            trips["Z3"] = "Open"

            trips["A4"] = "RT-INT-26-011"
            trips["B4"] = "Georgia Summer"
            trips["C4"] = "International"
            trips["D4"] = 2026
            trips["Z4"] = "Date TBD"
            trips["AA4"] = "MISSING_END_DATE; MISSING_START_DATE"

            trips["A5"] = "RT-INT-25-001"
            trips["B5"] = "Old Trip"
            trips["C5"] = "International"
            trips["D5"] = 2025
            trips["Z5"] = "Archived"

            wb.save(workbook_path)

            blacklisted = build_agent_response(workbook_path, "Someone Else", "1005828000", "local", "20")
            self.assertEqual(blacklisted["match_status"], "single_match")
            self.assertTrue(blacklisted["handoff_required"])
            self.assertEqual(blacklisted["handoff_reason"], "blacklisted_customer")
            self.assertIn("block_sales_flow", blacklisted["actions"])
            self.assertIsNone(blacklisted["trip_result"])

            duplicate = build_agent_response(workbook_path, "John", "1277445335", "international", "20")
            self.assertEqual(duplicate["match_status"], "multiple_matches")
            self.assertTrue(duplicate["handoff_required"])
            self.assertEqual(duplicate["handoff_reason"], "duplicate_phone_match")
            self.assertIsNone(duplicate["trip_result"])

            first_name_only = build_agent_response(workbook_path, "Mona", "1112223333", "international", "20")
            self.assertEqual(first_name_only["match_status"], "single_match")
            self.assertTrue(first_name_only["handoff_required"])
            self.assertEqual(first_name_only["handoff_reason"], "phone_name_conflict")
            self.assertIsNone(first_name_only["trip_result"])

            phone_only = build_agent_response(workbook_path, "", "1112223333", "international", "20")
            self.assertEqual(phone_only["match_status"], "single_match")
            self.assertEqual(phone_only["name_match_status"], "phone_only")
            self.assertFalse(phone_only["handoff_required"])
            self.assertIn("offer_date_tbd_follow_up", phone_only["actions"])

            vip = build_agent_response(workbook_path, "Mona Ali", "1112223333", "international", "20")
            self.assertEqual(vip["match_status"], "single_match")
            self.assertFalse(vip["handoff_required"])
            self.assertIn("offer_date_tbd_follow_up", vip["actions"])
            self.assertEqual(len(vip["trip_result"]["open_trips"]), 0)
            self.assertEqual(len(vip["trip_result"]["date_tbd_trips"]), 1)

            conflict = build_agent_response(workbook_path, "Malak Yasser Mohamed", "1112223333", "international", "20")
            self.assertEqual(conflict["match_status"], "single_match")
            self.assertEqual(conflict["name_match_status"], "conflict")
            self.assertTrue(conflict["handoff_required"])
            self.assertEqual(conflict["handoff_reason"], "phone_name_conflict")
            self.assertIn("human_review_identity_conflict", conflict["actions"])

            new_customer = build_agent_response(workbook_path, "Sara", "01009998877", "local", "")
            self.assertEqual(new_customer["match_status"], "not_found")
            self.assertIn("collect_new_traveler_data", new_customer["actions"])
            self.assertEqual(len(new_customer["trip_result"]["open_trips"]), 1)
        finally:
            shutil.rmtree(tmp_path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
