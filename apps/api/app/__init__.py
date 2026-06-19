import os
from pathlib import Path
from flask import Flask
from sqlalchemy import text
from .config import config
from .extensions import db, migrate, login_manager, socketio


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

def create_app(config_name=None):
    if config_name is None:
        config_name = os.getenv('FLASK_CONFIG', 'default')

    app = Flask(__name__)
    app.config.from_object(config[config_name])

    # Resolve DATABASE_URL at runtime so tests (and any code that sets
    # os.environ['DATABASE_URL'] before calling create_app) always get the
    # correct URI rather than the value frozen at module-import time.
    cfg_class = config[config_name]
    if hasattr(cfg_class, 'get_sqlalchemy_uri'):
        app.config['SQLALCHEMY_DATABASE_URI'] = cfg_class.get_sqlalchemy_uri()

    _fail_fast_on_operational_db_in_tests(app)

    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db, directory=str(MIGRATIONS_DIR))
    login_manager.init_app(app)
    
    from .models.user import User
    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))
    
    from .extensions import socketio
    socketio.init_app(app)
    _ensure_travelers_passport_columns(app)
    _ensure_trip_room_columns(app)
    _ensure_booking_history_columns(app)

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
    
    # Root redirect
    from flask import redirect, url_for
    @app.route('/')
    def index():
        return redirect(url_for('admin.dashboard'))

    return app
