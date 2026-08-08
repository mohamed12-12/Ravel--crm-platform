from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _reset_wsgi_module() -> None:
    for module_name in list(sys.modules):
        if module_name == "services.ai_agent.wsgi":
            sys.modules.pop(module_name, None)


class WsgiProxyFixTests(unittest.TestCase):
    """services/ai_agent/wsgi.py is gunicorn's real production entrypoint
    (deploy/pm2/ecosystem.config.js); demo_web/app.py's __main__ block
    applies ProxyFix for local runs, but production never touches that
    code path at all. Confirmed live (2026-08-08): without ProxyFix here,
    url_for('static', ...) had no way to know this app is mounted at
    nginx's /rahma-agent/ prefix, so the chat widget's CSS/JS all 404'd
    and it rendered completely unstyled.
    """

    def setUp(self) -> None:
        _reset_wsgi_module()
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))

    def tearDown(self) -> None:
        _reset_wsgi_module()

    def test_static_asset_urls_respect_the_rahma_agent_prefix(self) -> None:
        import services.ai_agent.wsgi as wsgi_module

        client = wsgi_module.app.test_client()
        html = client.get(
            "/",
            headers={"X-Forwarded-Prefix": "/rahma-agent", "X-Forwarded-Proto": "https"},
        ).get_data(as_text=True)
        self.assertIn("/rahma-agent/static/styles.css", html)
        self.assertIn("/rahma-agent/static/app.js", html)

    def test_static_asset_urls_are_unprefixed_without_a_proxy_header(self) -> None:
        """Confirms ProxyFix only adds the prefix when nginx actually sends
        one -- local/direct access (no reverse proxy) must stay unaffected.
        """
        import services.ai_agent.wsgi as wsgi_module

        client = wsgi_module.app.test_client()
        html = client.get("/").get_data(as_text=True)
        self.assertIn('href="/static/styles.css', html)


if __name__ == "__main__":
    unittest.main()
