import os
from datetime import datetime
from pathlib import Path
from flask import Flask
from sqlalchemy import text
from .config import config
from .extensions import db, migrate, login_manager, socketio
from .config import validate_config


MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "database" / "migrations"
OPERATIONAL_DB_PATH = (Path(__file__).resolve().parents[1] / "instance" / "rahma_traveler_dev.db").resolve()


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
    if not (
        app.testing
        or os.getenv("PYTEST_CURRENT_TEST")
        or os.getenv("PYTEST_ADDOPTS")
        or os.getenv("FLASK_ENV", "").lower() == "testing"
        or os.getenv("TESTING", "").lower() in {"1", "true", "yes"}
    ):
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


def _ensure_lead_and_booking_group_columns(app: Flask) -> None:
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if not uri.startswith("sqlite"):
        return

    with app.app_context():
        inspector = db.inspect(db.engine)
        if "leads" in inspector.get_table_names():
            lead_columns = {column["name"] for column in inspector.get_columns("leads")}
            if "group_size" not in lead_columns:
                with db.engine.begin() as connection:
                    connection.execute(text("ALTER TABLE leads ADD COLUMN group_size INTEGER DEFAULT 1"))

        if "trip_bookings" in inspector.get_table_names():
            booking_columns = {column["name"] for column in inspector.get_columns("trip_bookings")}
            if "group_size" not in booking_columns:
                with db.engine.begin() as connection:
                    connection.execute(text("ALTER TABLE trip_bookings ADD COLUMN group_size INTEGER DEFAULT 1"))


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
        },
        "trip_bookings": {
            "draft_created_at": "datetime",
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

    _fail_fast_on_operational_db_in_tests(app)
    config_errors = validate_config(config_name)
    if config_errors:
        raise RuntimeError("Configuration error: " + " | ".join(config_errors))
    app.config.setdefault('TRAVELER_UPLOAD_ROOT', str(Path(app.instance_path) / 'uploads' / 'travelers'))

    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db, directory=str(MIGRATIONS_DIR))
    login_manager.init_app(app)
    
    from .models.user import User
    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))
    
    from .extensions import socketio
    socketio.init_app(app)
    _normalize_sqlite_temporal_values(app)

    # Register Blueprints
    from .routes.travelers import travelers_bp
    from .routes.trips import trips_bp
    from .routes.bookings import bookings_bp
    from .routes.leads import leads_bp
    from .routes.interactions import interactions_bp
    from .routes.handoffs import handoffs_bp
    from .routes.admin import admin_bp
    from .routes.copy import copy_bp
    from .routes.crm import crm_bp

    app.register_blueprint(travelers_bp)
    app.register_blueprint(trips_bp)
    app.register_blueprint(bookings_bp)
    app.register_blueprint(leads_bp)
    app.register_blueprint(interactions_bp)
    app.register_blueprint(handoffs_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(copy_bp)
    app.register_blueprint(crm_bp)

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
