"""Safely migrate Rahma CRM data from SQLite to PostgreSQL.

The script opens SQLite in read-only mode and writes to PostgreSQL only when a
PostgreSQL URL is explicitly supplied and --dry-run is not enabled.
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

MIGRATION_ORDER = [
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

ARCHIVE_TABLES = {"legacy_booking_status_history_orphans"}

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
    "legacy_booking_status_history_orphans": ("history_id",),
}

SERIAL_PRIMARY_KEYS = {
    "users": "id",
    "language_templates": "id",
    "booking_status_history": "history_id",
    "traveler_documents": "document_id",
    "trip_media": "media_id",
    "assignment_history": "id",
    "user_audit_log": "id",
}

BOOLEAN_COLUMNS: dict[str, set[str]] = {
    "users": {"is_active"},
    "dm_copy_library": {"active"},
    "leads": {"handoff_required"},
    "interactions": {"handoff_required"},
    "trip_bookings": {"passport_required"},
    "trip_media": {"is_active"},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate Rahma CRM SQLite rows to PostgreSQL safely.",
    )
    parser.add_argument(
        "--sqlite-path",
        default=str(DEFAULT_SQLITE_PATH),
        help="Path to the source SQLite database. Opened read-only.",
    )
    parser.add_argument(
        "--postgres-url",
        default=os.environ.get("POSTGRES_URL") or os.environ.get("DATABASE_URL"),
        help="Target PostgreSQL URL. Required unless --dry-run is used.",
    )
    parser.add_argument(
        "--tables",
        help="Comma-separated table list. Defaults to the full dependency-safe order.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Rows per insert batch.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read SQLite and print table summaries without writing PostgreSQL.",
    )
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
        return list(MIGRATION_ORDER)
    requested = [item.strip() for item in raw_tables.split(",") if item.strip()]
    unknown = sorted(set(requested) - set(MIGRATION_ORDER))
    if unknown:
        raise SystemExit(f"Unknown table(s): {', '.join(unknown)}")
    requested_set = set(requested)
    return [table for table in MIGRATION_ORDER if table in requested_set]


def sqlite_connect_readonly(path: str) -> sqlite3.Connection:
    resolved = Path(path).resolve()
    if not resolved.exists():
        raise SystemExit(f"SQLite database not found: {resolved}")
    uri = f"file:{resolved.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
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


def table_columns(sqlite_conn: sqlite3.Connection, table: str) -> list[str]:
    rows = sqlite_conn.execute(f"PRAGMA table_info({quote_ident(table)})").fetchall()
    if not rows:
        raise SystemExit(f"SQLite table not found: {table}")
    return [str(row["name"]) for row in rows]


def source_count(sqlite_conn: sqlite3.Connection, table: str) -> int:
    row = sqlite_conn.execute(f"SELECT COUNT(*) AS count FROM {quote_ident(table)}").fetchone()
    return int(row["count"])


def normalize_value(table: str, column: str, value: Any) -> Any:
    if column in BOOLEAN_COLUMNS.get(table, set()):
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "y"}:
                return True
            if normalized in {"0", "false", "no", "n", ""}:
                return False
        return value
    return value


def iter_rows(
    sqlite_conn: sqlite3.Connection,
    table: str,
    columns: list[str],
    batch_size: int,
) -> Any:
    column_sql = ", ".join(quote_ident(column) for column in columns)
    cursor = sqlite_conn.execute(f"SELECT {column_sql} FROM {quote_ident(table)}")
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break
        yield [
            tuple(normalize_value(table, column, row[column]) for column in columns)
            for row in rows
        ]


def target_count(pg_cursor: Any, table: str) -> int:
    pg_cursor.execute(f"SELECT COUNT(*) FROM {quote_ident(table)}")
    return int(pg_cursor.fetchone()[0])


def reset_sequence(pg_cursor: Any, table: str, pk_column: str) -> None:
    pg_cursor.execute(
        "SELECT setval(pg_get_serial_sequence(%s, %s), "
        f"COALESCE((SELECT MAX({quote_ident(pk_column)}) FROM {quote_ident(table)}), 1), "
        f"(SELECT COUNT(*) > 0 FROM {quote_ident(table)}))",
        (table, pk_column),
    )


def migrate_table(
    sqlite_conn: sqlite3.Connection,
    pg_conn: Any,
    table: str,
    batch_size: int,
) -> tuple[int, int, int]:
    columns = table_columns(sqlite_conn, table)
    pk_columns = PRIMARY_KEYS[table]
    column_sql = ", ".join(quote_ident(column) for column in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    conflict_sql = ", ".join(quote_ident(column) for column in pk_columns)
    insert_sql = (
        f"INSERT INTO {quote_ident(table)} ({column_sql}) "
        f"VALUES ({placeholders}) ON CONFLICT ({conflict_sql}) DO NOTHING"
    )

    source_rows = source_count(sqlite_conn, table)
    inserted = 0
    with pg_conn.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL DEFERRED")
        before = target_count(cursor, table)
        for batch in iter_rows(sqlite_conn, table, columns, batch_size):
            cursor.executemany(insert_sql, batch)
        after = target_count(cursor, table)
        inserted = max(0, after - before)
        if table in SERIAL_PRIMARY_KEYS:
            reset_sequence(cursor, table, SERIAL_PRIMARY_KEYS[table])
        final_count = target_count(cursor, table)
    pg_conn.commit()
    return source_rows, inserted, final_count


def migrate_booking_status_history(
    sqlite_conn: sqlite3.Connection,
    pg_conn: Any,
    batch_size: int,
) -> tuple[int, int, int, int, int]:
    table = "booking_status_history"
    archive_table = "legacy_booking_status_history_orphans"
    source_rows = source_count(sqlite_conn, table)

    valid_columns = table_columns(sqlite_conn, table)
    valid_column_sql = ", ".join(quote_ident(column) for column in valid_columns)
    valid_placeholders = ", ".join(["%s"] * len(valid_columns))
    valid_insert_sql = (
        f"INSERT INTO {quote_ident(table)} ({valid_column_sql}) "
        f"VALUES ({valid_placeholders}) ON CONFLICT ({quote_ident('history_id')}) DO NOTHING"
    )

    archive_columns = [
        "history_id",
        "booking_id",
        "original_booking_id",
        "old_status",
        "new_status",
        "changed_at",
        "changed_by",
        "change_source",
        "notes",
        "source_table",
        "archived_reason",
    ]
    archive_column_sql = ", ".join(quote_ident(column) for column in archive_columns)
    archive_placeholders = ", ".join(["%s"] * len(archive_columns))
    archive_insert_sql = (
        f"INSERT INTO {quote_ident(archive_table)} ({archive_column_sql}) "
        f"VALUES ({archive_placeholders}) ON CONFLICT ({quote_ident('history_id')}) DO NOTHING"
    )

    valid_sql = """
        SELECT h.*
        FROM booking_status_history h
        INNER JOIN trip_bookings b ON b.booking_id = h.booking_id
        ORDER BY h.history_id
    """
    orphan_sql = """
        SELECT h.*
        FROM booking_status_history h
        LEFT JOIN trip_bookings b ON b.booking_id = h.booking_id
        WHERE h.booking_id IS NOT NULL AND b.booking_id IS NULL
        ORDER BY h.history_id
    """

    def fetch_batches(sql: str) -> Any:
        cursor = sqlite_conn.execute(sql)
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            yield rows

    with pg_conn.cursor() as cursor:
        before_valid = target_count(cursor, table)
        before_archive = target_count(cursor, archive_table)

        for batch in fetch_batches(valid_sql):
            payload = [tuple(row[column] for column in valid_columns) for row in batch]
            cursor.executemany(valid_insert_sql, payload)

        for batch in fetch_batches(orphan_sql):
            payload = [
                (
                    row["history_id"],
                    row["booking_id"],
                    row["booking_id"],
                    row["old_status"],
                    row["new_status"],
                    row["changed_at"],
                    row["changed_by"],
                    row["change_source"],
                    row["notes"],
                    "booking_status_history",
                    "missing_trip_booking_parent",
                )
                for row in batch
            ]
            cursor.executemany(archive_insert_sql, payload)

        reset_sequence(cursor, table, "history_id")
        after_valid = target_count(cursor, table)
        after_archive = target_count(cursor, archive_table)
    pg_conn.commit()
    return (
        source_rows,
        max(0, after_valid - before_valid),
        after_valid,
        max(0, after_archive - before_archive),
        after_archive,
    )


def main() -> int:
    args = parse_args()
    tables = select_tables(args.tables)
    sqlite_conn = sqlite_connect_readonly(args.sqlite_path)

    print(f"Source SQLite: {Path(args.sqlite_path).resolve()}")
    print(f"Selected tables: {', '.join(tables)}")

    if args.dry_run:
        for table in tables:
            columns = table_columns(sqlite_conn, table)
            print(
                f"[DRY RUN] table={table} source_rows={source_count(sqlite_conn, table)} "
                f"columns={len(columns)}"
            )
        print("Dry run complete. No PostgreSQL writes were attempted.")
        return 0

    if not args.postgres_url or args.postgres_url.startswith("sqlite"):
        raise SystemExit("--postgres-url must be a PostgreSQL URL when not using --dry-run")

    driver_name, driver = load_postgres_driver()
    print(f"Target PostgreSQL: {mask_url(args.postgres_url)}")
    print(f"Driver: {driver_name}")

    pg_conn = driver.connect(args.postgres_url)
    try:
        for table in tables:
            if table == "booking_status_history":
                source_rows, new_valid, target_valid, new_archive, target_archive = migrate_booking_status_history(
                    sqlite_conn,
                    pg_conn,
                    max(1, args.batch_size),
                )
                print(
                    f"[MIGRATED] table=booking_status_history source_rows={source_rows} "
                    f"valid_new_rows={new_valid} valid_target_rows={target_valid} "
                    f"archived_new_rows={new_archive} archived_target_rows={target_archive}"
                )
                continue
            source_rows, inserted, target_rows = migrate_table(
                sqlite_conn,
                pg_conn,
                table,
                max(1, args.batch_size),
            )
            print(
                f"[MIGRATED] table={table} source_rows={source_rows} "
                f"new_rows={inserted} target_rows={target_rows}"
            )
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        pg_conn.close()
        sqlite_conn.close()

    print("Migration complete. Run tools/validate_postgres_migration.py before cutover.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
