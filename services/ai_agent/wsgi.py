"""Production WSGI entrypoint for the AI agent service.

Used by gunicorn (see deploy/systemd/rahma-ai-agent.service):
    gunicorn --chdir <repo-root> services.ai_agent.wsgi:app ...

Deliberately imports create_app from services.ai_agent.ai_agent_app.server
directly rather than going through demo_web/app.py's
archive.legacy_demo_web fallback - production must not silently depend on
whether a gitignored archive/ directory happens to be present on disk.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from werkzeug.middleware.proxy_fix import ProxyFix

from services.ai_agent.ai_agent_app.server import create_app

try:
    app = create_app()
except Exception as exc:
    print(f"CRITICAL: Failed to boot AI agent Flask application: {exc}", file=sys.stderr)
    raise

if app is not None:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

