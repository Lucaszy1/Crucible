"""Toy web service for validation exercises.

Serves a status page plus one deliberately exposed admin endpoint. The
blue team closes it by patching, which drops a flag file this app checks
on every admin request. Stdlib only, no real data anywhere.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 8000
PATCH_FLAG = "/tmp/patched"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # keep stdout tidy; request lines below use our own format

    def _send(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _log_request(self, code):
        print(
            f"request from={self.client_address[0]} "
            f"method={self.command} path={self.path} status={code}",
            flush=True,
        )

    def do_GET(self):
        if self.path == "/":
            self._send(200, {"service": "webapp", "state": "up"})
            self._log_request(200)
        elif self.path == "/admin":
            if os.path.exists(PATCH_FLAG):
                self._send(404, {"error": "not found"})
                self._log_request(404)
            else:
                self._send(
                    200,
                    {
                        "admin": True,
                        "note": "toy misconfiguration: admin page left open",
                        "hint": "the internal host 'app' runs reports as 'svc-report'",
                    },
                )
                self._log_request(200)
        else:
            self._send(404, {"error": "not found"})
            self._log_request(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        self.rfile.read(min(length, 8192))
        self._send(404, {"error": "not found"})
        self._log_request(404)


if __name__ == "__main__":
    print(f"webapp listening on {PORT}", flush=True)
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
