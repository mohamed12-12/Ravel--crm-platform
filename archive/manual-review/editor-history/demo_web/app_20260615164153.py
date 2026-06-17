from __future__ import annotations

from archive.legacy_demo_web.app import create_app
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


__all__ = ["create_app"]


if __name__ == "__main__":
    app = create_app()
    settings = app.config["SETTINGS"]
    app.run(host=settings.app_host, port=settings.app_port, debug=(settings.app_env == "development"))
