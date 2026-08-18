"""ai_agent_sessions gains traveler_id/lead_id/raw_phone columns (Phase 7 step 1).

Before this, the only way to know which traveler a conversation belonged to
was to parse every row's JSON `payload` blob -- there was no SQL column to
query, and no CRM page ever surfaced a real transcript. This pins:

1. DurableSessionStore writes the three columns on save, on both a brand new
   table and one that pre-dates them (the self-healing ALTER TABLE path).
2. ToolCallingSessionRuntime._build_context() persists the resolved identity
   onto the SessionState object itself, not just into the returned context
   dict, and never regresses an already-known id back to blank.
"""
from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from services.ai_agent.ai_agent_app.agent.session_flow import SessionState
from services.ai_agent.ai_agent_app.agent.session_store import DurableSessionStore
from tests.test_phase9_production_reliability import reliability_runtime  # noqa: F401  (shared fixture)


class SessionIdentityColumnsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root = Path(".tmp-test-workdirs") / "session-identity-columns"
        self.tmp_root.mkdir(parents=True, exist_ok=True)

    def _db_path(self, name: str) -> Path:
        path = self.tmp_root / name
        if path.exists():
            path.unlink()
        return path

    def test_a_fresh_table_gets_the_identity_columns_and_they_are_written(self) -> None:
        db_path = self._db_path("fresh.db")
        store = DurableSessionStore(database_url=f"sqlite:///{db_path.as_posix()}")

        session = SessionState(id="sess-fresh", raw_phone="201000000000", traveler_id="TR00001", lead_id="LD00001")
        store.save(session)

        loaded = store.load("sess-fresh")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded[0].traveler_id, "TR00001")
        self.assertEqual(loaded[0].lead_id, "LD00001")
        self.assertEqual(loaded[0].raw_phone, "201000000000")

        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT traveler_id, lead_id, raw_phone FROM ai_agent_sessions WHERE session_id = ?",
                ("sess-fresh",),
            ).fetchone()
        self.assertEqual(row, ("TR00001", "LD00001", "201000000000"))

    def test_an_unresolved_identity_is_stored_as_null_not_empty_string(self) -> None:
        """An indexed column full of '' would match every not-yet-identified
        session on an equality lookup; NULL correctly matches none of them."""
        db_path = self._db_path("nulls.db")
        store = DurableSessionStore(database_url=f"sqlite:///{db_path.as_posix()}")
        store.save(SessionState(id="sess-blank"))

        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT traveler_id, lead_id, raw_phone FROM ai_agent_sessions WHERE session_id = ?",
                ("sess-blank",),
            ).fetchone()
        self.assertEqual(row, (None, None, None))

    def test_a_table_that_predates_the_columns_is_migrated_in_place(self) -> None:
        """The self-healing path: production already has rows on the old
        schema, and ensure_schema() must add the columns without touching --
        or losing -- what is already there."""
        db_path = self._db_path("legacy.db")
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE ai_agent_sessions (
                    session_id VARCHAR(64) PRIMARY KEY,
                    schema_version INTEGER NOT NULL DEFAULT 1,
                    payload TEXT NOT NULL,
                    agent_state TEXT,
                    version INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP,
                    locked_until TIMESTAMP,
                    lock_owner VARCHAR(80),
                    last_message_key VARCHAR(160)
                )
                """
            )
            conn.execute(
                "INSERT INTO ai_agent_sessions (session_id, payload, version) VALUES (?, ?, 0)",
                ("sess-legacy", '{"id": "sess-legacy", "raw_phone": "201099998888"}'),
            )
            conn.commit()

        store = DurableSessionStore(database_url=f"sqlite:///{db_path.as_posix()}")

        # The pre-existing row survives the migration and still loads.
        loaded = store.load("sess-legacy")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded[0].raw_phone, "201099998888")

        # A brand new save on the now-migrated table populates the columns.
        store.save(SessionState(id="sess-new", traveler_id="TR00099", raw_phone="201055551234"))

        with sqlite3.connect(db_path) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(ai_agent_sessions)").fetchall()}
            self.assertTrue({"traveler_id", "lead_id", "raw_phone"}.issubset(cols))
            new_row = conn.execute(
                "SELECT traveler_id, raw_phone FROM ai_agent_sessions WHERE session_id = ?", ("sess-new",)
            ).fetchone()
            self.assertEqual(new_row, ("TR00099", "201055551234"))
            # The pre-existing row's new columns are NULL, not fabricated.
            legacy_row = conn.execute(
                "SELECT traveler_id, lead_id FROM ai_agent_sessions WHERE session_id = ?", ("sess-legacy",)
            ).fetchone()
            self.assertEqual(legacy_row, (None, None))

    def test_ensure_schema_is_idempotent_against_an_already_migrated_table(self) -> None:
        """A second DurableSessionStore instance (e.g. a second app worker)
        must not error re-adding columns that already exist."""
        db_path = self._db_path("idempotent.db")
        DurableSessionStore(database_url=f"sqlite:///{db_path.as_posix()}").ensure_schema()
        second_store = DurableSessionStore(database_url=f"sqlite:///{db_path.as_posix()}")
        second_store.ensure_schema()  # must not raise
        second_store.save(SessionState(id="sess-ok", traveler_id="TR00001"))
        self.assertIsNotNone(second_store.load("sess-ok"))

    def test_a_postgres_style_column_check_is_scoped_to_the_search_path(self) -> None:
        """Regression guard for the query shape, not a live Postgres connection:
        the information_schema lookup used on Postgres must filter by
        table_schema (via current_schemas), never bare table_name alone, or a
        same-named table in another schema could be misread as this one."""
        import inspect

        from services.ai_agent.ai_agent_app.agent import session_store

        source = inspect.getsource(session_store.DurableSessionStore._ensure_identity_columns)
        self.assertIn("current_schemas", source)
        self.assertIn("table_schema", source)


# --- ToolCallingSessionRuntime._build_context() identity persistence -------
# Plain pytest functions (not unittest.TestCase methods): the shared
# `reliability_runtime` fixture is injected by parameter, matching the
# pattern already used throughout test_phase9_production_reliability.py and
# test_phase10_production_deployment_readiness.py -- pytest does not inject
# fixtures into unittest.TestCase methods.

def test_build_context_persists_traveler_id_and_lead_id_onto_the_session(reliability_runtime) -> None:
    """DurableSessionStore.save() only ever reads off the SessionState object,
    so a value that only exists in the returned context dict is invisible to
    it -- the identity must land on `session` itself."""
    runtime, _settings = reliability_runtime
    session = runtime.create_session()
    session.final_result = {
        "traveler": {"traveler_id": "TR00777"},
        "lead_id": "LD00777",
    }

    assert session.traveler_id == ""
    assert session.lead_id == ""

    runtime._build_context(session, "hello")

    assert session.traveler_id == "TR00777"
    assert session.lead_id == "LD00777"


def test_build_context_never_regresses_an_already_known_identity(reliability_runtime) -> None:
    """A later turn whose final_result/preview happens not to carry the id
    forward must not blank out an identity already resolved earlier -- that
    would silently disconnect the conversation from its traveler."""
    runtime, _settings = reliability_runtime
    session = runtime.create_session()
    session.traveler_id = "TR00777"
    session.lead_id = "LD00777"
    session.final_result = None
    session.preview = None

    runtime._build_context(session, "some later message")

    assert session.traveler_id == "TR00777"
    assert session.lead_id == "LD00777"


def test_build_context_resolves_traveler_id_from_preview_when_final_result_is_absent(reliability_runtime) -> None:
    runtime, _settings = reliability_runtime
    session = runtime.create_session()
    session.preview = {"traveler": {"traveler_id": "TR00585"}, "workflow": {}}
    session.duplicate_lead_choice = "new"  # skip the open-lead CRM lookup branch

    runtime._build_context(session, "hi")

    assert session.traveler_id == "TR00585"


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
