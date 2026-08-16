# app/services/revenue.py
"""Re-exports the canonical revenue-recognition rules from
services.crm.system_services.revenue_rules, which is where they actually
live now -- that module is backend-agnostic (no Flask/app import) and used
by both the Postgres and legacy-SQLite traveler-stats recalculation paths,
so the rules can't live under this Flask-specific package without those
paths importing back into it. This module exists purely so existing call
sites (`from app.services.revenue import ...`) keep working unchanged.
"""
from __future__ import annotations

from services.crm.system_services.revenue_rules import (
    REVENUE_BOOKING_STATUSES,
    REVENUE_CURRENCIES,
    REVENUE_PAYMENT_STATUSES,
    booking_recognized_at,
    booking_revenue,
    parse_money,
)

__all__ = [
    "REVENUE_BOOKING_STATUSES",
    "REVENUE_CURRENCIES",
    "REVENUE_PAYMENT_STATUSES",
    "booking_recognized_at",
    "booking_revenue",
    "parse_money",
]
