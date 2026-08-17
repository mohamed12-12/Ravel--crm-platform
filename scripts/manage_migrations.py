#!/usr/bin/env python
"""Run Alembic migrations without fighting the path setup.

`flask db ...` works here only if two things are true, and neither is obvious:
the interpreter must be the repo-root venv (not `apps/api/venv`, which does
not exist), and PYTHONPATH must include the repo root so the `services`
package is importable from inside `apps/api`. Miss either and you get
"No such file or directory" or "No module named 'services'", neither of which
points at the real problem.

This script sets both up itself, so from the repository root:

    venv/bin/python scripts/manage_migrations.py current
    venv/bin/python scripts/manage_migrations.py upgrade
    venv/bin/python scripts/manage_migrations.py heads
    venv/bin/python scripts/manage_migrations.py stamp <revision>

`current` is read-only and is the right first call on any database you are
not certain about. A database with no revision stamped at all will print
nothing for `current` -- and running `upgrade` against it replays the chain
from the initial migration, which fails on the first table that already
exists. Stamp it at the revision it actually matches before upgrading.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "apps" / "api"
for candidate in (str(REPO_ROOT), str(API_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("current", "heads", "upgrade", "downgrade", "stamp"),
        help="current and heads are read-only",
    )
    parser.add_argument("revision", nargs="?", default="", help="required for stamp/downgrade")
    args = parser.parse_args()

    if args.command in {"stamp", "downgrade"} and not args.revision:
        print(f"'{args.command}' needs a revision, e.g. "
              f"scripts/manage_migrations.py {args.command} d4e8f2a91c37")
        return 1

    from app import create_app
    from flask_migrate import current, downgrade, heads, stamp, upgrade

    app = create_app()
    with app.app_context():
        print(f"Database: {app.config.get('SQLALCHEMY_DATABASE_URI')}\n")
        if args.command == "current":
            current(verbose=True)
            print("\n(If nothing is listed above, this database has no Alembic revision "
                  "stamped. Do NOT run upgrade -- stamp it first.)")
        elif args.command == "heads":
            heads(verbose=True)
        elif args.command == "upgrade":
            upgrade()
            print("\nUpgrade complete.")
        elif args.command == "downgrade":
            downgrade(revision=args.revision)
            print(f"\nDowngraded to {args.revision}.")
        elif args.command == "stamp":
            stamp(revision=args.revision)
            print(f"\nStamped at {args.revision} without running any migration.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
