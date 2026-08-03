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
        "http://127.0.0.1:3000,http://localhost:3000,http://127.0.0.1:3001,http://localhost:3001,http://127.0.0.1:3002,http://localhost:3002",
    )
)

# Configure Login Manager
login_manager.login_view = 'auth.login'
login_manager.login_message_category = 'info'
