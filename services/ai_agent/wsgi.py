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

app = create_app()
# demo_web/app.py's __main__ block applies this same ProxyFix for local
# runs, but production goes through this module directly and never hits
# that code path -- confirmed live (2026-08-08): without it, url_for()
# has no way to know this app is mounted at nginx's /rahma-agent/ prefix,
# so every static asset URL resolved against the domain root instead and
# 404'd, serving the chat widget with no CSS/JS applied at all.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
