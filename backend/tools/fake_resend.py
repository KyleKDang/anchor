"""A stand-in for Resend's HTTP API, for the composed dev stack.

The web process posts to it exactly as it would to Resend; nothing leaves the box.
``POST /emails`` stores a message and answers like Resend, ``GET /emails`` lists the
stored messages (newest last), ``DELETE /emails`` clears them. Standard library only.
"""

import json
import os
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

emails: list[dict[str, Any]] = []


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.path != "/emails":
            return self._json(404, {"message": "not found"})
        length = int(self.headers.get("Content-Length", "0"))
        message = json.loads(self.rfile.read(length))
        message["id"] = str(uuid.uuid4())
        message["created_at"] = datetime.now(UTC).isoformat()
        emails.append(message)
        print(f"mail to {message.get('to')}: {message.get('subject')}", flush=True)
        self._json(200, {"id": message["id"]})

    def do_GET(self) -> None:
        if self.path != "/emails":
            return self._json(404, {"message": "not found"})
        self._json(200, emails)

    def do_DELETE(self) -> None:
        if self.path != "/emails":
            return self._json(404, {"message": "not found"})
        emails.clear()
        self._json(204, None)

    def _json(self, status: int, body: Any) -> None:
        payload = b"" if body is None else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8025"))
    print(f"fake Resend listening on :{port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
