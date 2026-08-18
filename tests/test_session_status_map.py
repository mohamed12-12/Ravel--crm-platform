"""server.py must not keep its own copy of the stage->status text map.

Before this fix, server.py's `_serialize_session` had its own literal dict
duplicating (most of) `tool_calling_runtime.SAFE_STATUS_MAP`. Four stages --
`starting`, `group_nationality_type_required`, `group_nationality_counts_required`,
`trip_media_shared` -- were added to SAFE_STATUS_MAP across earlier phases and
never copied into server.py's map, so a live session sitting in one of those
stages rendered "Understanding request" in the UI while the backend state was
correct. This pins the fix: server.py now builds its map from SAFE_STATUS_MAP
plus only the stages unique to the legacy deterministic runtime, so a stage
added to SAFE_STATUS_MAP can never again silently miss the UI.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SYSTEM_ROOT = Path(__file__).resolve().parent.parent / "apps" / "api"
if str(SYSTEM_ROOT) not in sys.path:
    sys.path.insert(0, str(SYSTEM_ROOT))


class SessionStatusMapTests(unittest.TestCase):
    def setUp(self) -> None:
        from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import SAFE_STATUS_MAP
        from services.ai_agent.ai_agent_app.server import SESSION_STATUS_MAP

        self.safe_status_map = SAFE_STATUS_MAP
        self.session_status_map = SESSION_STATUS_MAP

    def test_every_tool_calling_stage_is_covered(self) -> None:
        """The bug: a stage present in SAFE_STATUS_MAP but absent from the
        server's map falls through to the generic default instead of its
        real status text."""
        missing = [stage for stage in self.safe_status_map if stage not in self.session_status_map]
        self.assertEqual(missing, [])

    def test_the_four_stages_that_previously_fell_through_now_resolve(self) -> None:
        previously_broken = {
            "starting": "Ready",
            "group_nationality_type_required": "Waiting for customer response",
            "group_nationality_counts_required": "Waiting for customer response",
            "trip_media_shared": "Trip media shared",
        }
        for stage, expected in previously_broken.items():
            with self.subTest(stage=stage):
                self.assertEqual(self.session_status_map.get(stage), expected)
                self.assertNotEqual(
                    self.session_status_map.get(stage, "Understanding request"),
                    "Understanding request",
                )

    def test_tool_calling_stages_take_precedence_over_any_legacy_overlap(self) -> None:
        """Where a stage name is shared, SAFE_STATUS_MAP is the source of
        truth -- a second, independently-edited copy is exactly what caused
        the drift in the first place."""
        for stage, expected_text in self.safe_status_map.items():
            with self.subTest(stage=stage):
                self.assertEqual(self.session_status_map[stage], expected_text)

    def test_legacy_deterministic_runtime_stages_are_still_covered(self) -> None:
        """SAFE_STATUS_MAP only knows the tool_calling runtime's stages --
        the deterministic SessionFlowManager stages are a real, separate
        vocabulary and must not be dropped by unifying the two maps."""
        legacy_only_stages = (
            "awaiting_phone",
            "traveler_profile_incomplete",
            "traveler_creation_confirmation_required",
            "public_trip_details",
            "trip_preferences_incomplete",
            "unable_to_continue",
            "awaiting_country_code",
            "awaiting_intake",
            "awaiting_trip_type",
            "awaiting_confirmation",
            "awaiting_group_size",
            "awaiting_room_type",
            "awaiting_flight",
            "awaiting_currency",
            "awaiting_clarification",
            "gemini_conversation",
            "ready",
            "completed",
            "handed_off",
            "cancelled",
        )
        for stage in legacy_only_stages:
            with self.subTest(stage=stage):
                self.assertNotIn(stage, self.safe_status_map)
                self.assertIn(stage, self.session_status_map)

    def test_an_unknown_stage_still_falls_back_safely(self) -> None:
        self.assertNotIn("some_future_stage_nobody_added_yet", self.session_status_map)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
