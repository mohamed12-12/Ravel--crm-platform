from __future__ import annotations

import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.ai_agent.ai_agent_app.server import create_app as create_redesigned_app


def create_app(
    source_workbook: Path | None = None,
    runtime_workbook: Path | None = None,
):
    return create_redesigned_app(
        source_workbook=source_workbook,
        runtime_workbook=runtime_workbook,
    )

if __name__ == "__main__":
    app = create_app()
    settings = app.config["SETTINGS"]
    app.run(host=settings.app_host, port=settings.app_port, debug=(settings.app_env == "development"))
