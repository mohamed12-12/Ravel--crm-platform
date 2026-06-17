import os
from pathlib import Path
from flask import Flask
from sqlalchemy import text
from .config import config
from .extensions import db, migrate, login_manager, socketio


MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "database" / "migrations"


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

def create_app(config_name=None):
    if config_name is None:
        config_name = os.getenv('FLASK_CONFIG', 'default')

    app = Flask(__name__)
    app.config.from_object(config[config_name])

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
