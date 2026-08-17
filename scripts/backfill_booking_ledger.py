#!/usr/bin/env python
"""Populate the booking payment ledger from what history actually exists.

Dry-run by default. Nothing is written until you pass --apply, matching the
preview-then-apply convention app/services/importer.py already uses for
spreadsheet imports.

Four categories, in strict priority order. A booking is handled by the first
one it qualifies for:

  A  migration_workbook          Real amounts, dates and methods from the
                                 legacy workbook's First/Second Deposit
                                 columns. Only ~31 of 850 bookings have any.
                                 Requires --workbook.
  B  migration_legacy_refund     One refund per booking carrying a non-zero
                                 refund_amount. The figure is exact; the date
                                 was never recorded, so date_precision is
                                 'unknown'.
  C  migration_status_inferred   A synthetic payment equal to the booking
                                 value for "Fully Paid" bookings. This is the
                                 only place the backfill writes a number
                                 nobody typed, so it is OFF unless you pass
                                 --infer-fully-paid.
  D  (nothing)                   Pending, Deposit Paid, and refund statuses
                                 with no amount. The size of their payments
                                 is genuinely unknown and inventing one would
                                 defeat the point of the ledger.

Re-runnable: every row carries an idempotency key, so a second pass inserts
nothing. Nothing is ever updated or deleted -- not a booking, not a refund
amount, not an existing transaction.

    venv/bin/python scripts/backfill_booking_ledger.py
    venv/bin/python scripts/backfill_booking_ledger.py --workbook "docs/private/.../RT - Travelers Database.xlsx"
    venv/bin/python scripts/backfill_booking_ledger.py --apply
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "apps" / "api"
for candidate in (str(REPO_ROOT), str(API_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

WORKBOOK_SHEET = "Trip Bookings"
WORKBOOK_HEADER_ROW = 2  # 1-indexed; row 1 holds the merged deposit group labels


def _clean(value) -> str:
    return "" if value is None else str(value).strip()


def _parse_amount(value) -> float | None:
    text = _clean(value).replace(",", "").replace("$", "")
    if not text:
        return None
    try:
        amount = float(text)
    except ValueError:
        return None
    return amount if amount > 0 else None


def _parse_date(value) -> date | None:
    if value is None or _clean(value) == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(_clean(value), fmt).date()
        except ValueError:
            continue
    return None


def read_workbook_deposits(workbook_path: Path) -> dict[str, list[dict]]:
    """Real deposits keyed by booking id, from the legacy source workbook.

    Row 1 groups the columns as "First Deposit" / "Second Deposit"; row 2 has
    Currency, Amount, Date, Payment Method for each. Duplicate header names
    are why the original migration saw "Amount #2" -- here the blocks are read
    positionally instead, which also recovers the second block's own currency
    that the original migration ignored.
    """
    import openpyxl

    deposits: dict[str, list[dict]] = {}
    workbook = openpyxl.load_workbook(workbook_path, read_only=True, data_only=True)
    try:
        if WORKBOOK_SHEET not in workbook.sheetnames:
            raise SystemExit(f"Workbook has no '{WORKBOOK_SHEET}' sheet: {workbook_path}")
        sheet = workbook[WORKBOOK_SHEET]
        rows = list(sheet.iter_rows(values_only=True))
        header = [_clean(cell) for cell in rows[WORKBOOK_HEADER_ROW - 1]]
        try:
            booking_col = header.index("Booking ID")
        except ValueError:
            raise SystemExit("Workbook 'Trip Bookings' sheet has no 'Booking ID' column.")

        # The two deposit blocks are the two runs of Currency/Amount/Date/Method.
        blocks: list[tuple[int, int, int, int]] = []
        for idx, name in enumerate(header):
            if name == "Currency" and header[idx:idx + 4] == ["Currency", "Amount", "Date", "Payment Method"]:
                blocks.append((idx, idx + 1, idx + 2, idx + 3))

        for row in rows[WORKBOOK_HEADER_ROW:]:
            booking_id = _clean(row[booking_col]) if booking_col < len(row) else ""
            if not booking_id:
                continue
            for ordinal, (c_currency, c_amount, c_date, c_method) in enumerate(blocks, start=1):
                if c_method >= len(row):
                    continue
                amount = _parse_amount(row[c_amount])
                if amount is None:
                    continue
                deposits.setdefault(booking_id, []).append({
                    "ordinal": ordinal,
                    "amount": amount,
                    "currency": _clean(row[c_currency]).upper(),
                    "occurred_on": _parse_date(row[c_date]),
                    "method": _clean(row[c_method]).lower() or None,
                })
    finally:
        workbook.close()
    return deposits


def plan_backfill(deposits_by_booking: dict[str, list[dict]], infer_fully_paid: bool) -> tuple[list[dict], Counter, list[str]]:
    """Work out every row that would be written. Reads only."""
    from app.models.booking import TripBooking
    from app.models.booking_transaction import (
        ENTRY_PAYMENT,
        ENTRY_REFUND,
        PRECISION_EXACT,
        PRECISION_UNKNOWN,
        SOURCE_MIGRATION_LEGACY_REFUND,
        SOURCE_MIGRATION_STATUS_INFERRED,
        SOURCE_MIGRATION_WORKBOOK,
    )
    from app.models.trip import Trip
    from app.services.refund_limits import booking_contract_value

    planned: list[dict] = []
    counts: Counter = Counter()
    warnings: list[str] = []

    trips = {trip.trip_id: trip for trip in Trip.query.all()}
    bookings = TripBooking.query.order_by(TripBooking.booking_id.asc()).all()
    counts["bookings_examined"] = len(bookings)

    for booking in bookings:
        currency = _clean(booking.currency).upper()
        fallback_date = (
            booking.draft_created_at.date()
            if isinstance(booking.draft_created_at, datetime)
            else (booking.draft_created_at or date.today())
        )

        # --- A: real deposits from the workbook -------------------------
        # A zero or missing amount is the absence of a payment, not a payment
        # of nothing, and must never become a placeholder row. read_workbook_
        # deposits already drops these, but the guard belongs here too: this
        # function trusts whatever mapping it is handed, and without it a zero
        # reaches the database and surfaces as a CHECK violation mid-run
        # rather than a clean skip.
        real_deposits = [
            deposit for deposit in (deposits_by_booking.get(booking.booking_id) or [])
            if deposit.get("amount") is not None and float(deposit["amount"]) > 0
        ]
        counts["skipped_non_positive_deposit"] += len(
            deposits_by_booking.get(booking.booking_id) or []
        ) - len(real_deposits)
        for deposit in real_deposits:
            deposit_currency = deposit["currency"] or currency
            if not deposit_currency:
                warnings.append(f"{booking.booking_id}: deposit {deposit['ordinal']} has no currency; skipped.")
                counts["skipped_no_currency"] += 1
                continue
            if currency and deposit_currency != currency:
                warnings.append(
                    f"{booking.booking_id}: deposit {deposit['ordinal']} is {deposit_currency} "
                    f"but the booking is {currency}; skipped rather than guessed."
                )
                counts["skipped_currency_mismatch"] += 1
                continue
            planned.append({
                "booking_id": booking.booking_id,
                "traveler_id": booking.traveler_id,
                "entry_type": ENTRY_PAYMENT,
                "amount": round(deposit["amount"], 2),
                "currency": deposit_currency,
                "occurred_on": deposit["occurred_on"] or fallback_date,
                "date_precision": PRECISION_EXACT if deposit["occurred_on"] else PRECISION_UNKNOWN,
                "method": deposit["method"],
                "source": SOURCE_MIGRATION_WORKBOOK,
                # The amount is real; only a missing date is estimated, and
                # date_precision already says so.
                "is_inferred": False,
                "notes": f"Deposit {deposit['ordinal']} imported from the legacy source workbook.",
                "idempotency_key": f"migration:workbook:{booking.booking_id}:{deposit['ordinal']}",
            })
            counts["A_workbook_payments"] += 1

        # --- C: synthetic payment for Fully Paid ------------------------
        if not real_deposits and _clean(booking.payment_status).lower() == "fully paid":
            value = booking_contract_value(
                trips.get(booking.trip_id),
                room_type=booking.room_type or "",
                currency=currency,
                group_size=booking.group_size,
            )
            if value is None:
                counts["C_skipped_unpriceable"] += 1
            elif not infer_fully_paid:
                counts["C_available_not_written"] += 1
            else:
                planned.append({
                    "booking_id": booking.booking_id,
                    "traveler_id": booking.traveler_id,
                    "entry_type": ENTRY_PAYMENT,
                    "amount": round(value, 2),
                    "currency": currency,
                    "occurred_on": fallback_date,
                    "date_precision": PRECISION_UNKNOWN,
                    "method": None,
                    "source": SOURCE_MIGRATION_STATUS_INFERRED,
                    "is_inferred": True,
                    "notes": (
                        "Reconstructed from a 'Fully Paid' status. No payment amount was ever "
                        "recorded for this booking; this equals the booking value."
                    ),
                    "idempotency_key": f"migration:status_inferred:{booking.booking_id}",
                })
                counts["C_status_inferred_payments"] += 1

        # --- B: the legacy refund scalar --------------------------------
        refund = booking.refund_amount
        if refund is not None and float(refund) > 0:
            if not currency:
                warnings.append(f"{booking.booking_id}: refund of {refund} has no currency; skipped.")
                counts["skipped_refund_no_currency"] += 1
            else:
                planned.append({
                    "booking_id": booking.booking_id,
                    "traveler_id": booking.traveler_id,
                    "entry_type": ENTRY_REFUND,
                    "amount": round(float(refund), 2),
                    "currency": currency,
                    "occurred_on": fallback_date,
                    "date_precision": PRECISION_UNKNOWN,
                    "method": None,
                    "source": SOURCE_MIGRATION_LEGACY_REFUND,
                    "is_inferred": True,
                    "reason": "Reconstructed from the legacy refund_amount total.",
                    "notes": (
                        "The legacy field held one running total with no date and no breakdown, "
                        "so this is the sum of all refunds rather than an individual one."
                    ),
                    "idempotency_key": f"migration:legacy_refund:{booking.booking_id}",
                })
                counts["B_legacy_refunds"] += 1

    return planned, counts, warnings


def apply_backfill(planned: list[dict]) -> int:
    from app.extensions import db
    from app.models.booking_transaction import BookingTransaction

    existing_keys = {
        key for (key,) in db.session.query(BookingTransaction.idempotency_key)
        .filter(BookingTransaction.idempotency_key.isnot(None)).all()
    }
    written = 0
    for row in planned:
        if row["idempotency_key"] in existing_keys:
            continue
        db.session.add(BookingTransaction(**row))
        written += 1
    db.session.commit()
    return written


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill the booking payment ledger from existing data.",
        epilog="Run with the CRM's own interpreter: venv/bin/python scripts/backfill_booking_ledger.py",
    )
    parser.add_argument("--workbook", default="", help="path to the legacy source workbook (category A)")
    parser.add_argument("--apply", action="store_true", help="actually write; omit for a dry run")
    parser.add_argument(
        "--infer-fully-paid", action="store_true",
        help="also create synthetic payments for Fully Paid bookings (category C)",
    )
    args = parser.parse_args()

    from app import create_app

    app = create_app()

    deposits: dict[str, list[dict]] = {}
    if args.workbook:
        workbook_path = Path(args.workbook)
        if not workbook_path.exists():
            print(f"Workbook not found: {workbook_path}")
            return 1
        deposits = read_workbook_deposits(workbook_path)
        print(f"Workbook: {workbook_path}")
        print(f"  bookings with at least one real deposit: {len(deposits)}")
        print(f"  real deposit rows found: {sum(len(v) for v in deposits.values())}\n")
    else:
        print("No --workbook given: category A (real historical deposits) will be skipped.\n")

    with app.app_context():
        print(f"Database: {app.config.get('SQLALCHEMY_DATABASE_URI')}")

        from sqlalchemy.exc import OperationalError

        try:
            planned, counts, warnings = plan_backfill(deposits, args.infer_fully_paid)
        except OperationalError as exc:
            # Almost always a database that has not had `flask db upgrade` run
            # against it. The raw SQLAlchemy traceback buries that in sixty
            # lines of stack, which is no use to whoever is running this on a
            # server at the time.
            print("\nThe database schema is out of date for this code.")
            print(f"  {str(exc.orig).strip()}")
            print("\nCheck the database's migration state first, from the repo root:")
            print("  venv/bin/python scripts/manage_migrations.py current")
            print("  venv/bin/python scripts/manage_migrations.py upgrade")
            return 1

        if counts["bookings_examined"] == 0:
            print(
                "\nNo bookings found at all. That almost certainly means this ran against "
                "the wrong database rather than that the CRM is empty."
            )
            return 1

        print(f"Bookings examined: {counts['bookings_examined']}\n")
        print("Planned transactions")
        print(f"  A  real workbook payments      {counts['A_workbook_payments']:>5}")
        print(f"  B  legacy refunds              {counts['B_legacy_refunds']:>5}")
        print(f"  C  inferred Fully Paid         {counts['C_status_inferred_payments']:>5}")
        if counts["C_available_not_written"]:
            print(
                f"     ({counts['C_available_not_written']} Fully Paid bookings could be inferred; "
                "pass --infer-fully-paid to include them)"
            )
        if counts["C_skipped_unpriceable"]:
            print(f"     ({counts['C_skipped_unpriceable']} Fully Paid bookings cannot be priced)")
        print(f"  ── total                       {len(planned):>5}")

        if warnings:
            print(f"\n{len(warnings)} row(s) skipped rather than guessed:")
            for warning in warnings[:20]:
                print(f"  {warning}")
            if len(warnings) > 20:
                print(f"  ... and {len(warnings) - 20} more")

        if not args.apply:
            print("\nDry run. Nothing was written. Re-run with --apply to commit these rows.")
            return 0

        written = apply_backfill(planned)
        print(f"\nWrote {written} transaction(s); {len(planned) - written} already existed.")
        print("No booking, refund amount or existing transaction was modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
