from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os


class AuthMockHandler(BaseHTTPRequestHandler):
    def _write_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in {"/", "/health"}:
            self._write_json(200, {"status": "ok"})
            return
        self._write_json(404, {"error": "not_found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length:
            self.rfile.read(length)
        if self.path == "/post":
            self._write_json(200, {"access_token": "mock-jwt-token-abcd", "token_type": "Bearer"})
            return
        self._write_json(404, {"error": "not_found"})

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))


if __name__ == "__main__":
    host = os.environ.get("A2A_AUTH_MOCK_HOST", "127.0.0.1")
    port = int(os.environ.get("A2A_AUTH_MOCK_PORT", "8080"))
    server = ThreadingHTTPServer((host, port), AuthMockHandler)
    print(f"Auth mock listening on http://{host}:{port}")
    server.serve_forever()
