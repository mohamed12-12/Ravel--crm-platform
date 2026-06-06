import os
from flask import Flask
from .config import config
from .extensions import db, migrate, login_manager, socketio

def create_app(config_name=None):
    if config_name is None:
        config_name = os.getenv('FLASK_CONFIG', 'default')

    app = Flask(__name__)
    app.config.from_object(config[config_name])

    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    
    from .models.user import User
    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))
    
    from .extensions import socketio
    socketio.init_app(app)

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
