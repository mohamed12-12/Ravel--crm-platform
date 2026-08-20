import os
from datetime import datetime
from pathlib import Path
from flask import Flask, session
from werkzeug.middleware.proxy_fix import ProxyFix
from sqlalchemy import text
from .config import config
from .extensions import db, migrate, login_manager, limiter, socketio
from .config import validate_config


def _running_under_pytest() -> bool:
    return bool(
        os.getenv("PYTEST_CURRENT_TEST")
        or os.getenv("PYTEST_ADDOPTS")
        or os.getenv("FLASK_ENV", "").lower() == "testing"
        or os.getenv("TESTING", "").lower() in {"1", "true", "yes"}
    )


MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "database" / "migrations"
OPERATIONAL_DB_PATH = (Path(__file__).resolve().parents[1] / "instance" / "rahma_traveler_dev.db").resolve()


def _green_psycopg2_if_running_under_eventlet() -> None:
    """Make psycopg2's blocking libpq calls cooperate with eventlet's hub.

    gunicorn's eventlet worker (gunicorn/workers/geventlet.py: patch())
    calls eventlet.monkey_patch() before this app is even created, which
    covers the stdlib socket module -- but psycopg2 talks to libpq through
    its own C extension, bypassing Python's socket module entirely, so the
    monkey-patch alone does NOT make Postgres queries cooperative. Left
    unpatched, one greenthread's Postgres query blocks the *entire*
    eventlet worker for its duration -- every other concurrent request
    that worker is holding (including Socket.IO connections) stalls too,
    silently defeating the reason eventlet was chosen for this service.
    Only apply when eventlet has actually monkey-patched this process
    (i.e. really running under `gunicorn --worker-class eventlet`) so
    local dev, tests, and the plain Flask dev server are untouched.
    """
    import eventlet.patcher

    if not eventlet.patcher.is_monkey_patched("socket"):
        return
    from eventlet.support.psycopg2_patcher import make_psycopg_green

    make_psycopg_green()


def _resolve_sqlite_path(uri: str) -> Path | None:
    if not uri.startswith("sqlite"):
        return None
    raw = uri.split("sqlite://", 1)[1]
    if raw.startswith("///"):
        raw = raw[3:]
    elif raw.startswith("//"):
        raw = raw[2:]
    candidate = Path(raw)
    return candidate.resolve()


def _fail_fast_on_operational_db_in_tests(app: Flask) -> None:
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    resolved = _resolve_sqlite_path(uri)
    if resolved is None or resolved != OPERATIONAL_DB_PATH:
        return
    if not (app.testing or _running_under_pytest()):
        return
    raise RuntimeError(
        "Refusing to initialize the operational CRM database during tests. "
        f"Resolved path: {resolved}"
    )


def _ensure_travelers_passport_columns(app: Flask) -> None:
    """Backfill passport columns for older SQLite databases.

    TODO(production): move this into an Alembic migration for non-demo storage
    backends and remove startup DDL once legacy SQLite files are retired.
    """
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if not uri.startswith("sqlite"):
        return

    passport_columns = {
        "passport_name": "TEXT",
        "passport_number": "TEXT",
        "passport_expiry": "DATE",
        "passport_nationality": "TEXT",
        "passport_attachment_ref": "TEXT",
        "preferred_currency": "VARCHAR(20)",
    }

    with app.app_context():
        inspector = db.inspect(db.engine)
        if "travelers" not in inspector.get_table_names():
            return

        existing_columns = {column["name"] for column in inspector.get_columns("travelers")}
        missing_columns = [
            (name, col_type)
            for name, col_type in passport_columns.items()
            if name not in existing_columns
        ]
        if not missing_columns:
            return

        with db.engine.begin() as connection:
            for column_name, column_type in missing_columns:
                connection.execute(text(f"ALTER TABLE travelers ADD COLUMN {column_name} {column_type}"))


def _ensure_trip_room_columns(app: Flask) -> None:
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if not uri.startswith("sqlite"):
        return

    trip_columns = {
        "boys_double": "INTEGER",
        "girls_double": "INTEGER",
        "boys_triple": "INTEGER",
        "girls_triple": "INTEGER",
        "draft_holds_boys_double": "INTEGER DEFAULT 0",
        "draft_holds_girls_double": "INTEGER DEFAULT 0",
        "draft_holds_boys_triple": "INTEGER DEFAULT 0",
        "draft_holds_girls_triple": "INTEGER DEFAULT 0",
    }

    with app.app_context():
        inspector = db.inspect(db.engine)
        if "trips" not in inspector.get_table_names():
            return

        existing_columns = {column["name"] for column in inspector.get_columns("trips")}
        missing_columns = [
            (name, col_type)
            for name, col_type in trip_columns.items()
            if name not in existing_columns
        ]
        if not missing_columns:
            return

        with db.engine.begin() as connection:
            for column_name, column_type in missing_columns:
                connection.execute(text(f"ALTER TABLE trips ADD COLUMN {column_name} {column_type}"))


def _ensure_private_trip_schema(app: Flask) -> None:
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if not uri.startswith("sqlite"):
        return

    with app.app_context():
        inspector = db.inspect(db.engine)
        table_names = set(inspector.get_table_names())
        with db.engine.begin() as connection:
            if "trips" in table_names:
                trip_columns = {column["name"] for column in inspector.get_columns("trips")}
                if "is_private" not in trip_columns:
                    connection.execute(text("ALTER TABLE trips ADD COLUMN is_private BOOLEAN NOT NULL DEFAULT 0"))
            if "booking_transactions" in table_names:
                transaction_columns = {column["name"] for column in inspector.get_columns("booking_transactions")}
                if "is_non_refundable" not in transaction_columns:
                    connection.execute(text("ALTER TABLE booking_transactions ADD COLUMN is_non_refundable BOOLEAN NOT NULL DEFAULT 0"))
            connection.execute(text(
                """
                CREATE TABLE IF NOT EXISTS private_trip_requests (
                    request_id VARCHAR(20) PRIMARY KEY,
                    traveler_id VARCHAR(20),
                    lead_id VARCHAR(50),
                    service_type VARCHAR(40) NOT NULL,
                    trip_scope VARCHAR(20) NOT NULL,
                    destination VARCHAR(200),
                    start_date_pref DATE,
                    end_date_pref DATE,
                    dates_flexible BOOLEAN NOT NULL DEFAULT 0,
                    party_size INTEGER NOT NULL DEFAULT 1,
                    boys_count INTEGER NOT NULL DEFAULT 0,
                    girls_count INTEGER NOT NULL DEFAULT 0,
                    budget_amount FLOAT,
                    budget_currency VARCHAR(3),
                    stage VARCHAR(40) NOT NULL DEFAULT 'registered',
                    stage_changed_at DATETIME NOT NULL,
                    assigned_to_user_id INTEGER,
                    assigned_to VARCHAR(100),
                    assigned_at DATETIME,
                    assigned_by_user_id INTEGER,
                    priority VARCHAR(50) DEFAULT 'Medium',
                    current_step VARCHAR(200),
                    channel VARCHAR(50),
                    follow_up_status VARCHAR(100),
                    follow_up_due_date DATE,
                    last_contact_at DATETIME,
                    customer_response_status VARCHAR(100),
                    consultation_due_at DATETIME,
                    consultation_done_at DATETIME,
                    design_due_at DATETIME,
                    design_delivered_at DATETIME,
                    deposit_amount FLOAT,
                    deposit_currency VARCHAR(3),
                    deposit_paid_at DATETIME,
                    deposit_is_refundable BOOLEAN NOT NULL DEFAULT 0,
                    converted_booking_id VARCHAR(50),
                    lost_reason TEXT,
                    notes TEXT,
                    created_at DATETIME NOT NULL,
                    created_by INTEGER,
                    idempotency_key VARCHAR(200) UNIQUE
                )
                """
            ))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_private_trip_requests_stage ON private_trip_requests(stage)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_private_trip_requests_traveler_id ON private_trip_requests(traveler_id)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_private_trip_requests_lead_id ON private_trip_requests(lead_id)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_private_trip_requests_created_at ON private_trip_requests(created_at)"))

            # CREATE TABLE IF NOT EXISTS above only covers a brand-new DB --
            # a private_trip_requests table created before the employee
            # follow-up/assignment-audit columns existed needs its own
            # backfill, same as trips.is_private above. Read via PRAGMA on
            # this same `connection` (not the `inspector` from before this
            # transaction started) -- the table may have just been created
            # above, inside this still-open transaction, and a separate
            # inspector-driven connection is not guaranteed to see it yet.
            request_columns = {
                row[1] for row in connection.execute(text("PRAGMA table_info(private_trip_requests)")).fetchall()
            }
            for column_name, column_type in (
                ("assigned_to", "VARCHAR(100)"),
                ("assigned_at", "DATETIME"),
                ("assigned_by_user_id", "INTEGER"),
                ("priority", "VARCHAR(50) DEFAULT 'Medium'"),
                ("current_step", "VARCHAR(200)"),
                ("channel", "VARCHAR(50)"),
                ("follow_up_status", "VARCHAR(100)"),
                ("follow_up_due_date", "DATE"),
                ("last_contact_at", "DATETIME"),
                ("customer_response_status", "VARCHAR(100)"),
            ):
                if column_name not in request_columns:
                    connection.execute(text(f"ALTER TABLE private_trip_requests ADD COLUMN {column_name} {column_type}"))


def _ensure_lead_and_booking_group_columns(app: Flask) -> None:
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if not uri.startswith("sqlite"):
        return

    with app.app_context():
        inspector = db.inspect(db.engine)
        if "leads" in inspector.get_table_names():
            lead_columns = {column["name"] for column in inspector.get_columns("leads")}
            missing_lead_columns = []
            if "group_size" not in lead_columns:
                missing_lead_columns.append("ALTER TABLE leads ADD COLUMN group_size INTEGER DEFAULT 1")
            if "passport_attachment_ref" not in lead_columns:
                missing_lead_columns.append("ALTER TABLE leads ADD COLUMN passport_attachment_ref TEXT")
            if "passport_status" not in lead_columns:
                missing_lead_columns.append("ALTER TABLE leads ADD COLUMN passport_status VARCHAR(50)")
            if missing_lead_columns:
                with db.engine.begin() as connection:
                    for statement in missing_lead_columns:
                        connection.execute(text(statement))

        if "trip_bookings" in inspector.get_table_names():
            booking_columns = {column["name"] for column in inspector.get_columns("trip_bookings")}
            if "group_size" not in booking_columns:
                with db.engine.begin() as connection:
                    connection.execute(text("ALTER TABLE trip_bookings ADD COLUMN group_size INTEGER DEFAULT 1"))
            for column_name, column_type in (
                ("room_group", "VARCHAR(50)"),
                ("boys_rooms_requested", "INTEGER DEFAULT 0"),
                ("girls_rooms_requested", "INTEGER DEFAULT 0"),
                ("room_requirements_json", "TEXT"),
                ("refund_amount", "FLOAT"),
            ):
                if column_name not in booking_columns:
                    with db.engine.begin() as connection:
                        connection.execute(text(f"ALTER TABLE trip_bookings ADD COLUMN {column_name} {column_type}"))

        if "trips" in inspector.get_table_names():
            trip_columns = {column["name"] for column in inspector.get_columns("trips")}
            for column_name in ("itinerary", "inclusions", "exclusions", "room_prices_json"):
                if column_name not in trip_columns:
                    with db.engine.begin() as connection:
                        connection.execute(text(f"ALTER TABLE trips ADD COLUMN {column_name} TEXT"))


def _ensure_employee_followup_columns(app: Flask) -> None:
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if not uri.startswith("sqlite"):
        return

    with app.app_context():
        inspector = db.inspect(db.engine)
        table_names = set(inspector.get_table_names())
        additions = {
            "leads": {
                "assigned_to": "VARCHAR(100)",
                "last_contact_at": "DATETIME",
                "customer_response_status": "VARCHAR(100)",
            },
            "trip_bookings": {
                "assigned_to": "VARCHAR(100)",
                "priority": "VARCHAR(50)",
                "next_follow_up_at": "DATETIME",
                "next_action": "VARCHAR(200)",
                "last_contact_at": "DATETIME",
                "customer_response_status": "VARCHAR(100)",
            },
        }
        with db.engine.begin() as connection:
            for table_name, columns in additions.items():
                if table_name not in table_names:
                    continue
                existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
                for column_name, column_type in columns.items():
                    if column_name not in existing_columns:
                        connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"))


def _ensure_relational_assignment_schema(app: Flask) -> None:
    """Add employee ownership tables/columns for existing SQLite CRM databases."""
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if not uri.startswith("sqlite"):
        return

    with app.app_context():
        inspector = db.inspect(db.engine)
        table_names = set(inspector.get_table_names())
        with db.engine.begin() as connection:
            if "users" not in table_names:
                connection.execute(text(
                    """
                    CREATE TABLE users (
                        id INTEGER PRIMARY KEY,
                        username VARCHAR(80) NOT NULL UNIQUE,
                        full_name VARCHAR(200),
                        password_hash VARCHAR(200) NOT NULL,
                        email VARCHAR(120) UNIQUE,
                        role VARCHAR(50) NOT NULL DEFAULT 'agent',
                        is_active BOOLEAN NOT NULL DEFAULT 1,
                        created_at DATETIME,
                        updated_at DATETIME,
                        last_login_at DATETIME
                    )
                    """
                ))
                table_names.add("users")
            user_columns = {column["name"] for column in inspector.get_columns("users")}
            user_additions = {
                "full_name": "VARCHAR(200)",
                "role": "VARCHAR(50) NOT NULL DEFAULT 'agent'",
                "updated_at": "DATETIME",
                "last_login_at": "DATETIME",
            }
            for column_name, column_type in user_additions.items():
                if column_name not in user_columns:
                    connection.execute(text(f"ALTER TABLE users ADD COLUMN {column_name} {column_type}"))

            additions = {
                "leads": {
                    "assigned_to_user_id": "INTEGER",
                    "assigned_at": "DATETIME",
                    "assigned_by_user_id": "INTEGER",
                },
                "trip_bookings": {
                    "assigned_to_user_id": "INTEGER",
                    "assigned_at": "DATETIME",
                    "assigned_by_user_id": "INTEGER",
                },
            }
            for table_name, columns in additions.items():
                if table_name not in table_names:
                    continue
                existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
                for column_name, column_type in columns.items():
                    if column_name not in existing_columns:
                        connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"))

            connection.execute(text(
                """
                CREATE TABLE IF NOT EXISTS assignment_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    resource_type VARCHAR(30) NOT NULL,
                    resource_id VARCHAR(80) NOT NULL,
                    previous_user_id INTEGER,
                    new_user_id INTEGER,
                    assigned_by_user_id INTEGER,
                    reason TEXT,
                    request_id VARCHAR(100),
                    created_at DATETIME NOT NULL,
                    FOREIGN KEY(previous_user_id) REFERENCES users(id),
                    FOREIGN KEY(new_user_id) REFERENCES users(id),
                    FOREIGN KEY(assigned_by_user_id) REFERENCES users(id)
                )
                """
            ))
            connection.execute(text(
                """
                CREATE TABLE IF NOT EXISTS user_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_user_id INTEGER,
                    target_user_id INTEGER,
                    action VARCHAR(80) NOT NULL,
                    details TEXT,
                    created_at DATETIME NOT NULL,
                    FOREIGN KEY(actor_user_id) REFERENCES users(id),
                    FOREIGN KEY(target_user_id) REFERENCES users(id)
                )
                """
            ))
            for index_name, table_name, column_name in (
                ("ix_leads_assigned_to_user_id", "leads", "assigned_to_user_id"),
                ("ix_trip_bookings_assigned_to_user_id", "trip_bookings", "assigned_to_user_id"),
                ("ix_assignment_history_resource", "assignment_history", "resource_id"),
            ):
                if table_name in table_names or table_name == "assignment_history":
                    connection.execute(text(
                        f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} ({column_name})"
                    ))

        if {"leads", "trip_bookings"}.issubset(table_names):
            from .services.assignments import backfill_legacy_assignments

            backfill_legacy_assignments()


def _ensure_booking_history_columns(app: Flask) -> None:
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if not uri.startswith("sqlite"):
        return

    with app.app_context():
        inspector = db.inspect(db.engine)
        if "booking_status_history" in inspector.get_table_names():
            return

        with db.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS booking_status_history (
                        history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        booking_id VARCHAR(50) NOT NULL,
                        old_status VARCHAR(50),
                        new_status VARCHAR(50),
                        changed_at DATETIME,
                        changed_by VARCHAR(50),
                        change_source VARCHAR(50),
                        notes TEXT,
                        FOREIGN KEY(booking_id) REFERENCES trip_bookings (booking_id)
                    )
                    """
                )
            )

def _ensure_booking_transactions_table(app: Flask) -> None:
    """Create the payment ledger on legacy SQLite files.

    Same treatment booking_status_history gets: the Alembic migration is the
    real definition, but this repo's SQLite deployments are also reached
    through runtime schema checks, and a missing table here would 500 the
    booking page rather than degrade quietly.
    """
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if not uri.startswith("sqlite"):
        return

    with app.app_context():
        inspector = db.inspect(db.engine)
        if "booking_transactions" in inspector.get_table_names():
            return

        with db.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS booking_transactions (
                        transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        public_ref VARCHAR(24),
                        booking_id VARCHAR(50) NOT NULL,
                        traveler_id VARCHAR(20),
                        entry_type VARCHAR(16) NOT NULL,
                        amount FLOAT NOT NULL,
                        currency VARCHAR(3) NOT NULL,
                        occurred_on DATE NOT NULL,
                        date_precision VARCHAR(16) NOT NULL DEFAULT 'exact',
                        method VARCHAR(40),
                        reference VARCHAR(120),
                        reason TEXT,
                        notes TEXT,
                        source VARCHAR(32) NOT NULL DEFAULT 'crm_ui',
                        is_inferred BOOLEAN NOT NULL DEFAULT 0,
                        reverses_id INTEGER,
                        idempotency_key VARCHAR(200),
                        created_by_user_id INTEGER,
                        created_at DATETIME NOT NULL,
                        CONSTRAINT ck_booking_transactions_amount_positive CHECK (amount > 0),
                        CONSTRAINT ck_booking_transactions_entry_type
                            CHECK (entry_type IN ('payment', 'refund')),
                        FOREIGN KEY(booking_id) REFERENCES trip_bookings (booking_id),
                        FOREIGN KEY(traveler_id) REFERENCES travelers (traveler_id),
                        FOREIGN KEY(reverses_id) REFERENCES booking_transactions (transaction_id),
                        FOREIGN KEY(created_by_user_id) REFERENCES users (id)
                    )
                    """
                )
            )
            for statement in (
                "CREATE INDEX IF NOT EXISTS ix_booking_transactions_booking_id ON booking_transactions (booking_id)",
                "CREATE INDEX IF NOT EXISTS ix_booking_transactions_traveler_id ON booking_transactions (traveler_id)",
                "CREATE INDEX IF NOT EXISTS ix_booking_transactions_created_at ON booking_transactions (created_at)",
                "CREATE INDEX IF NOT EXISTS ix_booking_transactions_reverses_id ON booking_transactions (reverses_id)",
                "CREATE INDEX IF NOT EXISTS ix_booking_transactions_booking_entry ON booking_transactions (booking_id, entry_type)",
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_booking_transactions_public_ref ON booking_transactions (public_ref)",
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_booking_transactions_idempotency_key ON booking_transactions (idempotency_key)",
            ):
                connection.execute(text(statement))


def _ensure_traveler_documents_table(app: Flask) -> None:
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if not uri.startswith("sqlite"):
        return

    with app.app_context():
        inspector = db.inspect(db.engine)
        if "traveler_documents" in inspector.get_table_names():
            return

        with db.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS traveler_documents (
                        document_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        traveler_id VARCHAR(20) NOT NULL,
                        category VARCHAR(50) NOT NULL DEFAULT 'document',
                        file_name VARCHAR(255) NOT NULL,
                        original_file_name VARCHAR(255),
                        mime_type VARCHAR(100),
                        file_extension VARCHAR(20),
                        file_size INTEGER,
                        storage_path VARCHAR(500) NOT NULL,
                        uploaded_at DATETIME NOT NULL,
                        uploaded_by VARCHAR(100),
                        passport_full_name VARCHAR(200),
                        passport_number VARCHAR(50),
                        passport_nationality VARCHAR(100),
                        passport_expiry DATE,
                        verification_status VARCHAR(50) DEFAULT 'pending',
                        notes TEXT,
                        FOREIGN KEY(traveler_id) REFERENCES travelers (traveler_id)
                    )
                    """
                )
            )


def _ensure_trip_media_table(app: Flask) -> None:
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if not uri.startswith("sqlite"):
        return

    with app.app_context():
        inspector = db.inspect(db.engine)
        if "trip_media" in inspector.get_table_names():
            return

        with db.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS trip_media (
                        media_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        public_id VARCHAR(36) NOT NULL UNIQUE,
                        trip_id VARCHAR(50) NOT NULL,
                        storage_key VARCHAR(500) NOT NULL UNIQUE,
                        public_url VARCHAR(500) NOT NULL,
                        image_type VARCHAR(20) NOT NULL DEFAULT 'gallery',
                        alt_text VARCHAR(255),
                        display_order INTEGER NOT NULL DEFAULT 0,
                        original_filename VARCHAR(255),
                        mime_type VARCHAR(100) NOT NULL,
                        file_extension VARCHAR(10) NOT NULL,
                        file_size INTEGER NOT NULL DEFAULT 0,
                        uploaded_by_user_id INTEGER,
                        uploaded_by_name VARCHAR(100),
                        created_at DATETIME NOT NULL,
                        is_active BOOLEAN NOT NULL DEFAULT 1,
                        verification_status VARCHAR(50) NOT NULL DEFAULT 'verified',
                        FOREIGN KEY(trip_id) REFERENCES trips (trip_id),
                        FOREIGN KEY(uploaded_by_user_id) REFERENCES users (id)
                    )
                    """
                )
            )
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_trip_media_trip_id ON trip_media (trip_id)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_trip_media_public_id ON trip_media (public_id)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_trip_media_image_type ON trip_media (image_type)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_trip_media_is_active ON trip_media (is_active)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_trip_media_verification_status ON trip_media (verification_status)"))


def _normalize_sqlite_temporal_values(app: Flask) -> None:
    """Convert legacy slash-formatted SQLite date strings into SQLAlchemy-friendly ISO values."""
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if not uri.startswith("sqlite"):
        return

    datetime_formats = (
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y",
    )
    date_formats = (
        "%m/%d/%Y",
        "%m/%d/%y",
    )
    temporal_columns = {
        "travelers": {
            "created_at": "datetime",
            "last_contacted_at": "datetime",
            "birthday": "date",
            "passport_expiry": "date",
        },
        "leads": {
            "created_at": "datetime",
            "updated_at": "datetime",
            "follow_up_due_date": "date",
            "last_contact_at": "datetime",
        },
        "trip_bookings": {
            "draft_created_at": "datetime",
            "next_follow_up_at": "datetime",
            "last_contact_at": "datetime",
        },
        "ce_bookings": {
            "created_at": "datetime",
        },
        "handoff_queue": {
            "created_at": "datetime",
        },
        "traveler_documents": {
            "uploaded_at": "datetime",
            "passport_expiry": "date",
        },
        "booking_status_history": {
            "changed_at": "datetime",
        },
    }

    def normalize_temporal(raw_value: str, temporal_kind: str) -> str | None:
        value = str(raw_value or "").strip()
        if not value or "/" not in value:
            return None
        if temporal_kind == "date":
            for fmt in date_formats:
                try:
                    return datetime.strptime(value, fmt).date().isoformat()
                except ValueError:
                    continue
            return None
        for fmt in datetime_formats:
            try:
                parsed = datetime.strptime(value, fmt)
                return parsed.replace(microsecond=0).isoformat(sep=" ")
            except ValueError:
                continue
        return None

    with app.app_context():
        inspector = db.inspect(db.engine)
        table_names = set(inspector.get_table_names())
        with db.engine.begin() as connection:
            for table_name, columns in temporal_columns.items():
                if table_name not in table_names:
                    continue
                existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
                for column_name, temporal_kind in columns.items():
                    if column_name not in existing_columns:
                        continue
                    rows = connection.execute(
                        text(
                            f"""
                            SELECT rowid, {column_name}
                            FROM {table_name}
                            WHERE {column_name} IS NOT NULL
                              AND instr(CAST({column_name} AS TEXT), '/') > 0
                            """
                        )
                    ).fetchall()
                    for rowid, raw_value in rows:
                        normalized = normalize_temporal(raw_value, temporal_kind)
                        if not normalized:
                            continue
                        connection.execute(
                            text(f"UPDATE {table_name} SET {column_name} = :value WHERE rowid = :rowid"),
                            {"value": normalized, "rowid": rowid},
                        )

def create_app(config_name=None):
    if config_name is None:
        config_name = os.getenv('FLASK_CONFIG', 'default')

    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    app.config["CRM_AUTH_ENABLED"] = os.environ.get(
        "CRM_AUTH_ENABLED", "true"
    ).strip().lower() in {"1", "true", "yes", "on"}
    app.config["CRM_API_TOKEN"] = os.environ.get("CRM_API_TOKEN", "").strip()
    app.config["CRM_API_TOKEN_ROLE"] = (os.environ.get("CRM_API_TOKEN_ROLE", "").strip().lower() or "agent")
    app.config["DATA_AUTHORITY"] = os.environ.get("DATA_AUTHORITY", "crm").strip().lower()
    app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024
    app.config.from_object(config[config_name])
    if not app.config.get("SECRET_KEY") and config_name != "production":
        app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-key-rahma-traveler")
    app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
    app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")
    app.config.setdefault("SESSION_COOKIE_SECURE", config_name == "production")
    app.config.setdefault("REMEMBER_COOKIE_HTTPONLY", True)
    app.config.setdefault("REMEMBER_COOKIE_SAMESITE", "Lax")
    app.config.setdefault("REMEMBER_COOKIE_SECURE", config_name == "production")

    # Resolve DATABASE_URL at runtime so tests (and any code that sets
    # os.environ['DATABASE_URL'] before calling create_app) always get the
    # correct URI rather than the value frozen at module-import time.
    cfg_class = config[config_name]
    if hasattr(cfg_class, 'get_sqlalchemy_uri'):
        app.config['SQLALCHEMY_DATABASE_URI'] = cfg_class.get_sqlalchemy_uri()

    # SQLAlchemy's own defaults (pool_size=5, no pre-ping, no recycle) were
    # never overridden here, and SQLite doesn't support these options at
    # all (only relevant once DATABASE_URL is real Postgres, e.g.
    # production). Each gunicorn worker is a separate process with its own
    # pool, so total possible connections scale with worker count -- size
    # Postgres's own max_connections with that in mind if the worker count
    # ever grows materially past what's running today.
    resolved_uri = str(app.config.get('SQLALCHEMY_DATABASE_URI') or '')
    if resolved_uri.startswith('postgresql'):
        app.config.setdefault('SQLALCHEMY_ENGINE_OPTIONS', {
            'pool_size': int(os.environ.get('DB_POOL_SIZE', '5')),
            'max_overflow': int(os.environ.get('DB_MAX_OVERFLOW', '10')),
            'pool_pre_ping': True,
            'pool_recycle': int(os.environ.get('DB_POOL_RECYCLE_SECONDS', '280')),
        })
        _green_psycopg2_if_running_under_eventlet()

    _fail_fast_on_operational_db_in_tests(app)
    config_errors = validate_config(config_name)
    if config_errors:
        raise RuntimeError("Configuration error: " + " | ".join(config_errors))
    app.config.setdefault('TRAVELER_UPLOAD_ROOT', str(Path(app.instance_path) / 'uploads' / 'travelers'))
    app.config.setdefault('TRIP_MEDIA_ROOT', str(Path(app.instance_path) / 'uploads' / 'trips'))
    app.config.setdefault('TRIP_MEDIA_MAX_BYTES', int(os.environ.get("TRIP_MEDIA_MAX_BYTES", str(5 * 1024 * 1024))))

    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db, directory=str(MIGRATIONS_DIR))
    login_manager.init_app(app)

    # RATELIMIT_ENABLED is read once by Limiter.init_app(), so it must be
    # resolved before that call. app.config["TESTING"] is typically set by
    # test fixtures AFTER create_app() returns (too late), so default to
    # off under pytest via the same env-var detection used above; an
    # explicit RATELIMIT_ENABLED env var always wins so tests that want to
    # exercise real rate limiting can opt back in.
    ratelimit_override = os.environ.get("RATELIMIT_ENABLED")
    if ratelimit_override is not None:
        app.config["RATELIMIT_ENABLED"] = ratelimit_override.strip().lower() in {"1", "true", "yes", "on"}
    else:
        app.config.setdefault("RATELIMIT_ENABLED", not _running_under_pytest())
    limiter.init_app(app)

    from .models.user import User
    from .models.assignment_history import AssignmentHistory
    from .models.user_audit import UserAuditLog

    # Booking timeline auditing hangs off the session itself rather than the
    # routes, so every write path -- CRM UI, booking automation, agent bridge
    # -- records history without having to opt in. See
    # app/services/booking_audit.py.
    from .services.booking_audit import register_booking_audit_listeners
    register_booking_audit_listeners()

    # The payment ledger is append-only: the guard rejects any attempt to
    # update or delete a recorded transaction, so a correction has to be a
    # reversal rather than a quiet rewrite of financial history.
    from .models.booking_transaction import BookingTransaction
    from .services.booking_ledger import register_booking_ledger_listeners
    register_booking_ledger_listeners()

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))
    
    from .extensions import socketio
    socketio.init_app(app)
    _ensure_travelers_passport_columns(app)
    _ensure_trip_room_columns(app)
    _ensure_private_trip_schema(app)
    _ensure_lead_and_booking_group_columns(app)
    _ensure_employee_followup_columns(app)
    _ensure_relational_assignment_schema(app)
    _ensure_booking_history_columns(app)
    _ensure_booking_transactions_table(app)
    _ensure_traveler_documents_table(app)
    _ensure_trip_media_table(app)
    _normalize_sqlite_temporal_values(app)

    # Register Blueprints
    from .routes.travelers import travelers_bp
    from .routes.trips import trips_bp
    from .routes.bookings import bookings_bp
    from .routes.leads import leads_bp
    from .routes.interactions import interactions_bp
    from .routes.handoffs import handoffs_bp
    from .routes.admin import admin_bp
    from .routes.private_requests import private_requests_bp
    from .routes.copy import copy_bp
    from .routes.crm import crm_bp
    from .routes.api_docs import api_docs_bp
    from .routes.auth import auth_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(travelers_bp)
    app.register_blueprint(trips_bp)
    app.register_blueprint(bookings_bp)
    app.register_blueprint(leads_bp)
    app.register_blueprint(interactions_bp)
    app.register_blueprint(handoffs_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(private_requests_bp)
    app.register_blueprint(copy_bp)
    app.register_blueprint(crm_bp)
    app.register_blueprint(api_docs_bp)

    from .security import crm_request_guard
    app.before_request(crm_request_guard)

    from .security import current_role, current_user, generate_csrf_token

    @app.context_processor
    def inject_security_helpers():
        return {
            "csrf_token": generate_csrf_token,
            "current_employee": current_user(),
            "current_employee_role": current_role(),
        }

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if config_name == "production":
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'",
            )
        return response
    
    # Root redirect
    from flask import redirect, url_for
    @app.route('/')
    def index():
        return redirect(url_for('admin.dashboard'))

    return app
