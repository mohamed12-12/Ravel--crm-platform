"""Regression test: the frozen operational DB fixture must remain untouched by tests.

Proof strategy
--------------
1. Capture the SHA256 hash of the frozen fixture DB *before* any app
   creation.
2. Point DATABASE_URL at a fresh temporary SQLite file.
3. Call create_app() and run db.drop_all() / db.create_all() against the
   temp DB — the most destructive operations a test can perform.
4. Assert the temp DB changed (schema exists).
5. Assert the fixture DB SHA256 is identical to step-1 hash.
6. Assert fixture DB counts remain travelers=571, trips=42,
   trip_bookings=48.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
import unittest
from contextlib import closing
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup — make sure the Flask app package is importable
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

OPERATIONAL_DB = (REPO_ROOT / "tests" / "fixtures" / "operational_db_fixture.db").resolve()

EXPECTED_COUNTS = {
    "travelers": 571,
    "trips": 42,
    "trip_bookings": 48,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_counts(db_path: Path) -> dict[str, int]:
    with closing(sqlite3.connect(str(db_path))) as con:
        cur = con.cursor()
        return {
            "travelers": cur.execute("SELECT COUNT(*) FROM travelers").fetchone()[0],
            "trips": cur.execute("SELECT COUNT(*) FROM trips").fetchone()[0],
            "trip_bookings": cur.execute("SELECT COUNT(*) FROM trip_bookings").fetchone()[0],
        }


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class OperationalDbProtectionTest(unittest.TestCase):
    """Proves that no test can mutate the frozen operational fixture."""

    # ------------------------------------------------------------------
    # Setup / teardown
    # ------------------------------------------------------------------

    def setUp(self) -> None:
        # Snapshot the operational DB *before* touching anything
        self.assertTrue(
            OPERATIONAL_DB.exists(),
            f"Operational DB not found at {OPERATIONAL_DB}",
        )
        self._original_hash = _sha256(OPERATIONAL_DB)

        # Preserve original env so we can restore it after the test
        self._original_env = dict(os.environ)

        # Create a completely isolated temporary database for the Flask app
        self._tmpdir = Path(".tmp-test-workdirs") / f"db-isolation-test-{uuid.uuid4().hex}"
        self._tmpdir.mkdir(parents=True, exist_ok=True)
        self._tmp_db = self._tmpdir / "test_isolation.db"

        # Override DATABASE_URL *before* create_app() so the runtime
        # injection in __init__.py picks up the temp path
        os.environ["DATABASE_URL"] = f"sqlite:///{self._tmp_db.resolve().as_posix()}"
        os.environ["RAHMA_SYSTEM_DB_PATH"] = str(self._tmp_db.resolve())

    def tearDown(self) -> None:
        # Restore env
        os.environ.clear()
        os.environ.update(self._original_env)
        # Clean up temp dir
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_operational_db_is_untouched_after_app_creation_and_schema_reset(self) -> None:
        """
        Core isolation proof:
        - Create the Flask app pointed at a temp DB.
        - Run the most destructive operations (drop_all / create_all).
        - Confirm the operational DB hash and counts are unchanged.
        """
        from app import create_app
        from app.extensions import db

        app = create_app("development")
        app.config["TESTING"] = True

        # Confirm the app is NOT using the operational DB
        active_uri: str = app.config["SQLALCHEMY_DATABASE_URI"]
        self.assertNotIn(
            "rahma_traveler_dev.db",
            active_uri,
            f"create_app() is still pointing at the operational DB: {active_uri}",
        )
        self.assertIn(
            "test_isolation",
            active_uri,
            f"create_app() is not using the expected temp DB: {active_uri}",
        )

        # Run destructive schema operations against the temp DB
        with app.app_context():
            db.drop_all()
            db.create_all()

        # 1. Temp DB must have changed (schema was created)
        self.assertTrue(
            self._tmp_db.exists(),
            "Temp DB file was not created — db.create_all() may not have run.",
        )
        self.assertGreater(
            self._tmp_db.stat().st_size,
            0,
            "Temp DB is unexpectedly empty after db.create_all().",
        )

        # 2. Operational DB hash must be identical to pre-test snapshot
        post_hash = _sha256(OPERATIONAL_DB)
        self.assertEqual(
            self._original_hash,
            post_hash,
            (
                "CRITICAL: operational DB was modified during the test!\n"
                f"  Before: {self._original_hash}\n"
                f"  After:  {post_hash}"
            ),
        )

        # 3. Operational DB counts must remain at production values
        counts = _read_counts(OPERATIONAL_DB)
        self.assertEqual(
            counts,
            EXPECTED_COUNTS,
            (
                f"Operational DB counts changed!\n"
                f"  Expected: {EXPECTED_COUNTS}\n"
                f"  Actual:   {counts}"
            ),
        )

    def test_environment_variable_isolation(self) -> None:
        """DATABASE_URL set in setUp must NOT leak into the operational DB path."""
        active_url = os.environ.get("DATABASE_URL", "")
        self.assertIn("test_isolation", active_url)
        self.assertNotIn("rahma_traveler_dev", active_url)

    def test_operational_db_counts_are_correct_baseline(self) -> None:
        """Standalone sanity-check that the operational DB has the expected data."""
        counts = _read_counts(OPERATIONAL_DB)
        self.assertEqual(counts["travelers"], 571)
        self.assertEqual(counts["trips"], 42)
        self.assertEqual(counts["trip_bookings"], 48)

    def test_operational_db_hash_is_stable_across_two_reads(self) -> None:
        """SHA256 should be deterministic for an untouched file."""
        hash_a = _sha256(OPERATIONAL_DB)
        hash_b = _sha256(OPERATIONAL_DB)
        self.assertEqual(hash_a, hash_b)
        self.assertEqual(hash_a, self._original_hash)


if __name__ == "__main__":
    unittest.main()
