"""The legitimate agent's operator: publishes its public keys for Web Bot Auth.

Serves /.well-known/http-message-signatures-directory from /keys/jwks.json, with the
media type the draft asks for. Standard library only: nothing else runs here.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

JWKS = Path("/keys/jwks.json")
PATH = "/.well-known/http-message-signatures-directory"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        if self.path == "/saude":
            self._send(200, "application/json", b'{"ok": true}')
        elif self.path == PATH:
            body = JWKS.read_bytes() if JWKS.exists() else json.dumps({"keys": []}).encode()
            self._send(200, "application/http-message-signatures-directory+json", body)
        else:
            self._send(404, "text/plain", b"not found")

    def _send(self, status: int, ctype: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # quiet
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8090), Handler).serve_forever()  # noqa: S104 - lab network
