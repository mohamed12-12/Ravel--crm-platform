#!/usr/bin/env python
"""List bookings whose stored refund exceeds what the refund rule now allows.

Read-only by design. The refund ceiling introduced in
app/services/refund_limits.py applies to new saves only -- historical refunds
are left exactly as recorded, because a number someone entered deliberately is
evidence, not a bug to be silently corrected. This report is how you find them
so a human can decide what, if anything, each one needs.

Run against whatever DATABASE_URL the CRM itself uses:

    python scripts/report_legacy_refund_exceptions.py

Add --json for machine-readable output.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "apps" / "api"
for candidate in (str(REPO_ROOT), str(API_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)


def collect_exceptions() -> list[dict]:
    from app import create_app
    from app.extensions import db
    from app.models.booking import TripBooking
    from app.models.trip import Trip
    from app.services.refund_limits import format_money, refund_allowance_for_booking

    app = create_app()
    findings: list[dict] = []
    with app.app_context():
        trips = {trip.trip_id: trip for trip in Trip.query.all()}
        bookings = TripBooking.query.filter(TripBooking.refund_amount.isnot(None)).all()
        for booking in bookings:
            allowance = refund_allowance_for_booking(booking, trips.get(booking.trip_id))
            if allowance.maximum_refund is None:
                # Cannot price the booking, so there is nothing to compare
                # against. Reported separately rather than counted as a
                # violation -- absence of evidence is not a breach.
                if float(booking.refund_amount or 0) > 0:
                    findings.append({
                        "booking_id": booking.booking_id,
                        "traveler_id": booking.traveler_id,
                        "verdict": "unverifiable",
                        "refund_amount": booking.refund_amount,
                        "currency": booking.currency or "",
                        "reason": "Booking has no priceable trip/room/currency combination.",
                    })
                continue
            if float(booking.refund_amount) > allowance.maximum_refund + 0.005:
                findings.append({
                    "booking_id": booking.booking_id,
                    "traveler_id": booking.traveler_id,
                    "verdict": "exceeds_ceiling",
                    "refund_amount": booking.refund_amount,
                    "maximum_refund": allowance.maximum_refund,
                    "currency": allowance.currency,
                    "payment_status": booking.payment_status,
                    "basis": allowance.basis,
                    "excess": round(float(booking.refund_amount) - allowance.maximum_refund, 2),
                    "display": (
                        f"{format_money(booking.refund_amount, allowance.currency)} refunded against a "
                        f"maximum of {format_money(allowance.maximum_refund, allowance.currency)}"
                    ),
                })
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = parser.parse_args()

    findings = collect_exceptions()
    if args.json:
        print(json.dumps(findings, indent=2, default=str))
        return 0

    breaches = [f for f in findings if f["verdict"] == "exceeds_ceiling"]
    unverifiable = [f for f in findings if f["verdict"] == "unverifiable"]

    if not breaches:
        print("No stored refund exceeds the refund ceiling.")
    else:
        print(f"{len(breaches)} booking(s) with a refund above the ceiling:")
        for finding in breaches:
            print(
                f"  {finding['booking_id']}  {finding['display']}"
                f"  (payment status: {finding['payment_status'] or 'unset'}, basis: {finding['basis']})"
            )
    if unverifiable:
        print(f"\n{len(unverifiable)} booking(s) carry a refund that cannot be checked:")
        for finding in unverifiable:
            print(f"  {finding['booking_id']}  {finding['refund_amount']} {finding['currency']}  {finding['reason']}")
    print("\nNothing was modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
