"""Phase 4a: the payment ledger foundation.

The CRM has never been able to say how much a traveler actually paid. These
tests pin the table that fixes that, the immutability rules around it, and
the one place it currently changes behaviour: the Phase 2 refund ceiling
becomes exact for bookings that have a ledger, and unchanged for the ones
that do not.

Nothing here writes to trip_bookings. The ledger is additive.
"""
from __future__ import annotations

import os
import shutil
import sys
import unittest
import uuid
from datetime import date
from pathlib import Path

SYSTEM_ROOT = Path(__file__).resolve().parent.parent / "apps" / "api"
if str(SYSTEM_ROOT) not in sys.path:
    sys.path.insert(0, str(SYSTEM_ROOT))


def _create_temp_app():
    for module_name in list(sys.modules):
        if module_name == "app" or module_name.startswith("app."):
            sys.modules.pop(module_name, None)
    from app import create_app

    return create_app("development")


def _load_app_objects():
    from app.extensions import db
    from app.models.booking import TripBooking
    from app.models.booking_transaction import BookingTransaction
    from app.models.traveler import Traveler
    from app.models.trip import Trip
    from app.services import booking_audit, booking_ledger, refund_limits

    return db, TripBooking, BookingTransaction, Traveler, Trip, booking_audit, booking_ledger, refund_limits


class BookingLedgerTests(unittest.TestCase):
    """TRIP-1000 is priced 1,000 per person, so a solo booking is worth 1,000."""

    def setUp(self) -> None:
        self.tmpdir = Path(".tmp-test-workdirs") / f"ledger-{uuid.uuid4().hex}"
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.tmpdir / "app.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{self.db_path.resolve().as_posix()}"
        os.environ["RAHMA_SYSTEM_DB_PATH"] = str(self.db_path)
        os.environ["CRM_AUTH_ENABLED"] = "false"
        self.app = _create_temp_app()
        (
            self.db, self.TripBooking, self.BookingTransaction, self.Traveler,
            self.Trip, self.booking_audit, self.ledger, self.refund_limits,
        ) = _load_app_objects()
        self.app.config["TESTING"] = True
        with self.app.app_context():
            self.db.drop_all()
            self.db.create_all()
            self.db.session.add(
                self.Traveler(
                    traveler_id="TR100",
                    full_name="Ledger Traveler",
                    integrated_whatsapp="20:1000000000",
                    normalized_whatsapp="+201000000000",
                    phone_lookup_key="20:1000000000",
                )
            )
            self.db.session.add(
                self.Trip(
                    trip_id="TRIP-1000",
                    trip_name="Ledger Trip",
                    type="Local",
                    sales_status="Open",
                    single_total=9, double_total=9, triple_total=9,
                    single_remaining=9, double_remaining=9, triple_remaining=9,
                    draft_holds_single=0, draft_holds_double=0, draft_holds_triple=0,
                    public_price="1000",
                )
            )
            self.db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("RAHMA_SYSTEM_DB_PATH", None)

    # -- helpers ---------------------------------------------------------

    def _seed_booking(self, booking_id="B-LG-1", **overrides):
        fields = dict(
            booking_id=booking_id,
            trip_id="TRIP-1000",
            trip_name="Ledger Trip",
            traveler_id="TR100",
            traveler_name="Ledger Traveler",
            room_type="Double",
            currency="USD",
            group_size=1,
            booking_status="Confirmed",
            booking_source="Admin",
            payment_status="Fully Paid",
            priority="Medium",
            missing_info=False,
        )
        fields.update(overrides)
        with self.app.app_context():
            with self.booking_audit.suspend_booking_audit():
                self.db.session.add(self.TripBooking(**fields))
                self.db.session.commit()
        return booking_id

    def _add(self, booking_id="B-LG-1", entry_type="payment", amount=100.0, **overrides):
        fields = dict(
            booking_id=booking_id,
            traveler_id="TR100",
            entry_type=entry_type,
            amount=amount,
            currency="USD",
            occurred_on=date(2026, 3, 1),
            source="crm_ui",
        )
        fields.update(overrides)
        with self.app.app_context():
            entry = self.BookingTransaction(**fields)
            self.db.session.add(entry)
            self.db.session.commit()
            return entry.transaction_id

    def _totals(self, booking_id="B-LG-1"):
        with self.app.app_context():
            return self.ledger.ledger_totals(booking_id)

    # === the table itself ===============================================

    def test_a_payment_and_a_refund_sum_correctly(self) -> None:
        self._seed_booking()
        self._add(amount=400.0)
        self._add(amount=300.0)
        self._add(entry_type="refund", amount=120.0)

        totals = self._totals()
        self.assertEqual(totals.total_paid, 700.0)
        self.assertEqual(totals.total_refunded, 120.0)
        self.assertEqual(totals.remaining_refundable, 580.0)
        self.assertEqual(totals.entry_count, 3)

    def test_an_empty_ledger_is_distinguishable_from_a_zero_one(self) -> None:
        """The whole legacy fallback hinges on telling these apart."""
        self._seed_booking()
        with self.app.app_context():
            self.assertFalse(self.ledger.has_ledger("B-LG-1"))
        self._add(amount=1.0)
        with self.app.app_context():
            self.assertTrue(self.ledger.has_ledger("B-LG-1"))

    def test_each_transaction_gets_a_readable_reference(self) -> None:
        self._seed_booking()
        payment_id = self._add(amount=400.0)
        refund_id = self._add(entry_type="refund", amount=50.0)
        with self.app.app_context():
            payment = self.db.session.get(self.BookingTransaction, payment_id)
            refund = self.db.session.get(self.BookingTransaction, refund_id)
            self.assertTrue(payment.public_ref.startswith("PMT-"))
            self.assertTrue(refund.public_ref.startswith("RFD-"))
            self.assertNotEqual(payment.public_ref, refund.public_ref)

    def test_outstanding_balance_reports_unknown_rather_than_zero(self) -> None:
        self._seed_booking()
        self._add(amount=400.0)
        totals = self._totals()
        with self.app.app_context():
            self.assertEqual(self.ledger.outstanding_balance(1000.0, totals), 600.0)
            self.assertEqual(self.ledger.outstanding_balance(300.0, totals), -100.0)  # overpaid
            self.assertIsNone(self.ledger.outstanding_balance(None, totals))

    def test_a_non_positive_amount_is_refused_by_the_database(self) -> None:
        """The first financial CHECK constraint in this schema."""
        from sqlalchemy.exc import IntegrityError

        self._seed_booking()
        with self.app.app_context():
            self.db.session.add(
                self.BookingTransaction(
                    booking_id="B-LG-1", entry_type="payment", amount=0.0,
                    currency="USD", occurred_on=date(2026, 3, 1), source="crm_ui",
                )
            )
            with self.assertRaises(IntegrityError):
                self.db.session.commit()
            self.db.session.rollback()

    def test_an_unknown_entry_type_is_refused_by_the_database(self) -> None:
        from sqlalchemy.exc import IntegrityError

        self._seed_booking()
        with self.app.app_context():
            self.db.session.add(
                self.BookingTransaction(
                    booking_id="B-LG-1", entry_type="chargeback", amount=10.0,
                    currency="USD", occurred_on=date(2026, 3, 1), source="crm_ui",
                )
            )
            with self.assertRaises(IntegrityError):
                self.db.session.commit()
            self.db.session.rollback()

    def test_a_duplicate_idempotency_key_cannot_be_posted_twice(self) -> None:
        from sqlalchemy.exc import IntegrityError

        self._seed_booking()
        self._add(amount=100.0, idempotency_key="form:abc123")
        with self.app.app_context():
            self.db.session.add(
                self.BookingTransaction(
                    booking_id="B-LG-1", entry_type="payment", amount=100.0,
                    currency="USD", occurred_on=date(2026, 3, 1), source="crm_ui",
                    idempotency_key="form:abc123",
                )
            )
            with self.assertRaises(IntegrityError):
                self.db.session.commit()
            self.db.session.rollback()

    # === immutability ====================================================

    def test_a_recorded_transaction_cannot_be_edited(self) -> None:
        self._seed_booking()
        transaction_id = self._add(amount=400.0)
        with self.app.app_context():
            entry = self.db.session.get(self.BookingTransaction, transaction_id)
            entry.amount = 999.0
            with self.assertRaises(self.ledger.LedgerImmutableError):
                self.db.session.commit()
            self.db.session.rollback()
        self.assertEqual(self._totals().total_paid, 400.0)

    def test_a_recorded_transaction_cannot_be_deleted(self) -> None:
        self._seed_booking()
        transaction_id = self._add(amount=400.0)
        with self.app.app_context():
            entry = self.db.session.get(self.BookingTransaction, transaction_id)
            self.db.session.delete(entry)
            with self.assertRaises(self.ledger.LedgerImmutableError):
                self.db.session.commit()
            self.db.session.rollback()
        self.assertEqual(self._totals().total_paid, 400.0)

    def test_a_reversal_cancels_the_original_without_erasing_it(self) -> None:
        self._seed_booking()
        original = self._add(amount=400.0)
        self._add(amount=400.0, reverses_id=original, source="correction",
                  reason="Recorded against the wrong booking.")

        totals = self._totals()
        self.assertEqual(totals.total_paid, 0.0)
        # Both rows survive for audit.
        with self.app.app_context():
            self.assertEqual(
                self.BookingTransaction.query.filter_by(booking_id="B-LG-1").count(), 2
            )

    def test_a_reversal_leaves_other_transactions_alone(self) -> None:
        self._seed_booking()
        keep = self._add(amount=300.0)
        drop = self._add(amount=400.0)
        self._add(amount=400.0, reverses_id=drop, source="correction", reason="Duplicate.")

        self.assertEqual(self._totals().total_paid, 300.0)
        with self.app.app_context():
            self.assertIsNotNone(self.db.session.get(self.BookingTransaction, keep))

    # === the Phase 2 handover ===========================================

    def test_the_refund_ceiling_becomes_exact_when_a_ledger_exists(self) -> None:
        """A 1,000 booking with only 300 recorded as paid can be refunded 300,
        not 1,000. This is the improvement the whole phase is for."""
        self._seed_booking(payment_status="Fully Paid")
        self._add(amount=300.0)

        with self.app.app_context():
            booking = self.db.session.get(self.TripBooking, "B-LG-1")
            trip = self.db.session.get(self.Trip, "TRIP-1000")
            allowance = self.refund_limits.refund_allowance_for_booking(booking, trip)
        self.assertTrue(allowance.amount_paid_is_recorded)
        self.assertEqual(allowance.amount_paid, 300.0)
        self.assertEqual(allowance.maximum_refund, 300.0)

        response = self.client.post(
            "/bookings/B-LG-1/status",
            data={"payment_status": "Partial Refund", "refund_amount": "500"},
            follow_redirects=True,
        )
        self.assertIn("cannot exceed", response.get_data(as_text=True))
        with self.app.app_context():
            self.assertIsNone(self.db.session.get(self.TripBooking, "B-LG-1").refund_amount)

    def test_a_refund_within_what_was_actually_paid_is_allowed(self) -> None:
        self._seed_booking()
        self._add(amount=300.0)
        response = self.client.post(
            "/bookings/B-LG-1/status",
            data={"payment_status": "Partial Refund", "refund_amount": "250"},
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertEqual(self.db.session.get(self.TripBooking, "B-LG-1").refund_amount, 250.0)

    def test_ledger_refunds_reduce_what_can_still_be_refunded(self) -> None:
        self._seed_booking()
        self._add(amount=1000.0)
        self._add(entry_type="refund", amount=600.0)
        with self.app.app_context():
            booking = self.db.session.get(self.TripBooking, "B-LG-1")
            trip = self.db.session.get(self.Trip, "TRIP-1000")
            allowance = self.refund_limits.refund_allowance_for_booking(booking, trip)
        self.assertEqual(allowance.amount_paid, 1000.0)
        self.assertEqual(self._totals().remaining_refundable, 400.0)

    def test_a_booking_with_no_ledger_behaves_exactly_as_before(self) -> None:
        """The compatibility guarantee: most bookings will never have a ledger,
        and nothing about them may change."""
        self._seed_booking(payment_status="Fully Paid")
        with self.app.app_context():
            booking = self.db.session.get(self.TripBooking, "B-LG-1")
            trip = self.db.session.get(self.Trip, "TRIP-1000")
            allowance = self.refund_limits.refund_allowance_for_booking(booking, trip)
        self.assertEqual(allowance.maximum_refund, 1000.0)
        self.assertEqual(allowance.basis, self.refund_limits.BASIS_RECORDED_PAID)

        self._seed_booking(booking_id="B-LG-DEP", payment_status="Deposit Paid")
        with self.app.app_context():
            booking = self.db.session.get(self.TripBooking, "B-LG-DEP")
            trip = self.db.session.get(self.Trip, "TRIP-1000")
            allowance = self.refund_limits.refund_allowance_for_booking(booking, trip)
        self.assertFalse(allowance.amount_paid_is_recorded)
        self.assertEqual(allowance.basis, self.refund_limits.BASIS_BOOKING_VALUE)

    def test_a_ledger_read_failure_never_blocks_a_refund(self) -> None:
        """Financial validation degrading to the old behaviour is acceptable;
        a 500 on the refund form is not."""
        self._seed_booking()
        with self.app.app_context():
            import app.services.booking_ledger as ledger_module

            original = ledger_module.has_ledger
            ledger_module.has_ledger = lambda _bid: (_ for _ in ()).throw(RuntimeError("boom"))
            try:
                paid, recorded = self.refund_limits.resolve_amount_paid("Fully Paid", 1000.0, "B-LG-1")
            finally:
                ledger_module.has_ledger = original
        self.assertEqual(paid, 1000.0)
        self.assertTrue(recorded)

    # === revenue is deliberately untouched ==============================

    def test_revenue_is_not_yet_read_from_the_ledger(self) -> None:
        """Phase 3 still reads refund_amount, and must: the backfill makes the
        two identical, so switching would be a no-op that risks a real
        difference. Changing it belongs with the write path, not here."""
        self._seed_booking(payment_status="Partial Refund", refund_amount=120.0)
        self._add(entry_type="refund", amount=120.0, source="migration_legacy_refund", is_inferred=True)
        with self.app.app_context():
            from app.services.revenue import booking_revenue_breakdown

            booking = self.db.session.get(self.TripBooking, "B-LG-1")
            trip = self.db.session.get(self.Trip, "TRIP-1000")
            breakdown = booking_revenue_breakdown(booking, trip)
        self.assertEqual(breakdown.gross, 1000.0)
        self.assertEqual(breakdown.refunds, 120.0)
        self.assertEqual(breakdown.net, 880.0)

    # === the backfill ====================================================

    def _run_backfill(self, **kwargs):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        import importlib

        module = importlib.import_module("backfill_booking_ledger")
        importlib.reload(module)
        with self.app.app_context():
            planned, counts, warnings = module.plan_backfill(
                kwargs.get("deposits", {}), kwargs.get("infer_fully_paid", False)
            )
            written = module.apply_backfill(planned) if kwargs.get("apply") else 0
        return module, planned, counts, warnings, written

    def test_the_backfill_migrates_a_legacy_refund_without_touching_the_booking(self) -> None:
        self._seed_booking(payment_status="Partial Refund", refund_amount=120.0)
        _module, planned, counts, _warnings, written = self._run_backfill(apply=True)

        self.assertEqual(counts["B_legacy_refunds"], 1)
        self.assertEqual(written, 1)
        with self.app.app_context():
            entry = self.BookingTransaction.query.filter_by(booking_id="B-LG-1").one()
            self.assertEqual(entry.entry_type, "refund")
            self.assertEqual(entry.amount, 120.0)
            self.assertTrue(entry.is_inferred)
            self.assertEqual(entry.date_precision, "unknown")
            self.assertEqual(entry.source, "migration_legacy_refund")
            # The booking itself is untouched.
            booking = self.db.session.get(self.TripBooking, "B-LG-1")
            self.assertEqual(booking.refund_amount, 120.0)
            self.assertEqual(booking.payment_status, "Partial Refund")

    def test_the_migrated_refund_equals_the_legacy_total_exactly(self) -> None:
        """The property that lets revenue keep reading refund_amount safely."""
        self._seed_booking(payment_status="Partial Refund", refund_amount=337.75)
        self._run_backfill(apply=True)
        self.assertEqual(self._totals().total_refunded, 337.75)

    def test_the_backfill_writes_nothing_for_pending_or_deposit_paid(self) -> None:
        """Category D. Their payment amounts are genuinely unknown, and
        inventing one would defeat the point of the ledger."""
        self._seed_booking(booking_id="B-LG-PEND", payment_status="Pending")
        self._seed_booking(booking_id="B-LG-DEP", payment_status="Deposit Paid")
        _module, planned, _counts, _warnings, written = self._run_backfill(apply=True)
        self.assertEqual(planned, [])
        self.assertEqual(written, 0)
        with self.app.app_context():
            self.assertEqual(self.BookingTransaction.query.count(), 0)

    def test_inferred_fully_paid_payments_are_off_unless_asked_for(self) -> None:
        self._seed_booking(payment_status="Fully Paid")
        _module, planned, counts, _warnings, _written = self._run_backfill()
        self.assertEqual(planned, [])
        self.assertEqual(counts["C_available_not_written"], 1)

    def test_inferred_fully_paid_payments_are_clearly_labelled_when_enabled(self) -> None:
        self._seed_booking(payment_status="Fully Paid")
        self._run_backfill(infer_fully_paid=True, apply=True)
        with self.app.app_context():
            entry = self.BookingTransaction.query.filter_by(booking_id="B-LG-1").one()
            self.assertEqual(entry.amount, 1000.0)
            self.assertTrue(entry.is_inferred)
            self.assertEqual(entry.source, "migration_status_inferred")
            self.assertIn("No payment amount was ever recorded", entry.notes)

    def test_real_workbook_deposits_are_not_marked_inferred(self) -> None:
        """The amount is real; only a missing date is estimated."""
        self._seed_booking()
        deposits = {
            "B-LG-1": [
                {"ordinal": 1, "amount": 400.0, "currency": "USD",
                 "occurred_on": date(2026, 2, 10), "method": "transfer"},
                {"ordinal": 2, "amount": 300.0, "currency": "USD",
                 "occurred_on": None, "method": "cash"},
            ]
        }
        self._run_backfill(deposits=deposits, apply=True)
        with self.app.app_context():
            entries = self.BookingTransaction.query.filter_by(booking_id="B-LG-1").order_by(
                self.BookingTransaction.transaction_id.asc()
            ).all()
        self.assertEqual([e.amount for e in entries], [400.0, 300.0])
        self.assertFalse(any(e.is_inferred for e in entries))
        self.assertEqual(entries[0].date_precision, "exact")
        self.assertEqual(entries[0].method, "transfer")
        self.assertEqual(entries[1].date_precision, "unknown")

    def test_a_deposit_in_a_different_currency_is_skipped_not_guessed(self) -> None:
        self._seed_booking(currency="USD")
        deposits = {
            "B-LG-1": [
                {"ordinal": 1, "amount": 5000.0, "currency": "EGP",
                 "occurred_on": date(2026, 2, 10), "method": "cash"},
            ]
        }
        _module, planned, counts, warnings, _written = self._run_backfill(deposits=deposits)
        self.assertEqual(planned, [])
        self.assertEqual(counts["skipped_currency_mismatch"], 1)
        self.assertTrue(any("skipped rather than guessed" in w for w in warnings))

    # === reading the legacy workbook =====================================

    def _write_workbook(self, deposit_rows: list[tuple]) -> Path:
        """A miniature of the real workbook: row 1 groups the two deposit
        blocks, row 2 carries the real headers, data starts at row 3."""
        import openpyxl

        book = openpyxl.Workbook()
        sheet = book.active
        sheet.title = "Trip Bookings"
        sheet.append(["", "", "", "", "", "", "First Deposit", "", "", "", "Second Deposit", "", "", ""])
        sheet.append([
            "Booking ID", "Trip ID", "Trip Name", "Traveler ID", "Traveler Name", "Room Type",
            "Currency", "Amount", "Date", "Payment Method",
            "Currency", "Amount", "Date", "Payment Method",
        ])
        for row in deposit_rows:
            sheet.append(list(row))
        path = self.tmpdir / "legacy.xlsx"
        book.save(path)
        book.close()
        return path

    def _read_workbook(self, deposit_rows: list[tuple]):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        import importlib

        module = importlib.import_module("backfill_booking_ledger")
        importlib.reload(module)
        return module.read_workbook_deposits(self._write_workbook(deposit_rows))

    def test_an_unreadable_amount_is_reported_instead_of_vanishing(self) -> None:
        """The real workbook has a Second Deposit amount of "300 cash to
        menna". It is evidence that money moved, written by someone with no
        number column to put it in. Guessing 300 is forbidden -- but dropping
        it as silently as an empty cell is how a real payment disappears.
        """
        deposits, notes = self._read_workbook([
            ("B-LG-1", "", "", "", "", "", "USD", 500, None, "cash",
             "USD", "300 cash to menna", None, "cash"),
        ])
        # The readable half still imports; only the unreadable half is held back.
        self.assertEqual([d["amount"] for d in deposits["B-LG-1"]], [500.0])
        self.assertEqual(len(notes), 1)
        self.assertIn("300 cash to menna", notes[0])
        self.assertIn("B-LG-1", notes[0])
        self.assertIn("record it by hand", notes[0])

    def test_an_empty_amount_cell_is_not_reported_as_a_problem(self) -> None:
        """Absence of a deposit is the normal case for 819 of 850 bookings.
        Reporting each one would bury the handful that matter."""
        deposits, notes = self._read_workbook([
            ("B-LG-1", "", "", "", "", "", "", None, None, "", "", None, None, ""),
        ])
        self.assertEqual(deposits, {})
        self.assertEqual(notes, [])

    def test_a_written_zero_is_counted_but_never_named_individually(self) -> None:
        """Five of the six unimportable cells in the real workbook are a plain
        0 in Second Deposit -- "there wasn't one". Listing each by booking id
        would bury the single line that does need a human."""
        deposits, notes = self._read_workbook([
            ("B-LG-1", "", "", "", "", "", "USD", 0, None, "cash", "USD", 0, None, ""),
            ("B-LG-2", "", "", "", "", "", "USD", 0, None, "cash", "USD", None, None, ""),
        ])
        self.assertEqual(deposits, {})
        self.assertEqual(len(notes), 1)
        self.assertIn("3 deposit amount(s) are recorded as zero", notes[0])
        self.assertIn("absence of a payment", notes[0])
        self.assertNotIn("B-LG-1", notes[0])

    def test_a_zero_never_becomes_a_transaction(self) -> None:
        """The rule the whole legacy policy rests on, checked at the reader."""
        deposits, _notes = self._read_workbook([
            ("B-LG-1", "", "", "", "", "", "USD", 0, None, "cash", "USD", -50, None, "cash"),
        ])
        self.assertEqual(deposits, {})

    def test_deposits_for_bookings_this_database_does_not_have_are_counted(self) -> None:
        """The workbook covers all 850 historical bookings; a given database
        may hold a subset. Without this count, "50 found / 8 written" reads as
        a failure rather than as the two datasets differing."""
        self._seed_booking()
        deposits = {
            "B-LG-1": [{"ordinal": 1, "amount": 400.0, "currency": "USD",
                        "occurred_on": date(2026, 2, 10), "method": "cash"}],
            "B-NOT-MIGRATED": [{"ordinal": 1, "amount": 900.0, "currency": "USD",
                                "occurred_on": date(2026, 2, 10), "method": "cash"},
                               {"ordinal": 2, "amount": 100.0, "currency": "USD",
                                "occurred_on": None, "method": "cash"}],
        }
        _module, planned, counts, _warnings, _written = self._run_backfill(deposits=deposits)
        self.assertEqual(len(planned), 1)
        self.assertEqual(counts["deposits_for_unknown_bookings"], 2)
        self.assertEqual(counts["unknown_bookings_with_deposits"], 1)

    def test_the_scripts_output_survives_a_windows_console(self) -> None:
        """A box-drawing character in the totals line raised UnicodeEncodeError
        on cp1252 *after* the counts printed and *before* the skipped-row
        warnings -- losing the output the script exists to produce."""
        path = Path(__file__).resolve().parent.parent / "scripts" / "backfill_booking_ledger.py"
        source = path.read_text(encoding="utf-8")
        offenders = [
            f"{number}: {line.strip()}"
            for number, line in enumerate(source.splitlines(), start=1)
            if "print(" in line and any(ord(char) > 127 for char in line)
        ]
        self.assertEqual(offenders, [], f"non-ASCII in printed output: {offenders}")
        source.encode("cp1252")  # the whole script, not just the lines checked above

    def test_the_backfill_is_idempotent(self) -> None:
        self._seed_booking(payment_status="Partial Refund", refund_amount=120.0)
        self._run_backfill(apply=True)
        _module, _planned, _counts, _warnings, second = self._run_backfill(apply=True)
        self.assertEqual(second, 0)
        with self.app.app_context():
            self.assertEqual(self.BookingTransaction.query.count(), 1)

    # === legacy data policy =============================================
    # Approved rule: treat legacy payment data as unknown unless there is
    # explicit, trustworthy evidence. Never manufacture an amount, and never
    # write a zero as a placeholder for one.

    def test_an_empty_ledger_means_no_evidence_not_a_zero_payment(self) -> None:
        """The distinction the whole legacy policy rests on. total_paid reads
        0.0 either way, so anything drawing a conclusion from it must check
        whether that zero is a fact first."""
        self._seed_booking()
        totals = self._totals()
        self.assertEqual(totals.total_paid, 0.0)
        self.assertFalse(totals.total_paid_is_known)

        self._add(amount=250.0)
        known = self._totals()
        self.assertEqual(known.total_paid, 250.0)
        self.assertTrue(known.total_paid_is_known)

    def test_a_zero_refund_amount_creates_no_transaction(self) -> None:
        """A stored 0.0 is the absence of a refund, not a refund of nothing."""
        self._seed_booking(booking_id="B-LG-ZERO", payment_status="Fully Paid", refund_amount=0.0)
        _module, planned, _counts, _warnings, written = self._run_backfill(apply=True)
        self.assertEqual(planned, [])
        self.assertEqual(written, 0)
        with self.app.app_context():
            self.assertEqual(self.BookingTransaction.query.count(), 0)

    def test_a_zero_or_negative_workbook_deposit_creates_no_transaction(self) -> None:
        self._seed_booking()
        deposits = {
            "B-LG-1": [
                {"ordinal": 1, "amount": 0.0, "currency": "USD",
                 "occurred_on": date(2026, 2, 10), "method": "cash"},
                {"ordinal": 2, "amount": -50.0, "currency": "USD",
                 "occurred_on": date(2026, 2, 11), "method": "cash"},
            ]
        }
        _module, planned, counts, _warnings, written = self._run_backfill(
            deposits=deposits, apply=True
        )
        self.assertEqual(planned, [])
        self.assertEqual(written, 0)
        # Skipped cleanly at the planning stage, not left for the database
        # CHECK to reject mid-run.
        self.assertEqual(counts["skipped_non_positive_deposit"], 2)

    def test_a_zero_amount_cannot_be_written_even_deliberately(self) -> None:
        """Belt and braces: the database refuses it regardless of caller."""
        from sqlalchemy.exc import IntegrityError

        self._seed_booking()
        with self.app.app_context():
            self.db.session.add(
                self.BookingTransaction(
                    booking_id="B-LG-1", entry_type="refund", amount=0.0,
                    currency="USD", occurred_on=date(2026, 3, 1),
                    source="migration_legacy_refund",
                )
            )
            with self.assertRaises(IntegrityError):
                self.db.session.commit()
            self.db.session.rollback()

    def test_the_backfill_modifies_no_booking_row_at_all(self) -> None:
        """Across every category at once, including the ones that do produce
        transactions. The legacy fields stay exactly as they were."""
        self._seed_booking(booking_id="B-MIX-PAID", payment_status="Fully Paid")
        self._seed_booking(booking_id="B-MIX-REFUND", payment_status="Partial Refund", refund_amount=120.0)
        self._seed_booking(booking_id="B-MIX-PENDING", payment_status="Pending")
        self._seed_booking(booking_id="B-MIX-DEPOSIT", payment_status="Deposit Paid")

        def snapshot():
            with self.app.app_context():
                return {
                    booking.booking_id: (
                        booking.payment_status, booking.refund_amount,
                        booking.booking_status, booking.currency, booking.group_size,
                    )
                    for booking in self.TripBooking.query.all()
                }

        before = snapshot()
        deposits = {
            "B-MIX-PAID": [
                {"ordinal": 1, "amount": 400.0, "currency": "USD",
                 "occurred_on": date(2026, 2, 10), "method": "transfer"},
            ]
        }
        self._run_backfill(deposits=deposits, apply=True)
        self.assertEqual(snapshot(), before)

    def test_only_bookings_with_evidence_get_a_ledger(self) -> None:
        """The intended shape of the migrated data: evidence in, silence out."""
        self._seed_booking(booking_id="B-EV-WORKBOOK", payment_status="Fully Paid")
        self._seed_booking(booking_id="B-EV-REFUND", payment_status="Partial Refund", refund_amount=75.0)
        self._seed_booking(booking_id="B-EV-PAID-NOEVIDENCE", payment_status="Fully Paid")
        self._seed_booking(booking_id="B-EV-PENDING", payment_status="Pending")
        self._seed_booking(booking_id="B-EV-DEPOSIT", payment_status="Deposit Paid")

        deposits = {
            "B-EV-WORKBOOK": [
                {"ordinal": 1, "amount": 650.0, "currency": "USD",
                 "occurred_on": date(2026, 1, 20), "method": "cash"},
            ]
        }
        self._run_backfill(deposits=deposits, apply=True)

        with self.app.app_context():
            with_ledger = {
                booking_id for (booking_id,) in
                self.db.session.query(self.BookingTransaction.booking_id).distinct().all()
            }
        self.assertEqual(with_ledger, {"B-EV-WORKBOOK", "B-EV-REFUND"})
        # A Fully Paid booking with no evidence is left alone entirely: no
        # transaction, and its status is untouched.
        with self.app.app_context():
            untouched = self.db.session.get(self.TripBooking, "B-EV-PAID-NOEVIDENCE")
            self.assertEqual(untouched.payment_status, "Fully Paid")
            self.assertFalse(self.ledger.has_ledger("B-EV-PAID-NOEVIDENCE"))

    def test_category_c_stays_off_even_alongside_real_evidence(self) -> None:
        """The flag gates category C specifically, not the whole backfill."""
        self._seed_booking(booking_id="B-C-PAID", payment_status="Fully Paid")
        self._seed_booking(booking_id="B-C-REFUND", payment_status="Partial Refund", refund_amount=60.0)
        _module, planned, counts, _warnings, _written = self._run_backfill(apply=True)

        self.assertEqual(counts["C_status_inferred_payments"], 0)
        self.assertGreaterEqual(counts["C_available_not_written"], 1)
        self.assertEqual({row["booking_id"] for row in planned}, {"B-C-REFUND"})

    def test_a_dry_run_writes_nothing(self) -> None:
        self._seed_booking(payment_status="Partial Refund", refund_amount=120.0)
        _module, planned, _counts, _warnings, written = self._run_backfill()
        self.assertEqual(len(planned), 1)
        self.assertEqual(written, 0)
        with self.app.app_context():
            self.assertEqual(self.BookingTransaction.query.count(), 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
