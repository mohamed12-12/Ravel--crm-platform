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
        port=int(os.getenv("PORT", "5000")),
    )
