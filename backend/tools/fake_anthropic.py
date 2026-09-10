"""A stand-in for Anthropic's Messages API, for the composed dev stack.

The adapter calls it exactly as it would call the real API - one ``POST /v1/messages``
for an immediate call, and create, poll, fetch and cancel under ``/v1/messages/batches``
for a batched one - and it answers every structured-output shape Anchor asks for, read
off the request's own schema. Standard library only, like the TMDB and Resend fakes.

Without it the dev stack has no credential, so every LLM operation is skipped by design
(ADR 0004) and the stack has no prose profile and an empty discovery shelf (#109). With it
the demo build (``python -m anchor.demobuild``) reaches the surfaces the browser smoke
suite walks. Nothing here is clever: the prose is a template over the evidence it was
shown, a ranking keeps the list it was given, and a tagging answers a stable few of the
vocabulary offered, so the pipeline's own selection is what shapes what a screen shows.
"""

import hashlib
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

CANDIDATE = re.compile(r"^- (\d+): (.+?)(?: \(\d{4}\))?(?: \|.*)?$", re.MULTILINE)
"""One offered film in the rerank prompt: ``- <tmdb id>: <title> (<year>) | <genres>``."""

BULLET = re.compile(r"^- (.+)$", re.MULTILINE)

BATCHES: dict[str, dict[str, Any]] = {}
"""Every batch created, by id, holding the message it will answer with."""


def answer(body: dict[str, Any]) -> str:
    """The JSON text for one request, by the answer shape its schema requires."""
    schema = body.get("output_config", {}).get("format", {}).get("schema", {})
    (shape,) = schema.get("required") or ["paragraphs"]
    user = "".join(str(message.get("content", "")) for message in body.get("messages", []))
    if shape == "ranked":
        return json.dumps({"ranked": _ranking(user)})
    if shape == "qualities":
        return json.dumps({"qualities": _qualities(user)})
    return json.dumps({"paragraphs": _prose(user)})


def _section(user: str, heading: str) -> list[str]:
    """The bullet lines under one heading of the evidence text, in order."""
    start = user.find(heading)
    if start == -1:
        return []
    rest = user[start + len(heading) :]
    end = rest.find("\n\n")
    return BULLET.findall(rest if end == -1 else rest[:end])


def _title(line: str) -> str:
    """A film's title off an evidence line, which may carry a year and a band after it."""
    return re.split(r" \(\d{4}\)| - | at | as ", line, maxsplit=1)[0].strip()


def _prose(user: str) -> list[str]:
    loved = [_title(line) for line in _section(user, "favourite first")[:3]]
    cold = [_title(line) for line in _section(user, "least favourite first")[:2]]
    first = "You go for films that take their time and trust you to keep up"
    if loved:
        first += f": {', '.join(loved[:-1])} and {loved[-1]}" if len(loved) > 1 else f": {loved[0]}"
    first += " are the shape of it - long looks, little said, and an ending that stays open."
    second = "What leaves you cold is a film that explains what it wants you to feel"
    if cold:
        verb = "sit" if len(cold) > 1 else "sits"
        second += f"; {' and '.join(cold)} {verb} at the bottom of your wall for exactly that."
    else:
        second += "."
    return [first, second]


def _ranking(user: str) -> list[dict[str, Any]]:
    fits = ("strong_fit", "plausible", "strong_fit", "plausible", "poor_fit")
    return [
        {
            "tmdb_id": int(tmdb_id),
            "fit": fits[index % len(fits)],
            "explanation": f"Because {title} moves the way the films at the top of your wall do.",
        }
        for index, (tmdb_id, title) in enumerate(CANDIDATE.findall(user))
    ]


def _qualities(user: str) -> list[str]:
    offered = _section(user, "The vocabulary:") or _section(user, "Their list:")
    if not offered:
        return []
    digest = hashlib.sha256(user.encode()).digest()
    picked = sorted({digest[n] % len(offered) for n in range(3)})
    return [offered[index] for index in picked]


def _message(text: str) -> dict[str, Any]:
    return {
        "id": "msg_dev",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 900, "output_tokens": 150},
    }


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        body = self._body()
        if self.path == "/v1/messages":
            return self._json(200, _message(answer(body)))
        if self.path == "/v1/messages/batches":
            batch_id = f"msgbatch_{len(BATCHES) + 1:06d}"
            rows = body.get("requests") or []
            BATCHES[batch_id] = {
                row.get("custom_id", ""): _message(answer(row.get("params") or {})) for row in rows
            }
            return self._json(200, {"id": batch_id, "processing_status": "in_progress"})
        if self.path.startswith("/v1/messages/batches/") and self.path.endswith("/cancel"):
            return self._json(
                200, {"id": self.path.split("/")[-2], "processing_status": "canceling"}
            )
        self._json(
            404, {"type": "error", "error": {"type": "not_found_error", "message": self.path}}
        )

    def do_GET(self) -> None:
        parts = self.path.strip("/").split("/")
        if len(parts) >= 4 and parts[:3] == ["v1", "messages", "batches"]:
            batch = BATCHES.get(parts[3])
            if batch is None:
                return self._json(
                    404,
                    {"type": "error", "error": {"type": "not_found_error", "message": parts[3]}},
                )
            if len(parts) == 4:
                return self._json(200, {"id": parts[3], "processing_status": "ended"})
            if parts[4] == "results":
                lines = [
                    json.dumps(
                        {
                            "custom_id": custom_id,
                            "result": {"type": "succeeded", "message": message},
                        }
                    )
                    for custom_id, message in batch.items()
                ]
                return self._text(200, "\n".join(lines) + "\n")
        self._json(
            404, {"type": "error", "error": {"type": "not_found_error", "message": self.path}}
        )

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            parsed = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _json(self, status: int, body: Any) -> None:
        self._text(status, json.dumps(body), "application/json")

    def _text(self, status: int, text: str, content_type: str = "application/x-ndjson") -> None:
        payload = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8040"))
    print(f"fake Anthropic listening on :{port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
