from __future__ import annotations

import unittest

from app.sheets.sheets_adapter import FakeWorkbook, SheetRowAdapter
from scripts.phase0_cleanup import INTERACTIONS_SHEET, ensure_headers
from scripts.phase1_readonly_agent import TRAVELERS_EXPECTED_HEADERS, TRIPS_EXPECTED_HEADERS
from scripts.phase2_controlled_agent import run_phase2_from_wb
from scripts.phase4_sales_intelligence import ensure_leads_sheet


class GoogleSheetsAdapterTests(unittest.TestCase):
    def test_run_phase2_from_wb_uses_header_names_with_sheet_adapter(self) -> None:
        traveler_headers = [
            "Status",
            "Traveler ID",
            "Full Name",
            "First Name",
            "Last Name",
            "Birthday",
            "Gender",
            "Nationality",
            "Code",
            "WhatsApp",
            "Email",
            "Loc. Trips",
            "Int. Trips",
            "Total trips",
            "Integrated WhatsApp",
            "Phone Lookup Key",
            "Lead Source",
            "Created At",
            "Last Contacted At",
            "Agent Notes",
            "Data Audit",
            "Normalized WhatsApp",
        ]
        travelers = SheetRowAdapter(
            [
                traveler_headers,
                ["", "TR00522", "Existing Traveler", "", "", "", "", "", "20", "1000000000", "", 0, 0, 0, "+201000000000", "20:1000000000", "", "", "", "", "", "+201000000000"],
            ],
            title="Travelers",
        )
        trips = SheetRowAdapter(
            [
                [],
                ["Trip ID", "Trip Name", "Type", "Year", "", "Start Date", "End Date", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "Sales Status", "Data Audit"],
                ["RT-LOC-26-001", "Siwa", "Local", "2026", "", "2026-08-01", "2026-08-03", "", "", "", "3", "2", "1", "", "", "", "", "", "", "", "", "", "", "", "", "Open", ""],
            ],
            title="Trips",
        )
        interactions = SheetRowAdapter(
            [
                [
                    "Interaction ID",
                    "Timestamp",
                    "Channel",
                    "Customer Name",
                    "Raw Phone",
                    "Integrated WhatsApp",
                    "Phone Lookup Key",
                    "Traveler ID",
                    "Matched Row",
                    "Status Snapshot",
                    "Intent",
                    "Trip Type",
                    "Suggested Trips",
                    "Action Taken",
                    "Handoff Required",
                    "Handoff Reason",
                    "Agent Notes",
                ]
            ],
            title=INTERACTIONS_SHEET,
        )
        leads = SheetRowAdapter([[]], title="Leads")
        wb = FakeWorkbook({"Travelers": travelers, "Trips": trips, INTERACTIONS_SHEET: interactions, "Leads": leads})

        traveler_header_map = ensure_headers(travelers, 1, TRAVELERS_EXPECTED_HEADERS, ["Birthday", "Gender", "Nationality", "Lead Source", "Created At", "Last Contacted At", "Agent Notes", "Normalized WhatsApp"])
        trip_header_map = ensure_headers(trips, 2, TRIPS_EXPECTED_HEADERS, [])
        lead_ws, lead_headers = ensure_leads_sheet(wb)
        interaction_headers = {
            str(interactions.cell(1, col).value): col
            for col in range(1, interactions.max_column + 1)
            if interactions.cell(1, col).value
        }

        result, mutations = run_phase2_from_wb(
            wb=wb,
            travelers_ws=travelers,
            trips_ws=trips,
            interactions_ws=interactions,
            leads_ws=lead_ws,
            traveler_headers=traveler_header_map,
            trip_headers=trip_header_map,
            interaction_headers=interaction_headers,
            lead_headers=lead_headers,
            full_name="New Customer",
            raw_phone="01012345678",
            country_code="20",
            trip_type="local",
            channel="web-demo",
            source="test",
            agent_notes="adapter regression",
        )

        self.assertEqual(result["write_result"]["created_traveler"]["traveler_id"], "TR00523")
        self.assertEqual(travelers.cell(3, traveler_header_map["Traveler ID"]).value, "TR00523")
        self.assertEqual(travelers.cell(3, traveler_header_map["Full Name"]).value, "New Customer")
        self.assertEqual(travelers.cell(3, traveler_header_map["Code"]).value, "20")
        self.assertEqual(travelers.cell(3, traveler_header_map["WhatsApp"]).value, "1012345678")
        self.assertGreater(len(mutations), 0)


if __name__ == "__main__":
    unittest.main()
