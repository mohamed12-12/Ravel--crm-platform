from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure project root is on sys.path so top-level packages (like `archive`) are importable
# when running this file directly (e.g., `python demo_web/app.py`).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from archive.legacy_demo_web.app import create_app
except ModuleNotFoundError:
    from services.ai_agent.ai_agent_app.server import create_app


__all__ = ["create_app"]


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


if __name__ == "__main__":
    app = create_app()

    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(
        app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1
    )
    settings = app.config["SETTINGS"]
    debug_enabled = _env_flag("APP_DEBUG", default=False)
    use_reloader = _env_flag("APP_USE_RELOADER", default=False)
    app.run(
        host=settings.app_host,
        port=settings.app_port,
        debug=debug_enabled,
        use_reloader=use_reloader,
    )
