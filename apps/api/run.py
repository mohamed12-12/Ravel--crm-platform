import os
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
APP_ROOT = CURRENT_DIR
REPO_ROOT = CURRENT_DIR.parents[1]

for path in (APP_ROOT, REPO_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from app import create_app
from app.extensions import socketio


app = create_app(os.getenv('FLASK_CONFIG', 'default'))


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}

if __name__ == '__main__':
    debug = _env_flag("FLASK_DEBUG", default=False)
    socketio.run(
        app,
        debug=debug,
        use_reloader=_env_flag("FLASK_USE_RELOADER", default=debug),
        # CRM_API_PORT takes priority over PORT: apps/middleware also reads
        # the generic PORT var from the same shared .env (apps/middleware/src/index.ts),
        # so relying on PORT alone here means the two services fight over
        # the same port whenever both read the shared .env. CRM_API_PORT
        # lets this service have its own dedicated setting; PORT is kept as
        # a fallback for anyone already relying on it for a single-service run.
        port=int(os.getenv("CRM_API_PORT", os.getenv("PORT", "5000"))),
        # Flask-SocketIO refuses to start its dev server without this unless
        # debug=True - local/demo use only. Production serves via gunicorn +
        # eventlet instead (see deploy/systemd/rahma-crm-api.service), which
        # doesn't go through socketio.run() at all.
        allow_unsafe_werkzeug=True,
    )
