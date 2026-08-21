# CRM Models

SQLAlchemy models for travelers, leads, trips, bookings, interactions, handoffs, copy templates, users, and event trails.

Money records are append-only: `booking_transaction.py` and `private_trip_transaction.py` are never updated or deleted (a mistake is corrected by inserting a reversal), and `additional_fee.py` rows are voided rather than removed. The immutability guards live in `app/services/booking_ledger.py` and `app/services/private_trip_ledger.py`.

Schema changes should be paired with migrations under `database/migrations/`.
