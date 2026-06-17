import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from app.extensions import socketio


app = create_app(os.getenv('FLASK_CONFIG', 'default'))

if __name__ == '__main__':
    socketio.run(app, debug=True, port=5000)
