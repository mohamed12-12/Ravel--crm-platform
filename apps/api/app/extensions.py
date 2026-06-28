from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
import os
from flask_socketio import SocketIO

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
socketio = SocketIO(
    cors_allowed_origins=os.environ.get(
        "SOCKETIO_CORS_ALLOWED_ORIGINS",
        "http://127.0.0.1:5001,http://localhost:5001,http://127.0.0.1:5000,http://localhost:5000",
    )
)

# Configure Login Manager
login_manager.login_view = 'auth.login'
login_manager.login_message_category = 'info'
