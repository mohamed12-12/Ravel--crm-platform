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

if __name__ == '__main__':
    socketio.run(app, debug=True, port=5000)
