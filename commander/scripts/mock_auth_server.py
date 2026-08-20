from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class AuthMockHandler(BaseHTTPRequestHandler):
    def _reply(self):
        body = b'{"ok":true,"token":"mock"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._reply()

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self._reply()

    def log_message(self, *_args):
        return


def main():
    parser = argparse.ArgumentParser(description="Tiny auth mock for A2A demos")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), AuthMockHandler)
    print(f"auth mock listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
