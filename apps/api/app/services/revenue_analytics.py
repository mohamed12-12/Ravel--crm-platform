# app/services/revenue_analytics.py
"""Everything the Revenue Analytics dashboard shows, computed in one place.

Two things earn money in this CRM and they are recognized differently, which
is the whole reason this module exists rather than a page full of inline
aggregation:

**Trip bookings** are recognized on status. A booking in a revenue status with
a recognized payment status is worth its room price x party size, plus any
additional fees, less refunds -- and it is attributed to the date it first
entered a revenue status (revenue_rules.booking_recognized_at). That rule is
unchanged; fees are the only new term.

**Private trips** are recognized on receipt. A private request is priced by
hand and settled in instalments, so its revenue is the money actually recorded
in its ledger, attributed to the day each payment arrived, less refunds
attributed to the day they went back. The agreed price is never revenue; it is
what the outstanding balance is measured against. A request quoted at 45,000
with a 15,000 deposit contributes 15,000 -- reporting the quote would report
money the company does not have.

Rules that hold everywhere in here:

* **USD and EGP are never added together.** There is no exchange rate anywhere
  in this system, on purpose. Every figure is per currency, all the way down.
* **Nothing is inferred.** Every number traces to a booking's stored fields, a
  fee row or a ledger entry. Where a figure cannot be established it is
  reported as unknown rather than as zero.
* **What is excluded is said out loud.** Anything that looks like revenue but
  is not counted lands in `needs_attention`, and any list that is capped says
  so -- a silently truncated table reads as "this is everything".
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from app.extensions import db
from app.models.additional_fee import AdditionalFee
from app.models.booking import TripBooking
from app.models.booking_status_history import BookingStatusHistory
from app.models.booking_transaction import BookingTransaction
from app.models.private_trip_request import PrivateTripRequest
from app.models.private_trip_transaction import PrivateTripTransaction
from app.models.trip import Trip
from app.services.additional_fees import fees_for_bookings, fees_for_private_requests
from app.services.private_trip_ledger import standing_transactions_for, summarize
from services.crm.system_services.private_trips import PRIVATE_REQUEST_CLOSED_STAGES
from services.crm.system_services.revenue_rules import (
    RECOGNIZED_PAYMENT_STATUSES,
    REVENUE_BOOKING_STATUSES,
    booking_recognized_at,
    booking_revenue_breakdown,
    private_trip_money,
)

CURRENCIES = ("USD", "EGP")

# Enough rows to scan a year's money without turning the page into a data
# dump. Whatever is dropped is reported next to the table.
TRANSACTION_FEED_LIMIT = 300
TOP_LIST_LIMIT = 8

# Distinct enough to tell apart on the dark theme, and reused by both the
# donut and the mix bars so a category is the same colour in both.
CATEGORY_COLORS = (
    "#37d39a",
    "#4aa9ff",
    "#f2c14e",
    "#c579f0",
    "#ff8a5c",
    "#5ad1d1",
    "#e06c8b",
    "#9aa7b8",
)


def empty_bucket() -> dict:
    """One currency-separated money bucket.

    Net is the headline; gross, refunds and fees travel with it because one
    number cannot distinguish a quiet month from a month of heavy refunds, and
    cannot show how much of the total came from fees rather than trips.
    """
    bucket = {"count": 0}
    for currency in CURRENCIES:
        bucket[currency] = 0.0
        bucket[f"{currency}_gross"] = 0.0
        bucket[f"{currency}_refunds"] = 0.0
        bucket[f"{currency}_fees"] = 0.0
    return bucket


def add_money(
    bucket: dict,
    currency: str,
    *,
    gross: float = 0.0,
    refunds: float = 0.0,
    fees: float = 0.0,
    counted: bool = True,
) -> None:
    if currency not in CURRENCIES:
        return
    bucket[f"{currency}_gross"] += gross
    bucket[f"{currency}_refunds"] += refunds
    bucket[f"{currency}_fees"] += fees
    bucket[currency] += gross - refunds
    if counted:
        bucket["count"] += 1


def bucket_has_money(bucket: dict) -> bool:
    return any(
        abs(bucket.get(f"{currency}_gross", 0.0)) > 0.005
        or abs(bucket.get(f"{currency}_refunds", 0.0)) > 0.005
        for currency in CURRENCIES
    )


def _as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _percent(part: float, whole: float) -> float:
    if not whole:
        return 0.0
    return round(part / whole * 100, 1)


# ---------------------------------------------------------------------------
# Chart geometry -- computed here, not in the template
# ---------------------------------------------------------------------------

_PLOT_LEFT = 44.0
_PLOT_RIGHT = 704.0
_PLOT_TOP = 14.0
_PLOT_BOTTOM = 176.0


def _nice_ceiling(value: float) -> float:
    """A round number at or just above `value`, for a readable axis."""
    if value <= 0:
        return 1.0
    magnitude = 10 ** (len(str(int(value))) - 1)
    for step in (1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10):
        candidate = magnitude * step
        if candidate >= value:
            return float(candidate)
    return float(magnitude * 10)


def build_trend_chart(rows: list[dict], currency: str) -> dict:
    """An area+line chart of one currency's monthly net revenue.

    Plain SVG with every coordinate resolved here: the CRM ships no charting
    library and the production Content-Security-Policy would not load one.
    """
    values = [max(float(row.get(currency) or 0.0), 0.0) for row in rows]
    gross_values = [max(float(row.get(f"{currency}_gross") or 0.0), 0.0) for row in rows]
    top = _nice_ceiling(max(gross_values + values + [0.0]))
    span = max(len(rows) - 1, 1)
    width = _PLOT_RIGHT - _PLOT_LEFT
    height = _PLOT_BOTTOM - _PLOT_TOP

    def point(index: int, value: float) -> tuple[float, float]:
        x = _PLOT_LEFT + (width * index / span)
        y = _PLOT_BOTTOM - (height * (value / top if top else 0.0))
        return round(x, 2), round(y, 2)

    net_points = [point(index, value) for index, value in enumerate(values)]
    gross_points = [point(index, value) for index, value in enumerate(gross_values)]
    polyline = " ".join(f"{x},{y}" for x, y in net_points)
    gross_polyline = " ".join(f"{x},{y}" for x, y in gross_points)
    area = ""
    if net_points:
        area = (
            f"M{net_points[0][0]},{_PLOT_BOTTOM} "
            + " ".join(f"L{x},{y}" for x, y in net_points)
            + f" L{net_points[-1][0]},{_PLOT_BOTTOM} Z"
        )
    gridlines = [
        {
            "y": round(_PLOT_BOTTOM - height * fraction, 2),
            "value": round(top * fraction, 2),
        }
        for fraction in (0.0, 0.25, 0.5, 0.75, 1.0)
    ]
    return {
        "currency": currency,
        "max": top,
        "has_data": any(value > 0 for value in gross_values),
        "polyline": polyline,
        "gross_polyline": gross_polyline,
        "area": area,
        "gridlines": gridlines,
        "points": [
            {
                "x": x,
                "y": y,
                "label": rows[index].get("label", ""),
                "net": values[index],
                "gross": gross_values[index],
                "refunds": float(rows[index].get(f"{currency}_refunds") or 0.0),
                "count": rows[index].get("count", 0),
            }
            for index, (x, y) in enumerate(net_points)
        ],
        "labels": [
            {"x": round(_PLOT_LEFT + (width * index / span), 2), "label": row.get("label", "")}
            for index, row in enumerate(rows)
        ],
        "baseline": _PLOT_BOTTOM,
        "plot_left": _PLOT_LEFT,
        "plot_right": _PLOT_RIGHT,
    }


def build_donut(rows: list[tuple[str, dict]], currency: str) -> dict:
    """Share of net revenue by category, as stroke-dash segments on a circle.

    Dash offsets rather than arc paths: no trigonometry, so there is no way for
    a rounding slip to draw a segment that misrepresents its share.
    """
    radius = 54.0
    circumference = 2 * 3.141592653589793 * radius
    values = [(label, max(float(bucket.get(currency) or 0.0), 0.0)) for label, bucket in rows]
    total = sum(value for _label, value in values)
    segments = []
    offset = 0.0
    for index, (label, value) in enumerate(values):
        if value <= 0:
            continue
        share = value / total if total else 0.0
        length = circumference * share
        segments.append({
            "label": label,
            "value": value,
            "share": round(share * 100, 1),
            "color": CATEGORY_COLORS[index % len(CATEGORY_COLORS)],
            "dash": f"{length:.2f} {circumference - length:.2f}",
            "offset": round(-offset, 2),
        })
        offset += length
    return {
        "currency": currency,
        "radius": radius,
        "circumference": round(circumference, 2),
        "total": total,
        "segments": segments,
        "has_data": bool(segments),
    }


# ---------------------------------------------------------------------------
# The dashboard
# ---------------------------------------------------------------------------


def build_revenue_dashboard(*, selected_year: int | None = None, now: datetime | None = None) -> dict:
    moment = now or datetime.now(timezone.utc)
    current_year = moment.year
    year = selected_year or current_year

    # ---- Trip bookings -------------------------------------------------
    bookings = TripBooking.query.all()
    trip_ids = {b.trip_id for b in bookings if b.trip_id}
    trips = {t.trip_id: t for t in Trip.query.filter(Trip.trip_id.in_(trip_ids)).all()} if trip_ids else {}
    booking_fees = fees_for_bookings([b.booking_id for b in bookings if b.booking_id])

    history_by_booking: dict[str, list] = {}
    for entry in BookingStatusHistory.query.order_by(BookingStatusHistory.changed_at.asc()).all():
        history_by_booking.setdefault(entry.booking_id, []).append(entry)

    # ---- Private trips -------------------------------------------------
    private_requests = PrivateTripRequest.query.all()
    request_ids = [item.request_id for item in private_requests if item.request_id]
    private_entries = standing_transactions_for(request_ids)
    private_fees = fees_for_private_requests(request_ids)

    total = empty_bucket()
    this_month = empty_bucket()
    last_month = empty_bucket()
    yearly: dict[int, dict] = {}
    monthly: dict[int, dict] = {index: empty_bucket() for index in range(1, 13)}
    by_category: dict[str, dict] = {}
    by_source: dict[str, dict] = {}
    fees_bucket = empty_bucket()
    available_years: set[int] = {current_year}
    needs_attention: list[dict] = []
    top_earners: dict[str, dict] = {}
    method_mix: dict[str, dict] = {}

    previous_month_year, previous_month = (
        (current_year - 1, 12) if moment.month == 1 else (current_year, moment.month - 1)
    )

    def record(
        *,
        currency: str,
        gross: float,
        refunds: float,
        fees: float,
        when: date | None,
        category: str,
        source: str,
        label: str,
        counted: bool = True,
    ) -> None:
        if currency not in CURRENCIES or when is None:
            return
        available_years.add(when.year)
        add_money(total, currency, gross=gross, refunds=refunds, fees=fees, counted=counted)
        add_money(
            yearly.setdefault(when.year, empty_bucket()),
            currency, gross=gross, refunds=refunds, fees=fees, counted=counted,
        )
        add_money(
            by_category.setdefault(category, empty_bucket()),
            currency, gross=gross, refunds=refunds, fees=fees, counted=counted,
        )
        add_money(
            by_source.setdefault(source, empty_bucket()),
            currency, gross=gross, refunds=refunds, fees=fees, counted=counted,
        )
        add_money(
            top_earners.setdefault(label, empty_bucket()),
            currency, gross=gross, refunds=refunds, fees=fees, counted=counted,
        )
        if fees:
            add_money(fees_bucket, currency, gross=fees, counted=counted)
        if when.year == current_year and when.month == moment.month:
            add_money(this_month, currency, gross=gross, refunds=refunds, fees=fees, counted=counted)
        if when.year == previous_month_year and when.month == previous_month:
            add_money(last_month, currency, gross=gross, refunds=refunds, fees=fees, counted=counted)
        if when.year == year:
            add_money(
                monthly[when.month], currency, gross=gross, refunds=refunds, fees=fees, counted=counted
            )

    for booking in bookings:
        trip = trips.get(booking.trip_id)
        fees = booking_fees.get(booking.booking_id, [])
        breakdown = booking_revenue_breakdown(booking, trip, fees)
        if breakdown is None:
            # Status and payment do not (yet) call for revenue -- a Draft or
            # Cancelled booking has nothing to report. But a booking whose
            # status and payment already say it SHOULD be revenue and is still
            # excluded is the "Completed, Fully Paid, invisible" gap.
            booking_status = str(booking.booking_status or "").strip().lower()
            payment_status = str(booking.payment_status or "").strip().lower()
            if booking_status in REVENUE_BOOKING_STATUSES and payment_status in RECOGNIZED_PAYMENT_STATUSES:
                missing = booking.compute_missing_fields()
                if missing:
                    needs_attention.append({
                        "kind": "booking",
                        "record_id": booking.booking_id,
                        "party": booking.traveler_name or "Unknown traveler",
                        "reason": "Missing " + ", ".join(missing) + ".",
                        "url_endpoint": "bookings.detail",
                        "url_kwargs": {"booking_id": booking.booking_id},
                        "action": "Fix Booking",
                    })
            continue

        currency = breakdown.currency
        # Judged on GROSS, deliberately: a fully refunded booking nets zero
        # while being perfectly well configured.
        if breakdown.gross <= 0:
            needs_attention.append({
                "kind": "booking",
                "record_id": booking.booking_id,
                "party": booking.traveler_name or "Unknown traveler",
                "reason": "No price configured for this room/currency combination.",
                "url_endpoint": "bookings.detail",
                "url_kwargs": {"booking_id": booking.booking_id},
                "action": "Fix Booking",
            })
        elif breakdown.refund_exceeds_gross:
            needs_attention.append({
                "kind": "booking",
                "record_id": booking.booking_id,
                "party": booking.traveler_name or "Unknown traveler",
                "reason": (
                    f"Recorded refund ({breakdown.refunds_recorded:,.2f} {currency}) is larger than "
                    f"the booking value ({breakdown.gross:,.2f} {currency})."
                ),
                "url_endpoint": "bookings.detail",
                "url_kwargs": {"booking_id": booking.booking_id},
                "action": "Review Refund",
            })

        # A fee in a currency the booking does not use cannot be added to its
        # total without an exchange rate, so it is excluded and flagged.
        stray = [
            fee for fee in fees
            if fee.is_active and str(fee.currency or "").strip().upper() != currency
        ]
        if stray:
            needs_attention.append({
                "kind": "booking",
                "record_id": booking.booking_id,
                "party": booking.traveler_name or "Unknown traveler",
                "reason": (
                    f"{len(stray)} additional fee(s) are not in {currency} and are excluded "
                    "from revenue -- the CRM never converts currencies."
                ),
                "url_endpoint": "bookings.detail",
                "url_kwargs": {"booking_id": booking.booking_id},
                "action": "Fix Fees",
            })

        recognized_at = _as_date(booking_recognized_at(booking, history_by_booking))
        trip_type = str(trip.type).strip().title() if trip and trip.type else "Unspecified"
        record(
            currency=currency,
            gross=breakdown.gross,
            refunds=breakdown.refunds,
            fees=breakdown.fees,
            when=recognized_at,
            category=trip_type,
            source="Trip bookings",
            label=booking.trip_name or booking.trip_id or "Unlinked trip",
        )

    request_by_id = {item.request_id: item for item in private_requests}
    for request_id, entries in private_entries.items():
        item = request_by_id.get(request_id)
        if item is None:
            continue
        # A request converted under the retired conversion step already has its
        # money in the booking it produced. Counting its ledger too would be
        # the double-count the retirement exists to prevent.
        if item.converted_booking_id:
            continue
        fees = private_fees.get(request_id, [])
        totals = summarize(entries)
        money = private_trip_money(
            price=item.agreed_price_amount,
            price_currency=item.agreed_price_currency,
            fees=fees,
            total_paid=totals.total_paid,
            total_refunded=totals.total_refunded,
            entry_count=totals.entry_count,
            ledger_currency=totals.currency,
            fallback_currency=item.budget_currency,
        )
        category = f"Private — {item.trip_scope or 'Unspecified'}"
        label = f"Private: {item.destination or item.request_id}"
        party = (item.traveler.full_name if item.traveler else None) or item.traveler_id or item.request_id

        if totals.has_currency_conflict:
            needs_attention.append({
                "kind": "private",
                "record_id": request_id,
                "party": party,
                "reason": (
                    "Payments on this request are recorded in more than one currency "
                    f"({', '.join(totals.currencies_seen)}), so its revenue cannot be totalled."
                ),
                "url_endpoint": "private_requests.detail",
                "url_kwargs": {"request_id": request_id},
                "action": "Fix Payments",
            })
            continue
        if money.currency_conflict:
            needs_attention.append({
                "kind": "private",
                "record_id": request_id,
                "party": party,
                "reason": (
                    f"Priced in {item.agreed_price_currency} but paid in {totals.currency}. "
                    "The balance cannot be calculated across currencies."
                ),
                "url_endpoint": "private_requests.detail",
                "url_kwargs": {"request_id": request_id},
                "action": "Fix Currency",
            })
        if totals.total_paid > 0 and money.price is None:
            needs_attention.append({
                "kind": "private",
                "record_id": request_id,
                "party": party,
                "reason": (
                    f"{totals.total_paid:,.2f} {totals.currency} received with no agreed price recorded, "
                    "so the outstanding balance is unknown."
                ),
                "url_endpoint": "private_requests.detail",
                "url_kwargs": {"request_id": request_id},
                "action": "Set Price",
            })
        elif money.contract_value is not None and totals.total_paid > money.contract_value + 0.005:
            needs_attention.append({
                "kind": "private",
                "record_id": request_id,
                "party": party,
                "reason": (
                    f"Overpaid: {totals.total_paid:,.2f} {money.currency} received against a "
                    f"{money.contract_value:,.2f} {money.currency} total."
                ),
                "url_endpoint": "private_requests.detail",
                "url_kwargs": {"request_id": request_id},
                "action": "Review",
            })

        # One row per ledger entry: private revenue is attributed to the day
        # the money moved, which is more precise than a booking's status date.
        for entry in entries:
            currency = str(entry.currency or "").strip().upper()
            if currency not in CURRENCIES:
                continue
            occurred = _as_date(entry.occurred_on)
            if entry.is_payment:
                record(
                    currency=currency,
                    gross=float(entry.amount or 0.0),
                    refunds=0.0,
                    fees=0.0,
                    when=occurred,
                    category=category,
                    source="Private trips",
                    label=label,
                )
                add_money(
                    method_mix.setdefault(entry.method or "other", empty_bucket()),
                    currency,
                    gross=float(entry.amount or 0.0),
                )
            else:
                record(
                    currency=currency,
                    gross=0.0,
                    refunds=float(entry.amount or 0.0),
                    fees=0.0,
                    when=occurred,
                    category=category,
                    source="Private trips",
                    label=label,
                    counted=False,
                )

    # ---- Every payment and refund, both ledgers -------------------------
    transactions, transaction_total = _build_transaction_feed(year)

    # ---- Private pipeline: what is agreed, collected and still owed -----
    pipeline = _build_private_pipeline(private_requests, private_entries, private_fees)

    # ---- Rows -----------------------------------------------------------
    years_sorted = sorted(available_years, reverse=True)
    yearly_rows = [{"year": value, **yearly.get(value, empty_bucket())} for value in years_sorted]
    monthly_rows = [
        {
            "month": index,
            "label": datetime(2000, index, 1).strftime("%b"),
            **monthly[index],
        }
        for index in range(1, 13)
    ]
    max_monthly = {
        currency: max((row[currency] for row in monthly_rows), default=0.0) or 1.0
        for currency in CURRENCIES
    }
    for row in monthly_rows:
        for currency in CURRENCIES:
            row[f"{currency.lower()}_bar_pct"] = round(
                min(row[currency] / max_monthly[currency], 1.0) * 100, 1
            )

    category_rows = sorted(
        by_category.items(), key=lambda pair: -(pair[1]["USD"] + pair[1]["EGP"])
    )
    source_rows = sorted(by_source.items(), key=lambda pair: -(pair[1]["USD"] + pair[1]["EGP"]))
    top_rows = [
        (label, bucket)
        for label, bucket in sorted(
            top_earners.items(), key=lambda pair: -(pair[1]["USD"] + pair[1]["EGP"])
        )
        if bucket_has_money(bucket)
    ][:TOP_LIST_LIMIT]
    method_rows = sorted(method_mix.items(), key=lambda pair: -(pair[1]["USD"] + pair[1]["EGP"]))

    return {
        "total": total,
        "this_year": yearly.get(current_year, empty_bucket()),
        "this_month": this_month,
        "last_month": last_month,
        "selected_year_totals": yearly.get(year, empty_bucket()),
        "yearly_rows": yearly_rows,
        "monthly_rows": monthly_rows,
        "category_rows": category_rows,
        "source_rows": source_rows,
        "top_rows": top_rows,
        "method_rows": method_rows,
        "fees_bucket": fees_bucket,
        "pipeline": pipeline,
        "transactions": transactions,
        "transaction_total": transaction_total,
        "transaction_limit": TRANSACTION_FEED_LIMIT,
        "needs_attention": needs_attention,
        "insights": _build_insights(
            total=total,
            this_month=this_month,
            last_month=last_month,
            selected_year_totals=yearly.get(year, empty_bucket()),
            yearly=yearly,
            year=year,
            fees_bucket=fees_bucket,
            pipeline=pipeline,
        ),
        "charts": {
            "trend": {currency: build_trend_chart(monthly_rows, currency) for currency in CURRENCIES},
            "category": {currency: build_donut(category_rows, currency) for currency in CURRENCIES},
        },
        "currencies": CURRENCIES,
        "available_years": years_sorted,
        "selected_year": year,
        "current_year": current_year,
        "current_month_label": moment.strftime("%B %Y"),
    }


def _build_transaction_feed(year: int) -> tuple[list[dict], int]:
    """Every recorded payment and refund in `year`, both ledgers, newest first.

    Reversals are included and labelled rather than hidden: a corrected entry
    is part of the money's story, and someone reconciling a month needs to see
    that the correction happened.
    """
    rows: list[dict] = []

    booking_entries = (
        BookingTransaction.query.filter(
            db.extract("year", BookingTransaction.occurred_on) == year
        ).all()
    )
    booking_ids = {entry.booking_id for entry in booking_entries if entry.booking_id}
    booking_names = {}
    if booking_ids:
        for booking in TripBooking.query.filter(TripBooking.booking_id.in_(booking_ids)).all():
            booking_names[booking.booking_id] = booking.traveler_name or booking.traveler_id or ""
    reversed_booking_ids = {entry.reverses_id for entry in booking_entries if entry.reverses_id}
    for entry in booking_entries:
        rows.append({
            "occurred_on": entry.occurred_on,
            "kind": "Payment" if entry.is_payment else "Refund",
            "is_payment": bool(entry.is_payment),
            "reference": entry.public_ref or f"#{entry.transaction_id}",
            "amount": float(entry.amount or 0.0),
            "currency": str(entry.currency or "").strip().upper(),
            "source": "Booking",
            "record_id": entry.booking_id,
            "party": booking_names.get(entry.booking_id, ""),
            "method": entry.method or "",
            "url_endpoint": "bookings.detail",
            "url_kwargs": {"booking_id": entry.booking_id},
            "is_reversal": entry.reverses_id is not None,
            "is_reversed": entry.transaction_id in reversed_booking_ids,
            "note": entry.reason or entry.notes or "",
        })

    private_entries = (
        PrivateTripTransaction.query.filter(
            db.extract("year", PrivateTripTransaction.occurred_on) == year
        ).all()
    )
    request_ids = {entry.request_id for entry in private_entries if entry.request_id}
    request_parties = {}
    if request_ids:
        for item in PrivateTripRequest.query.filter(PrivateTripRequest.request_id.in_(request_ids)).all():
            request_parties[item.request_id] = (
                (item.traveler.full_name if item.traveler else None) or item.traveler_id or ""
            )
    reversed_private_ids = {entry.reverses_id for entry in private_entries if entry.reverses_id}
    for entry in private_entries:
        rows.append({
            "occurred_on": entry.occurred_on,
            "kind": "Payment" if entry.is_payment else "Refund",
            "is_payment": bool(entry.is_payment),
            "reference": entry.public_ref or f"#{entry.transaction_id}",
            "amount": float(entry.amount or 0.0),
            "currency": str(entry.currency or "").strip().upper(),
            "source": "Private trip",
            "record_id": entry.request_id,
            "party": request_parties.get(entry.request_id, ""),
            "method": entry.method or "",
            "url_endpoint": "private_requests.detail",
            "url_kwargs": {"request_id": entry.request_id},
            "is_reversal": entry.reverses_id is not None,
            "is_reversed": entry.transaction_id in reversed_private_ids,
            "note": entry.reason or entry.notes or "",
        })

    rows.sort(key=lambda row: (row["occurred_on"] or date.min, row["reference"]), reverse=True)
    return rows[:TRANSACTION_FEED_LIMIT], len(rows)


def _build_private_pipeline(private_requests, private_entries, private_fees) -> dict:
    """Agreed, collected and outstanding money across private trips.

    Separate from revenue on purpose: an agreed price is a commitment, not
    income, and this is the one place the two are allowed to sit side by side
    -- clearly labelled -- so a manager can see what is still to collect
    without any of it leaking into a revenue figure.
    """
    agreed = {currency: 0.0 for currency in CURRENCIES}
    collected = {currency: 0.0 for currency in CURRENCIES}
    refunded = {currency: 0.0 for currency in CURRENCIES}
    outstanding = {currency: 0.0 for currency in CURRENCIES}
    unpriced_with_money = 0
    open_requests = 0
    balances: list[dict] = []

    for item in private_requests:
        if item.converted_booking_id:
            continue
        entries = private_entries.get(item.request_id, [])
        totals = summarize(entries)
        fees = private_fees.get(item.request_id, [])
        money = private_trip_money(
            price=item.agreed_price_amount,
            price_currency=item.agreed_price_currency,
            fees=fees,
            total_paid=totals.total_paid,
            total_refunded=totals.total_refunded,
            entry_count=totals.entry_count,
            ledger_currency=totals.currency,
            fallback_currency=item.budget_currency,
        )
        currency = money.currency
        if currency not in CURRENCIES:
            continue
        if item.stage not in PRIVATE_REQUEST_CLOSED_STAGES:
            open_requests += 1
        collected[currency] += money.paid
        refunded[currency] += money.refunded
        if money.contract_value is None:
            if money.paid > 0:
                unpriced_with_money += 1
            continue
        agreed[currency] += money.contract_value
        due = money.outstanding or 0.0
        outstanding[currency] += due
        if due > 0.005 and item.stage != "lost":
            balances.append({
                "record_id": item.request_id,
                "party": (item.traveler.full_name if item.traveler else None) or item.traveler_id or "—",
                "destination": item.destination or "—",
                "stage": item.stage,
                "currency": currency,
                "contract_value": money.contract_value,
                "paid": money.paid,
                "outstanding": due,
                "payment_status": money.payment_status,
            })

    balances.sort(key=lambda row: -row["outstanding"])
    return {
        "agreed": agreed,
        "collected": collected,
        "refunded": refunded,
        "outstanding": outstanding,
        "collection_rate": {
            currency: _percent(collected[currency], agreed[currency]) for currency in CURRENCIES
        },
        "unpriced_with_money": unpriced_with_money,
        "open_requests": open_requests,
        "balances": balances[:TOP_LIST_LIMIT],
        "balance_total": len(balances),
    }


def _build_insights(
    *,
    total: dict,
    this_month: dict,
    last_month: dict,
    selected_year_totals: dict,
    yearly: dict,
    year: int,
    fees_bucket: dict,
    pipeline: dict,
) -> dict:
    """The read-outs a manager actually asks for, per currency.

    Every one is a ratio of two figures already on the page, so anything here
    can be checked against the tables above it rather than taken on trust.
    """
    previous_year = yearly.get(year - 1, empty_bucket())
    insights = {}
    for currency in CURRENCIES:
        gross = total[f"{currency}_gross"]
        refunds = total[f"{currency}_refunds"]
        month_now = this_month[currency]
        month_before = last_month[currency]
        year_now = selected_year_totals[currency]
        year_before = previous_year[currency]
        insights[currency] = {
            "gross": gross,
            "refunds": refunds,
            "net": total[currency],
            "fees": fees_bucket[f"{currency}_gross"],
            "refund_rate": _percent(refunds, gross),
            "fee_share": _percent(fees_bucket[f"{currency}_gross"], gross),
            "month_change": _percent(month_now - month_before, month_before) if month_before else None,
            "month_direction": "up" if month_now >= month_before else "down",
            "year_change": _percent(year_now - year_before, year_before) if year_before else None,
            "year_direction": "up" if year_now >= year_before else "down",
            "average_value": (total[currency] / total["count"]) if total["count"] else 0.0,
            "outstanding": pipeline["outstanding"][currency],
            "collection_rate": pipeline["collection_rate"][currency],
        }
    return insights
