from __future__ import annotations

import logging
import unittest

from services.ai_agent.ai_agent_app.agent.tool_contracts import normalize_tool_result_contract
from services.ai_agent.ai_agent_app.agent.tool_registry import ToolSpec
from services.ai_agent.ai_agent_app.agent.write_result import (
    WriteOutcome,
    normalize_write_result,
)


class WriteResultBoundaryTests(unittest.TestCase):
    def test_every_known_sqlite_status_maps_correctly(self) -> None:
        # UnifiedCRMService (SQLite) convention.
        for status, expected in (
            ("success", WriteOutcome.SUCCESS),
            ("reused", WriteOutcome.SUCCESS),
            ("duplicate", WriteOutcome.SUCCESS),
            ("blocked", WriteOutcome.FAILURE),
            ("failed", WriteOutcome.FAILURE),
        ):
            with self.subTest(status=status):
                result = normalize_write_result(
                    {"write_result_contract": {"status": status, "record_id": "X1"}},
                    backend="sqlite",
                )
                self.assertEqual(result.outcome, expected)

    def test_every_known_postgres_status_maps_correctly(self) -> None:
        # PostgresAgentBridgeService convention.
        for status, expected in (
            ("created", WriteOutcome.SUCCESS),
            ("updated", WriteOutcome.SUCCESS),
            ("reused", WriteOutcome.SUCCESS),
            ("duplicate", WriteOutcome.SUCCESS),
            ("blocked", WriteOutcome.FAILURE),
        ):
            with self.subTest(status=status):
                result = normalize_write_result(
                    {"write_result_contract": {"status": status, "record_id": "X2"}},
                    backend="postgres",
                )
                self.assertEqual(result.outcome, expected)

    def test_unrecognized_status_is_unknown_and_logs_loudly(self) -> None:
        with self.assertLogs("rahma_agent", level=logging.ERROR) as captured:
            result = normalize_write_result(
                {"write_result_contract": {"status": "totally_new_status", "record_id": "X3"}},
                backend="postgres",
            )
        self.assertEqual(result.outcome, WriteOutcome.UNKNOWN)
        joined = "\n".join(captured.output)
        self.assertIn("UNRECOGNIZED write status", joined)
        self.assertIn("totally_new_status", joined)
        self.assertIn("postgres", joined)

    def test_missing_status_is_unknown_but_does_not_log(self) -> None:
        """A bare write result with no write_result_contract at all is a
        known legacy shape, not a new backend convention -- it must not
        trigger the loud UNKNOWN log meant for genuinely new statuses.
        """
        with self.assertNoLogs("rahma_agent", level=logging.ERROR):
            result = normalize_write_result({"booking_id": "BK1"}, backend="sqlite")
        self.assertEqual(result.outcome, WriteOutcome.UNKNOWN)
        self.assertEqual(result.raw_status, "")

    def test_record_id_extraction_across_record_types(self) -> None:
        booking = normalize_write_result({"booking_id": "BK00001", "write_result_contract": {"status": "created"}})
        lead = normalize_write_result({"lead_id": "LD00001", "write_result_contract": {"status": "created"}})
        traveler = normalize_write_result({"traveler_id": "TR00001", "write_result_contract": {"status": "created"}})
        self.assertEqual(booking.record_id, "BK00001")
        self.assertEqual(lead.record_id, "LD00001")
        self.assertEqual(traveler.record_id, "TR00001")

    def test_record_id_prefers_contract_record_id_over_top_level_keys(self) -> None:
        result = normalize_write_result(
            {
                "booking_id": "STALE-ID",
                "write_result_contract": {"status": "created", "record_id": "BK00042"},
            }
        )
        self.assertEqual(result.record_id, "BK00042")

    def test_the_original_bk000002_regression(self) -> None:
        """The literal scenario that caused a genuinely successful production
        booking to be reported to the customer as a failure: the Postgres
        bridge's write_result_contract used status="created", which was
        missing from the status map entirely.
        """
        raw_postgres_response = {
            "booking_id": "BK000002",
            "write_result_contract": {
                "status": "created",
                "record_id": "BK000002",
                "record_type": "booking",
                "executed": True,
            },
        }
        result = normalize_write_result(raw_postgres_response, backend="postgres")
        self.assertEqual(result.outcome, WriteOutcome.SUCCESS)
        self.assertEqual(result.record_id, "BK000002")


class ToolContractsUseTheBoundaryTests(unittest.TestCase):
    """normalize_tool_result_contract had its own, separately-audited
    instance of the same gap: it checked status in {"success", "reused",
    "duplicate"} directly, missing "created"/"updated" -- a latent variant
    of the BK000002 bug (a Postgres "created" write result would have
    skipped the missing-record-id safety check and defaulted executed=False
    if the write path ever omitted that key explicitly).
    """

    def _write_spec(self) -> ToolSpec:
        return ToolSpec(
            name="create_booking_draft",
            description="",
            input_schema={},
            output_schema={},
            allowed_write=True,
        )

    def test_created_status_is_not_flagged_as_missing_record_id(self) -> None:
        result = normalize_tool_result_contract(
            spec=self._write_spec(),
            result={
                "write_result_contract": {
                    "status": "created",
                    "record_id": "BK000002",
                    "executed": True,
                }
            },
        )
        errors = result.get("errors") or []
        self.assertFalse(any(e.get("code") == "missing_write_record_id" for e in errors))
        self.assertTrue(result.get("executed"))
        self.assertEqual(result.get("result_id"), "BK000002")

    def test_created_status_without_a_record_id_is_still_caught_as_malformed(self) -> None:
        result = normalize_tool_result_contract(
            spec=self._write_spec(),
            result={"write_result_contract": {"status": "created", "record_id": "", "executed": True}},
        )
        errors = result.get("errors") or []
        self.assertTrue(any(e.get("code") == "missing_write_record_id" for e in errors))

    def test_failed_status_is_still_not_executed(self) -> None:
        result = normalize_tool_result_contract(
            spec=self._write_spec(),
            result={"write_result_contract": {"status": "failed", "record_id": ""}},
        )
        self.assertFalse(result.get("executed"))


if __name__ == "__main__":
    unittest.main()
