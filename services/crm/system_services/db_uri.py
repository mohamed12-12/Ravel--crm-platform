# services/crm/system_services/db_uri.py
"""Rendering a database URL without handing out its password.

Production runs against a managed Postgres whose connection string carries
real credentials. Anything that prints, logs, or returns that string over
HTTP -- diagnostics endpoints, operational scripts, migration reports --
publishes the password to terminal scrollback, proxy logs, browser caches and
screenshots. SQLAlchemy's own logging masks it; hand-written output has to ask.

Lives here rather than in the Flask app so the `services` package, the scripts
and the routes can all share one definition.
"""
from __future__ import annotations


def safe_database_uri(uri, fallback: str = "(unset)") -> str:
    """The connection string with the password replaced by ***."""
    text = str(uri or "").strip()
    if not text:
        return fallback
    try:
        from sqlalchemy.engine import make_url

        return make_url(text).render_as_string(hide_password=True)
    except Exception:
        # An unparseable value could be anything, including a bare password.
        # Saying nothing is better than guessing it is safe to show.
        return "(unreadable database URL)"
