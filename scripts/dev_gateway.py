from __future__ import annotations

import argparse
import html
import http.client
import socketserver
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit


HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def build_landing(bind_port: int, routes: dict[str, tuple[str, int]]) -> bytes:
    labels = {
        "/p3000": "Port 3000",
        "/p5001": "Port 5001",
        "/crm": "CRM alias",
        "/demo": "Demo alias",
    }
    route_links = "\n".join(
        f'<a href="{html.escape(prefix)}/"><strong>{html.escape(labels.get(prefix, prefix))}</strong>'
        f"<span>http://127.0.0.1:{target_port}</span></a>"
        for prefix, (_, target_port) in routes.items()
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Rahma Dev Gateway</title>
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: Arial, sans-serif;
      background: #f7f4ee;
      color: #1f2933;
      display: grid;
      place-items: center;
    }}
    main {{
      width: min(680px, calc(100vw - 32px));
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 32px;
      letter-spacing: 0;
    }}
    p {{
      margin: 0 0 22px;
      color: #52616b;
    }}
    .links {{
      display: grid;
      gap: 12px;
    }}
    a {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      padding: 16px;
      border: 1px solid #d6d0c4;
      border-radius: 8px;
      color: inherit;
      text-decoration: none;
      background: #ffffff;
    }}
    a:hover {{
      border-color: #8b6f47;
    }}
    span {{
      color: #697985;
      overflow-wrap: anywhere;
    }}
    code {{
      background: #ebe4d8;
      padding: 2px 5px;
      border-radius: 4px;
    }}
  </style>
</head>
<body>
  <main>
    <h1>Rahma Dev Gateway</h1>
    <p>This single public ngrok domain routes normal CRM paths to port 3000, and demo paths to port 5001. Gateway port: <code>{bind_port}</code>.</p>
    <div class="links">
      {route_links}
    </div>
  </main>
</body>
</html>""".encode("utf-8")


class GatewayHandler(BaseHTTPRequestHandler):
    routes: dict[str, tuple[str, int]] = {}
    bind_port: int = 8080

    def do_GET(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def do_PUT(self) -> None:
        self._handle()

    def do_PATCH(self) -> None:
        self._handle()

    def do_DELETE(self) -> None:
        self._handle()

    def do_OPTIONS(self) -> None:
        self._handle()

    def _handle(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path in {"/_gateway", "/_gateway/"}:
            body = build_landing(self.bind_port, self.routes)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        route = self._match_route(parsed.path)
        if route is None:
            route = ("", self.routes["/p3000"][1])

        prefix, target_port = route
        target_path = parsed.path[len(prefix):] or "/"
        if not target_path.startswith("/"):
            target_path = f"/{target_path}"
        if parsed.query:
            target_path = f"{target_path}?{parsed.query}"

        self._proxy(prefix=prefix, target_port=target_port, target_path=target_path)

    def _match_route(self, path: str) -> tuple[str, int] | None:
        for prefix, (_, port) in sorted(self.routes.items(), key=lambda item: len(item[0]), reverse=True):
            if path == prefix or path.startswith(f"{prefix}/"):
                return prefix, port
        return None

    def _proxy(self, prefix: str, target_port: int, target_path: str) -> None:
        body_length = int(self.headers.get("Content-Length", "0") or "0")
        request_body = self.rfile.read(body_length) if body_length else None

        upstream_headers = {}
        for key, value in self.headers.items():
            lowered = key.lower()
            if lowered in HOP_BY_HOP_HEADERS or lowered in {"host", "content-length", "accept-encoding"}:
                continue
            upstream_headers[key] = value
        upstream_headers["Host"] = f"127.0.0.1:{target_port}"
        upstream_headers["X-Forwarded-Host"] = self.headers.get("Host", "")
        upstream_headers["X-Forwarded-Prefix"] = prefix
        upstream_headers["X-Forwarded-Proto"] = "https" if self.headers.get("X-Forwarded-Proto") == "https" else "http"
        if request_body is not None:
            upstream_headers["Content-Length"] = str(len(request_body))

        conn = http.client.HTTPConnection("127.0.0.1", target_port, timeout=30)
        try:
            conn.request(self.command, target_path, body=request_body, headers=upstream_headers)
            response = conn.getresponse()
            response_body = response.read()
            headers = response.getheaders()
        except OSError as exc:
            self.send_error(502, f"Could not reach local port {target_port}: {exc}")
            return
        finally:
            conn.close()

        content_type = self._header_value(headers, "Content-Type")
        response_body = self._rewrite_body(response_body, content_type, prefix)

        self.send_response(response.status, response.reason)
        for key, value in headers:
            lowered = key.lower()
            if lowered in HOP_BY_HOP_HEADERS or lowered in {"content-length", "content-encoding"}:
                continue
            if lowered == "location":
                value = self._rewrite_location(value, prefix)
            elif lowered == "set-cookie":
                value = value.replace("Path=/", f"Path={prefix}/")
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    @staticmethod
    def _header_value(headers: list[tuple[str, str]], name: str) -> str:
        for key, value in headers:
            if key.lower() == name.lower():
                return value
        return ""

    @staticmethod
    def _rewrite_location(value: str, prefix: str) -> str:
        if value.startswith("/"):
            return f"{prefix}{value}"
        return value

    @staticmethod
    def _rewrite_body(body: bytes, content_type: str, prefix: str) -> bytes:
        if not any(kind in content_type for kind in ("text/html", "text/css", "javascript", "application/json")):
            return body
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return body

        replacements = {
            'href="/': f'href="{prefix}/',
            "href='/": f"href='{prefix}/",
            'src="/': f'src="{prefix}/',
            "src='/": f"src='{prefix}/",
            'action="/': f'action="{prefix}/',
            "action='/": f"action='{prefix}/",
            'fetch("/': f'fetch("{prefix}/',
            "fetch('/": f"fetch('{prefix}/",
            "fetch(`/": f"fetch(`{prefix}/",
            'api("/': f'api("{prefix}/',
            "api('/": f"api('{prefix}/",
            "api(`/": f"api(`{prefix}/",
            'url("/': f'url("{prefix}/',
            "url('/": f"url('{prefix}/",
            "url(/": f"url({prefix}/",
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        return text.encode("utf-8")


class ThreadingHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    parser = argparse.ArgumentParser(description="Rahma local path gateway for one ngrok domain.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--p3000-target", type=int, default=3000)
    parser.add_argument("--p5001-target", type=int, default=5001)
    args = parser.parse_args()

    GatewayHandler.bind_port = args.port
    GatewayHandler.routes = {
        "/p3000": ("127.0.0.1", args.p3000_target),
        "/p5001": ("127.0.0.1", args.p5001_target),
        "/crm": ("127.0.0.1", args.p3000_target),
        "/demo": ("127.0.0.1", args.p5001_target),
    }

    with ThreadingHTTPServer((args.host, args.port), GatewayHandler) as httpd:
        print(f"Rahma dev gateway listening on http://{args.host}:{args.port}", flush=True)
        httpd.serve_forever()


if __name__ == "__main__":
    main()
