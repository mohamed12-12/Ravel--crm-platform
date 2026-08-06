"""Production-path parity smoke test (Phase 4 / Task B.3).

Verifies, end to end:

  1. (Read-only) The real production Postgres schema matches Alembic's
     current head. Uses whatever DATABASE_URL is already configured in
     .env -- never writes, never runs `flask db upgrade`.
  2. A full scripted conversation walk (new adult traveler, local trip)
     against the REAL production code path -- PostgresAgentBridgeService,
     CRM_ACCESS_MODE=api, real GeminiWriteToolExecutor -- but against a
     disposable, throwaway SQLite-backed database dressed up as Postgres
     via the same technique tests/test_postgres_agent_bridge.py uses
     (SQLALCHEMY_DATABASE_URI overridden after app construction, so the
     ORM code path is real even though the SQL dialect is SQLite). This
     NEVER touches real production data. Verifies via direct DB read-back
     (not by trusting the conversation's own claims) that the traveler,
     lead, and booking all actually landed, and that a request for the
     genuinely-open seeded trip returns it (guards Section 3.5's bug).

Exits non-zero with a clear diagnostic on any failure, so this can be wired
into a pre-deploy CI gate.

Usage:
    python tools/production_parity_smoke_test.py
    python tools/production_parity_smoke_test.py --skip-schema-check
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import threading
import time
import uuid
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "apps" / "api"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

SCRATCH_ROOT = ROOT / ".tmp-run" / "production_parity_smoke"


def _fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    sys.exit(1)


def check_schema_matches_alembic_head() -> None:
    """Read-only: confirm the real production DB is at the head Alembic
    thinks it's at, and that head matches the latest migration file on
    disk. Does not run any DDL -- a plain SELECT against alembic_version.
    """
    print("[1/2] Checking live schema matches Alembic head (read-only)...")
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import text

    from app import create_app
    from app.extensions import db

    app = create_app(os.environ.get("FLASK_CONFIG", "development"))
    with app.app_context():
        try:
            db_current = db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()
        except Exception as exc:  # pragma: no cover - environment-dependent
            _fail(f"Could not read alembic_version from the live database: {exc}")
            return

    migrations_dir = ROOT / "database" / "migrations"
    alembic_cfg = Config()
    alembic_cfg.set_main_option("script_location", str(migrations_dir))
    script_dir = ScriptDirectory.from_config(alembic_cfg)
    file_heads = set(script_dir.get_heads())

    print(f"    Live database is at revision: {db_current}")
    print(f"    Migration files on disk have head(s): {sorted(file_heads)}")
    if db_current not in file_heads:
        _fail(
            f"Live database revision {db_current!r} does not match any migration file head "
            f"{sorted(file_heads)!r} -- schema drift detected."
        )
    print("    OK -- no DDL was run; this only reports state.")


def _seed_scratch_app(tmpdir: Path):
    for module_name in list(sys.modules):
        if module_name == "app" or module_name.startswith("app."):
            sys.modules.pop(module_name, None)
    db_path = (tmpdir / "smoke.db").resolve()
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    os.environ["RAHMA_SYSTEM_DB_PATH"] = str(db_path)
    os.environ["DATA_AUTHORITY"] = "crm"
    os.environ["CRM_ACCESS_MODE"] = "shared_service"
    os.environ["CRM_AUTH_ENABLED"] = "false"
    from app import create_app
    from app.extensions import db
    from app.models import Trip

    app = create_app("development")
    app.config.update(TESTING=False)
    with app.app_context():
        db.drop_all()
        db.create_all()
        db.session.add(
            Trip(
                trip_id="RT-SMOKE-LOCAL",
                trip_name="Smoke Test Local Trip",
                type="Local",
                sales_status="Open",
                start_date=date.today() + timedelta(days=20),
                end_date=date.today() + timedelta(days=28),
                single_total=6,
                single_remaining=6,
                double_total=6,
                double_remaining=6,
                triple_total=6,
                triple_remaining=6,
                public_price="1000$",
            )
        )
        db.session.commit()
    # Dress this app up as the real production code path: PostgresAgentCRMTools,
    # exercised via the ORM against a SQLite-backed engine.
    app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://smoke/redacted"
    return app


def run_scripted_conversation_walk() -> None:
    print("[2/2] Running a scripted conversation walk against the production code path...")
    tmpdir = SCRATCH_ROOT / uuid.uuid4().hex
    tmpdir.mkdir(parents=True, exist_ok=True)
    original_env = dict(os.environ)
    try:
        app = _seed_scratch_app(tmpdir)

        from werkzeug.serving import make_server

        server = make_server("127.0.0.1", 0, app)
        port = server.server_port
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        time.sleep(0.3)

        try:
            from services.ai_agent.ai_agent_app.config import load_settings
            from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime

            settings = replace(
                load_settings(),
                crm_access_mode="api",
                crm_api_base_url=f"http://127.0.0.1:{port}",
                crm_api_token="smoke-test-token",
            )
            runtime = ToolCallingSessionRuntime(settings=settings)
            session = runtime.create_session()

            phone = f"010{str(uuid.uuid4().int)[:8]}"
            steps = [
                phone,
                "Smoke Test Traveler",
                "Egyptian",
                "1/1/1995",
                "1",  # currency EGP
                "local",
                "1",  # select the seeded trip
                "boys",
                "single",
                "1",
                "yes",  # confirm the booking draft
            ]
            for text in steps:
                session = runtime.handle_message(session, text, gateway=None)

            reply = session.messages[-1]["text"]
            if not session.booking_completed:
                _fail(f"Conversation walk did not complete a booking. Last reply: {reply!r}")
            if "could not" in reply.lower():
                _fail(f"Booking was reported as failed to the customer. Reply: {reply!r}")

            traveler_id = str((session.preview or {}).get("traveler", {}).get("traveler_id") or "").strip()
            booking = session.booking_result if isinstance(session.booking_result, dict) else {}
            booking_id = str(booking.get("booking_id") or "").strip()
            if not traveler_id or not booking_id:
                _fail(f"Missing traveler_id ({traveler_id!r}) or booking_id ({booking_id!r}) after a claimed success.")

            print(f"    Conversation reported success: traveler={traveler_id} booking={booking_id}")
        finally:
            server.shutdown()

        # Independent DB read-back -- never trust the conversation's own claims.
        with app.app_context():
            from app.extensions import db
            from app.models import Lead, Traveler, TripBooking

            traveler_row = db.session.get(Traveler, traveler_id)
            booking_row = db.session.get(TripBooking, booking_id)
            if traveler_row is None:
                _fail(f"Traveler {traveler_id} was reported as created but does not exist in the database.")
            if booking_row is None:
                _fail(f"Booking {booking_id} was reported as created but does not exist in the database.")
            lead_row = Lead.query.filter_by(traveler_id=traveler_id).first()
            if lead_row is None:
                _fail(f"No lead found for traveler {traveler_id} after a claimed successful booking.")
            print(
                f"    Independent DB read-back confirmed: traveler={traveler_row.traveler_id} "
                f"lead={lead_row.lead_id} booking={booking_row.booking_id} "
                f"status={booking_row.booking_status}"
            )
    finally:
        os.environ.clear()
        os.environ.update(original_env)
        shutil.rmtree(tmpdir, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-schema-check",
        action="store_true",
        help="Skip the read-only production schema check (use if RDS is unreachable from this environment).",
    )
    args = parser.parse_args()

    if not args.skip_schema_check:
        check_schema_matches_alembic_head()
    else:
        print("[1/2] Skipped (--skip-schema-check).")

    run_scripted_conversation_walk()
    print("\nPASS: production-path parity smoke test succeeded.")


if __name__ == "__main__":
    main()
