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
        # apps/api/app/__init__.py defaults CRM_AUTH_ENABLED to "true" when the
        # env var is absent; these page-render checks don't exercise auth, so
        # set it explicitly rather than depending on another test file having
        # left the ambient environment at "false".
        os.environ["CRM_AUTH_ENABLED"] = "false"
        os.environ["DATABASE_URL"] = f"sqlite:///{self.db_path.resolve().as_posix()}"
        os.environ["RAHMA_SYSTEM_DB_PATH"] = str(self.db_path)
        os.environ["SHEET_BACKEND"] = "excel"
        os.environ["EXCEL_SOURCE_WORKBOOK"] = str(ROOT / "tests" / "fixtures" / "operational_source_workbook.xlsx")
        os.environ["EXCEL_RUNTIME_WORKBOOK"] = str(self.tmpdir / "runtime.xlsx")
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
        # "Agent Interaction Studio" was removed by the live-chat UI simplification
        # (internal demo panels dropped in favor of a single compact chat layout);
        # assert on the compact layout's own markers instead.
        self.assertIn(b'class="chat-area glass-panel"', response.data)
        self.assertIn(b'id="message-input"', response.data)

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

    def test_leads_and_handoffs_action_urls_respect_a_reverse_proxy_path_prefix(self) -> None:
        """leads/detail.html's Advance Stage button and admin/handoffs.html's
        three update-handoff fetch() calls used to build their request URL
        as a hardcoded '/leads/<id>/advance' or '/admin/handoffs/<id>'
        string -- the browser resolves that against the origin root, not
        the current page's path, so behind a prefixed reverse proxy
        (confirmed live: nginx's /rahma-crm/) it silently lands on the
        wrong route, the same bug class already fixed for the traveler
        delete buttons and the handoff-badge poller. This confirms both
        surfaces now emit prefix-aware URLs end-to-end.
        """
        _reset_app_modules()
        from app import create_app
        from app.extensions import db
        from app.models.lead import Lead

        app = create_app("development")
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        with app.app_context():
            db.drop_all()
            db.create_all()
            db.session.add(Lead(
                lead_id="L-PREFIX-1",
                customer_name="Prefix Advance Lead",
                lead_stage="New Lead",
                priority="Medium",
            ))
            db.session.commit()

        headers = {"X-Forwarded-Prefix": "/rahma-crm"}
        with app.test_client() as client:
            lead_html = client.get("/leads/L-PREFIX-1", headers=headers).get_data(as_text=True)
            self.assertIn('fetch("/rahma-crm/leads/L-PREFIX-1/advance"', lead_html)

            handoffs_html = client.get("/admin/handoffs/", headers=headers).get_data(as_text=True)
            self.assertIn(
                'const HANDOFF_UPDATE_URL_TEMPLATE = "/rahma-crm/admin/handoffs/__HANDOFF_ID__";',
                handoffs_html,
            )
            self.assertNotIn("fetch(`/admin/handoffs/${handoffId}`", handoffs_html)


if __name__ == "__main__":
    unittest.main()
