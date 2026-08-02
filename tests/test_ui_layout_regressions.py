from __future__ import annotations

import os
import shutil
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API_ROOT = ROOT / "apps" / "api"
DEMO_CSS = ROOT / "services" / "ai_agent" / "ai_agent_app" / "web" / "static" / "styles.css"
HANDOFF_TEMPLATE = API_ROOT / "app" / "templates" / "admin" / "handoffs.html"


def _reset_app_modules() -> None:
    for module_name in list(sys.modules):
        if module_name == "app" or module_name.startswith("app."):
            sys.modules.pop(module_name, None)


class UiLayoutRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = ROOT / ".tmp-test-workdirs" / f"ui-layout-{uuid.uuid4().hex}"
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.tmpdir / "app.db"
        self.original_env = dict(os.environ)
        os.environ["DATABASE_URL"] = f"sqlite:///{self.db_path.resolve().as_posix()}"
        os.environ["RAHMA_SYSTEM_DB_PATH"] = str(self.db_path)
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        if str(API_ROOT) not in sys.path:
            sys.path.insert(0, str(API_ROOT))

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.original_env)
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        _reset_app_modules()

    def test_crm_primary_pages_render_after_style_pass(self) -> None:
        _reset_app_modules()
        from app import create_app
        from app.extensions import db

        app = create_app("development")
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        with app.app_context():
            db.drop_all()
            db.create_all()

        with app.test_client() as client:
            for path in (
                "/admin/dashboard",
                "/admin/handoffs/",
                "/leads/",
                "/bookings/",
                "/travelers/",
                "/trips/",
                "/interactions/",
            ):
                with self.subTest(path=path):
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200)

    def test_demo_page_renders_after_compact_layout_pass(self) -> None:
        from services.ai_agent.ai_agent_app.server import create_app

        app = create_app()
        with app.test_client() as client:
            response = client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Agent Interaction Studio", response.data)

    def test_demo_css_keeps_chat_and_inspector_scroll_contained(self) -> None:
        css = DEMO_CSS.read_text(encoding="utf-8")

        self.assertIn(".workspace {\n  grid-template-columns: minmax(0, 1fr) 320px;", css)
        self.assertIn("height: 100vh;", css)
        self.assertIn("overflow: hidden;", css)
        self.assertIn(".chat-log {\n  flex: 1 1 auto;", css)
        self.assertIn("overflow-y: auto;", css)
        self.assertIn(".inspector {\n  height: calc(100vh - 2rem);", css)
        self.assertIn("max-height: calc(100vh - 2rem);", css)
        self.assertIn("overflow-wrap: anywhere;", css)

    def test_handoff_css_uses_compact_columns_and_expected_colors(self) -> None:
        template = HANDOFF_TEMPLATE.read_text(encoding="utf-8")

        self.assertIn(".handoff-dashboard {\n        min-height: auto;", template)
        self.assertIn("background: transparent;", template)
        self.assertIn(".kanban-column {\n        min-height: auto;", template)
        self.assertIn(".column-body {\n        min-height: 0;", template)
        self.assertIn(".empty-state {\n        padding: 1rem;", template)
        self.assertIn(".filter-pill.active-critical {\n        background: rgba(239, 68, 68, 0.14);", template)
        self.assertIn(".filter-pill.active-high {\n        background: rgba(0, 208, 146, 0.12);", template)
        self.assertIn(".filter-pill.active-medium {\n        background: rgba(234, 179, 8, 0.14);", template)
        self.assertIn(".card-glow-high {\n        border-left-color: var(--accent);", template)


if __name__ == "__main__":
    unittest.main()
