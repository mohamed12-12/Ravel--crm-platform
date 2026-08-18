#!/usr/bin/env python
"""Populate ai_agent_sessions.traveler_id/lead_id/raw_phone for existing rows.

DurableSessionStore.save() has always resolved a conversation's traveler and
lead the moment they become known -- but only into the row's JSON `payload`
blob, never into a queryable SQL column, so the only way to find which
conversation belongs to which traveler was to parse every row's JSON. New
columns fix that for every save going forward (see session_store.py); this
script fills them in for rows written before the columns existed.

Dry-run by default. Nothing is written until you pass --apply, matching the
preview-then-apply convention app/services/importer.py and
scripts/backfill_booking_ledger.py already use.

Only rows with a NULL/blank column are touched, and only to fill that gap --
a row that already has a value (from a save after this deploy) is left
exactly as it is. This is a backfill, not a correction: it never overwrites
what a real save already wrote.

    venv/bin/python scripts/backfill_session_identity_columns.py
    venv/bin/python scripts/backfill_session_identity_columns.py --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "apps" / "api"
for candidate in (str(REPO_ROOT), str(API_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from services.crm.system_services.db_uri import safe_database_uri  # noqa: E402


def _resolve_identity(payload: dict) -> tuple[str, str, str]:
    """(traveler_id, lead_id, raw_phone) from one row's parsed JSON payload.

    Mirrors ToolCallingSessionRuntime._linked_ids() exactly -- reconstructing
    a real SessionState from the payload and calling that method directly,
    rather than re-deriving the resolution order by hand, so this script
    cannot silently drift from what the live runtime actually does.
    """
    from services.ai_agent.ai_agent_app.agent.session_store import DurableSessionStore
    from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime

    session = DurableSessionStore._session_from_payload(str(payload.get("id") or ""), payload)
    linked = ToolCallingSessionRuntime._linked_ids(session)
    traveler_id = linked["traveler_id"] or str(session.traveler_id or "").strip()
    lead_id = linked["lead_id"] or str(session.lead_id or "").strip()
    raw_phone = str(session.raw_phone or "").strip()
    return traveler_id, lead_id, raw_phone


def plan_backfill(rows: list[dict]) -> tuple[list[dict], dict]:
    """Work out every row that would be updated. Reads only."""
    import json

    planned: list[dict] = []
    counts = {
        "rows_examined": len(rows),
        "already_populated": 0,
        "resolved_traveler_id": 0,
        "resolved_lead_id": 0,
        "resolved_raw_phone": 0,
        "nothing_resolvable": 0,
    }

    for row in rows:
        needs_traveler = not row["traveler_id"]
        needs_lead = not row["lead_id"]
        needs_phone = not row["raw_phone"]
        if not (needs_traveler or needs_lead or needs_phone):
            counts["already_populated"] += 1
            continue

        try:
            payload = json.loads(row["payload"] or "{}")
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        traveler_id, lead_id, raw_phone = _resolve_identity(payload)
        update: dict = {}
        if needs_traveler and traveler_id:
            update["traveler_id"] = traveler_id
            counts["resolved_traveler_id"] += 1
        if needs_lead and lead_id:
            update["lead_id"] = lead_id
            counts["resolved_lead_id"] += 1
        if needs_phone and raw_phone:
            update["raw_phone"] = raw_phone
            counts["resolved_raw_phone"] += 1

        if update:
            planned.append({"session_id": row["session_id"], **update})
        else:
            counts["nothing_resolvable"] += 1

    return planned, counts


def apply_backfill(engine, planned: list[dict]) -> int:
    from sqlalchemy import text

    written = 0
    with engine.begin() as connection:
        for item in planned:
            session_id = item["session_id"]
            set_clauses = [f"{column} = :{column}" for column in item if column != "session_id"]
            connection.execute(
                text(f"UPDATE ai_agent_sessions SET {', '.join(set_clauses)} WHERE session_id = :session_id"),
                item,
            )
            written += 1
    return written


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill ai_agent_sessions.traveler_id/lead_id/raw_phone from existing payloads.",
        epilog="Run with the CRM's own interpreter: venv/bin/python scripts/backfill_session_identity_columns.py",
    )
    parser.add_argument("--apply", action="store_true", help="actually write; omit for a dry run")
    args = parser.parse_args()

    from sqlalchemy import create_engine, text

    from services.ai_agent.ai_agent_app.agent.session_store import _coerce_database_url, _default_sqlite_url
    import os

    database_url = _coerce_database_url(
        os.getenv("AI_AGENT_SESSION_DATABASE_URL", "").strip()
        or os.getenv("DATABASE_URL", "").strip()
        or _default_sqlite_url()
    )
    print(f"Database: {safe_database_uri(database_url)}")

    engine = create_engine(database_url)
    with engine.connect() as connection:
        try:
            rows = [
                dict(row)
                for row in connection.execute(
                    text("SELECT session_id, payload, traveler_id, lead_id, raw_phone FROM ai_agent_sessions")
                ).mappings()
            ]
        except Exception as exc:
            print(f"\nCould not read ai_agent_sessions: {exc}")
            print("If the columns don't exist yet, start the AI agent service once first --")
            print("DurableSessionStore.ensure_schema() adds them automatically on startup.")
            return 1

    if not rows:
        print("\nNo sessions found at all. That almost certainly means this ran against ")
        print("the wrong database rather than that there are genuinely no sessions.")
        return 1

    planned, counts = plan_backfill(rows)
    print(f"\nRows examined: {counts['rows_examined']}")
    print(f"  already populated (skipped)   {counts['already_populated']:>5}")
    print(f"  traveler_id resolved            {counts['resolved_traveler_id']:>5}")
    print(f"  lead_id resolved                {counts['resolved_lead_id']:>5}")
    print(f"  raw_phone resolved              {counts['resolved_raw_phone']:>5}")
    print(f"  nothing resolvable               {counts['nothing_resolvable']:>5}")
    print(f"  -- rows that would be updated  {len(planned):>5}")

    if not args.apply:
        print("\nDry run. Nothing was written. Re-run with --apply to commit these updates.")
        return 0

    written = apply_backfill(engine, planned)
    print(f"\nUpdated {written} row(s). No row's payload, version, or any other column was touched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
