"""Tiny local HTTP server that serves app/fixtures/v1.html, v2.html, or v3.html,
depending on app/runtime/ACTIVE_VERSION. This simulates a deploy that changes the DOM
out from under your tests.
"""
import http.server
import os
import threading

from framework.constants import ACTIVE_VERSION_PATH, APP_PORT, DEFAULT_FIXTURE_VERSION, FIXTURES_DIR


def set_active_version(version: str):
    with open(ACTIVE_VERSION_PATH, "w") as f:
        f.write(version)


def get_active_version() -> str:
    if not os.path.exists(ACTIVE_VERSION_PATH):
        return DEFAULT_FIXTURE_VERSION
    return open(ACTIVE_VERSION_PATH).read().strip() or DEFAULT_FIXTURE_VERSION


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            version = get_active_version()
            path = os.path.join(FIXTURES_DIR, f"{version}.html")
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            with open(path, "rb") as f:
                self.wfile.write(f.read())
            return
        return super().do_GET()

    def log_message(self, format, *args):
        pass  # keep test output clean


def start_server(port: int = APP_PORT):
    set_active_version(DEFAULT_FIXTURE_VERSION)
    os.chdir(FIXTURES_DIR)
    httpd = http.server.HTTPServer(("localhost", port), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd
