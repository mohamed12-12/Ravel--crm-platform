"""Validate a staged Rahma SQLite to PostgreSQL migration.

The validator compares counts and keys without printing customer data or IDs.
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SQLITE_PATH = ROOT / "apps" / "api" / "instance" / "rahma_traveler_dev.db"

TABLES = [
    "users",
    "travelers",
    "trips",
    "community_events",
    "dm_copy_library",
    "language_templates",
    "leads",
    "interactions",
    "trip_bookings",
    "ce_bookings",
    "booking_status_history",
    "handoff_queue",
    "booking_event_trail",
    "traveler_documents",
    "trip_media",
    "assignment_history",
    "user_audit_log",
    "sync_queue",
]

ARCHIVE_TABLE = "legacy_booking_status_history_orphans"

PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "users": ("id",),
    "travelers": ("traveler_id",),
    "trips": ("trip_id",),
    "community_events": ("event_id",),
    "dm_copy_library": ("message_key",),
    "language_templates": ("id",),
    "leads": ("lead_id",),
    "interactions": ("interaction_id",),
    "trip_bookings": ("booking_id",),
    "ce_bookings": ("booking_id",),
    "booking_status_history": ("history_id",),
    "handoff_queue": ("handoff_id",),
    "booking_event_trail": ("event_id",),
    "traveler_documents": ("document_id",),
    "trip_media": ("media_id",),
    "assignment_history": ("id",),
    "user_audit_log": ("id",),
    "sync_queue": ("mapping_name", "record_id"),
}

REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "users": ("username", "password_hash", "role"),
    "travelers": ("traveler_id", "full_name"),
    "trips": ("trip_id", "trip_name"),
    "community_events": ("event_id", "event_name"),
    "dm_copy_library": ("message_key",),
    "leads": ("lead_id",),
    "interactions": ("interaction_id",),
    "trip_bookings": ("booking_id",),
    "ce_bookings": ("booking_id",),
    "booking_status_history": ("history_id", "booking_id"),
    "handoff_queue": ("handoff_id",),
    "booking_event_trail": ("event_id",),
    "traveler_documents": ("document_id", "traveler_id", "category", "file_name", "storage_path"),
    "trip_media": (
        "media_id",
        "public_id",
        "trip_id",
        "storage_key",
        "public_url",
        "image_type",
        "mime_type",
        "file_extension",
    ),
    "assignment_history": ("id", "resource_type", "resource_id"),
    "user_audit_log": ("id", "action"),
    "sync_queue": ("mapping_name", "record_id", "status"),
}

FOREIGN_KEY_CHECKS = {
    "leads.traveler_id": """
        SELECT COUNT(*) FROM leads l
        LEFT JOIN travelers t ON t.traveler_id = l.traveler_id
        WHERE l.traveler_id IS NOT NULL AND t.traveler_id IS NULL
    """,
    "trip_bookings.trip_id": """
        SELECT COUNT(*) FROM trip_bookings b
        LEFT JOIN trips t ON t.trip_id = b.trip_id
        WHERE b.trip_id IS NOT NULL AND t.trip_id IS NULL
    """,
    "trip_bookings.traveler_id": """
        SELECT COUNT(*) FROM trip_bookings b
        LEFT JOIN travelers t ON t.traveler_id = b.traveler_id
        WHERE b.traveler_id IS NOT NULL AND t.traveler_id IS NULL
    """,
    "trip_bookings.lead_id": """
        SELECT COUNT(*) FROM trip_bookings b
        LEFT JOIN leads l ON l.lead_id = b.lead_id
        WHERE b.lead_id IS NOT NULL AND l.lead_id IS NULL
    """,
    "handoff_queue.lead_id": """
        SELECT COUNT(*) FROM handoff_queue h
        LEFT JOIN leads l ON l.lead_id = h.lead_id
        WHERE h.lead_id IS NOT NULL AND l.lead_id IS NULL
    """,
    "handoff_queue.traveler_id": """
        SELECT COUNT(*) FROM handoff_queue h
        LEFT JOIN travelers t ON t.traveler_id = h.traveler_id
        WHERE h.traveler_id IS NOT NULL AND t.traveler_id IS NULL
    """,
    "traveler_documents.traveler_id": """
        SELECT COUNT(*) FROM traveler_documents d
        LEFT JOIN travelers t ON t.traveler_id = d.traveler_id
        WHERE d.traveler_id IS NOT NULL AND t.traveler_id IS NULL
    """,
    "trip_media.trip_id": """
        SELECT COUNT(*) FROM trip_media m
        LEFT JOIN trips t ON t.trip_id = m.trip_id
        WHERE m.trip_id IS NOT NULL AND t.trip_id IS NULL
    """,
}

DUPLICATE_CHECKS = {
    "trip_bookings.idempotency_key": """
        SELECT COUNT(*) FROM (
            SELECT idempotency_key
            FROM trip_bookings
            WHERE idempotency_key IS NOT NULL AND idempotency_key <> ''
            GROUP BY idempotency_key
            HAVING COUNT(*) > 1
        ) d
    """,
    "leads.idempotency_key": """
        SELECT COUNT(*) FROM (
            SELECT idempotency_key
            FROM leads
            WHERE idempotency_key IS NOT NULL AND idempotency_key <> ''
            GROUP BY idempotency_key
            HAVING COUNT(*) > 1
        ) d
    """,
    "handoff_queue.idempotency_key": """
        SELECT COUNT(*) FROM (
            SELECT idempotency_key
            FROM handoff_queue
            WHERE idempotency_key IS NOT NULL AND idempotency_key <> ''
            GROUP BY idempotency_key
            HAVING COUNT(*) > 1
        ) d
    """,
    "active_trip_booking_pair": """
        SELECT COUNT(*) FROM (
            SELECT traveler_id, trip_id
            FROM trip_bookings
            WHERE traveler_id IS NOT NULL
              AND trip_id IS NOT NULL
              AND COALESCE(booking_status, '') NOT IN ('Cancelled', 'Canceled', 'Lost')
            GROUP BY traveler_id, trip_id
            HAVING COUNT(*) > 1
        ) d
    """,
    "open_handoff_pair": """
        SELECT COUNT(*) FROM (
            SELECT COALESCE(lead_id, ''), COALESCE(traveler_id, ''), COALESCE(trip_id, '')
            FROM handoff_queue
            WHERE COALESCE(status, '') NOT IN ('Closed', 'Resolved', 'Cancelled', 'Canceled')
            GROUP BY COALESCE(lead_id, ''), COALESCE(traveler_id, ''), COALESCE(trip_id, '')
            HAVING COUNT(*) > 1
        ) d
    """,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a staged PostgreSQL migration.")
    parser.add_argument("--sqlite-path", default=str(DEFAULT_SQLITE_PATH))
    parser.add_argument(
        "--postgres-url",
        default=os.environ.get("POSTGRES_URL") or os.environ.get("DATABASE_URL"),
        help="Target PostgreSQL URL.",
    )
    parser.add_argument("--tables", help="Comma-separated table subset.")
    return parser.parse_args()


def quote_ident(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return f'"{name}"'


def mask_url(url: str | None) -> str:
    if not url:
        return "<not supplied>"
    parts = urlsplit(url)
    if not parts.password:
        return urlunsplit(parts)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    username = parts.username or ""
    netloc = f"{username}:***@{host}" if username else host
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def select_tables(raw_tables: str | None) -> list[str]:
    if not raw_tables:
        return list(TABLES)
    requested = [item.strip() for item in raw_tables.split(",") if item.strip()]
    unknown = sorted(set(requested) - set(TABLES))
    if unknown:
        raise SystemExit(f"Unknown table(s): {', '.join(unknown)}")
    requested_set = set(requested)
    return [table for table in TABLES if table in requested_set]


def sqlite_connect_readonly(path: str) -> sqlite3.Connection:
    resolved = Path(path).resolve()
    if not resolved.exists():
        raise SystemExit(f"SQLite database not found: {resolved}")
    connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def load_postgres_driver() -> tuple[str, Any]:
    try:
        import psycopg  # type: ignore

        return "psycopg3", psycopg
    except ImportError:
        try:
            import psycopg2  # type: ignore

            return "psycopg2", psycopg2
        except ImportError as exc:
            raise SystemExit(
                "Install a PostgreSQL driver first: python -m pip install psycopg[binary]"
            ) from exc


def sqlite_count(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) AS count FROM {quote_ident(table)}").fetchone()
    return int(row["count"])


def sqlite_booking_history_counts(conn: sqlite3.Connection) -> tuple[int, int, int]:
    valid = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM booking_status_history h
        INNER JOIN trip_bookings b ON b.booking_id = h.booking_id
        """
    ).fetchone()["count"]
    orphan = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM booking_status_history h
        LEFT JOIN trip_bookings b ON b.booking_id = h.booking_id
        WHERE h.booking_id IS NOT NULL AND b.booking_id IS NULL
        """
    ).fetchone()["count"]
    total = conn.execute("SELECT COUNT(*) AS count FROM booking_status_history").fetchone()["count"]
    return int(valid), int(orphan), int(total)


def pg_count(cursor: Any, table: str) -> int:
    cursor.execute(f"SELECT COUNT(*) FROM {quote_ident(table)}")
    return int(cursor.fetchone()[0])


def table_exists(cursor: Any, table: str) -> bool:
    cursor.execute("SELECT to_regclass(%s)", (table,))
    return cursor.fetchone()[0] is not None


def sqlite_keys(conn: sqlite3.Connection, table: str) -> set[tuple[Any, ...]]:
    columns = PRIMARY_KEYS[table]
    sql = ", ".join(quote_ident(column) for column in columns)
    return {tuple(row[column] for column in columns) for row in conn.execute(f"SELECT {sql} FROM {quote_ident(table)}")}


def pg_keys(cursor: Any, table: str) -> set[tuple[Any, ...]]:
    columns = PRIMARY_KEYS[table]
    sql = ", ".join(quote_ident(column) for column in columns)
    cursor.execute(f"SELECT {sql} FROM {quote_ident(table)}")
    return {tuple(row) for row in cursor.fetchall()}


def null_check_sql(table: str, column: str) -> str:
    column_sql = quote_ident(column)
    return (
        f"SELECT COUNT(*) FROM {quote_ident(table)} "
        f"WHERE {column_sql} IS NULL OR ({column_sql}::text = '')"
    )


def main() -> int:
    args = parse_args()
    tables = select_tables(args.tables)
    if not args.postgres_url or args.postgres_url.startswith("sqlite"):
        raise SystemExit("--postgres-url must point to the staged PostgreSQL database")

    sqlite_conn = sqlite_connect_readonly(args.sqlite_path)
    driver_name, driver = load_postgres_driver()
    print(f"Source SQLite: {Path(args.sqlite_path).resolve()}")
    print(f"Target PostgreSQL: {mask_url(args.postgres_url)}")
    print(f"Driver: {driver_name}")

    failures = 0
    pg_conn = driver.connect(args.postgres_url)
    try:
        with pg_conn.cursor() as cursor:
            for table in tables:
                source_rows = sqlite_count(sqlite_conn, table)
                if table == "booking_status_history":
                    source_rows = sqlite_booking_history_counts(sqlite_conn)[0]
                target_rows = pg_count(cursor, table)
                source_keys = sqlite_keys(sqlite_conn, table)
                if table == "booking_status_history":
                    source_keys = {
                        tuple(row[column] for column in PRIMARY_KEYS[table])
                        for row in sqlite_conn.execute(
                            """
                            SELECT h.*
                            FROM booking_status_history h
                            INNER JOIN trip_bookings b ON b.booking_id = h.booking_id
                            """
                        )
                    }
                target_keys = pg_keys(cursor, table)
                missing_keys = len(source_keys - target_keys)
                status = "PASS" if source_rows == target_rows and missing_keys == 0 else "FAIL"
                if status == "FAIL":
                    failures += 1
                print(
                    f"[{status}] table={table} source_rows={source_rows} "
                    f"target_rows={target_rows} missing_keys={missing_keys}"
                )

            valid_history, orphan_history, total_history = sqlite_booking_history_counts(sqlite_conn)
            archive_count = pg_count(cursor, ARCHIVE_TABLE) if table_exists(cursor, ARCHIVE_TABLE) else -1
            preserved_total = pg_count(cursor, "booking_status_history") + max(archive_count, 0)
            archive_status = (
                "PASS"
                if valid_history == 48
                and orphan_history == 17
                and total_history == 65
                and archive_count == orphan_history
                and preserved_total == total_history
                else "FAIL"
            )
            failures += 0 if archive_status == "PASS" else 1
            print(
                f"[{archive_status}] booking_history_repair valid_source={valid_history} "
                f"archived_source={orphan_history} source_total={total_history} "
                f"archived_target={archive_count} preserved_total={preserved_total}"
            )

            for label, sql in FOREIGN_KEY_CHECKS.items():
                cursor.execute(sql)
                violations = int(cursor.fetchone()[0])
                status = "PASS" if violations == 0 else "FAIL"
                failures += 0 if status == "PASS" else 1
                print(f"[{status}] foreign_key={label} violations={violations}")

            for label, sql in DUPLICATE_CHECKS.items():
                cursor.execute(sql)
                duplicates = int(cursor.fetchone()[0])
                status = "PASS" if duplicates == 0 else "FAIL"
                failures += 0 if status == "PASS" else 1
                print(f"[{status}] duplicate_check={label} duplicate_groups={duplicates}")

            for table, columns in REQUIRED_COLUMNS.items():
                if table not in tables:
                    continue
                for column in columns:
                    cursor.execute(null_check_sql(table, column))
                    nulls = int(cursor.fetchone()[0])
                    status = "PASS" if nulls == 0 else "FAIL"
                    failures += 0 if status == "PASS" else 1
                    print(f"[{status}] required_field={table}.{column} null_or_blank={nulls}")
    finally:
        pg_conn.close()
        sqlite_conn.close()

    if failures:
        print(f"Validation failed with {failures} issue(s). Do not cut over.")
        return 1
    print("Validation passed. Staging is eligible for application smoke tests.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
