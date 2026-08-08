from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
import os
from flask_socketio import SocketIO
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()

# Socket.IO state (who's connected to which handoff room) and the rate
# limiter's request counters both default to living in this process's own
# memory -- correct only as long as exactly one worker process is running.
# Confirmed live (2026-08-08): rahma-crm-api actually runs `gunicorn -w 2`,
# two independent OS processes, with no message_queue configured -- a
# handoff notification fired on the process that isn't holding the target
# admin's websocket connection is silently dropped, right now, roughly half
# the time. Setting REDIS_URL gives every worker process a shared backend
# for both instead; leaving it unset keeps today's in-memory behavior
# unchanged (e.g. local dev, tests, or a deployment intentionally pinned to
# exactly one worker like rahma-agent -- see deploy/systemd/rahma-ai-agent.service).
_REDIS_URL = os.environ.get("REDIS_URL", "").strip()

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["300 per hour"],
    storage_uri=_REDIS_URL or "memory://",
)
socketio = SocketIO(
    cors_allowed_origins=os.environ.get(
        "SOCKETIO_CORS_ALLOWED_ORIGINS",
        "http://127.0.0.1:3000,http://localhost:3000,http://127.0.0.1:3001,http://localhost:3001,http://127.0.0.1:3002,http://localhost:3002",
    ),
    message_queue=_REDIS_URL or None,
)

# Configure Login Manager
login_manager.login_view = 'auth.login'
login_manager.login_message_category = 'info'
