from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone

from app import create_app
from app.extensions import db
from app.models.handoff import HandoffQueue
from app.models.traveler import Traveler


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def seed_duplicate_travelers() -> int:
    rows = [
        Traveler(
            traveler_id="TR90001",
            status="Active",
            full_name="Duplicate Alpha",
            whatsapp_raw="01099990001",
            integrated_whatsapp="+201099990001",
            normalized_whatsapp="+201099990001",
            phone_lookup_key="20:1099990001",
            created_at=_utc_now(),
        ),
        Traveler(
            traveler_id="TR90002",
            status="Active",
            full_name="Duplicate Beta",
            whatsapp_raw="01099990001",
            integrated_whatsapp="+201099990001",
            normalized_whatsapp="+201099990001",
            phone_lookup_key="20:1099990001",
            created_at=_utc_now(),
        ),
    ]
    inserted = 0
    for row in rows:
        if not db.session.get(Traveler, row.traveler_id):
            db.session.add(row)
            inserted += 1
    return inserted


def seed_pending_handoff() -> int:
    if db.session.get(HandoffQueue, "H-TEST-0001"):
        return 0
    handoff = HandoffQueue(
        handoff_id="H-TEST-0001",
        created_at=_utc_now(),
        traveler_id="TR90001",
        flow_key="booking",
        reason="duplicate_phone_match",
        priority="High",
        channel="WhatsApp",
        status="Pending",
        notes="Seeded handoff for UI validation",
    )
    db.session.add(handoff)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed duplicate traveler and handoff UI test cases.")
    parser.add_argument(
        "--mode",
        choices=("duplicates", "handoff", "all"),
        default="all",
        help="Which UI test data to seed.",
    )
    args = parser.parse_args()

    app = create_app()
    os.environ.setdefault("FLASK_ENV", "development")
    with app.app_context():
        inserted = 0
        if args.mode in {"duplicates", "all"}:
            inserted += seed_duplicate_travelers()
        if args.mode in {"handoff", "all"}:
            inserted += seed_pending_handoff()
        db.session.commit()

    print(f"Seeded {inserted} record(s) for mode={args.mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
