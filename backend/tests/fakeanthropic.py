"""Anthropic faked at the adapter's HTTP edge, the way TMDB and Resend are.

The seam's own tests script :mod:`fakellm` and never reach an adapter at all; this one
exists for the adapter itself - that the request carries the model, the schema and the
right headers, that a 429 is waited out, and that a batch is created, polled and
fetched. No automated test calls a real provider (testing.md); the real client gets at
most a tiny manual smoke check.
"""

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

BASE_URL = "https://api.anthropic.com"


@dataclass(frozen=True)
class Request:
    """One request, as the fake received it."""

    method: str
    path: str
    body: dict[str, Any] | None


@dataclass
class FakeAnthropic:
    """A canned Anthropic. One answer, however it is asked for."""

    answer: str = '{"paragraphs": ["You like slow films."]}'
    input_tokens: int = 1200
    output_tokens: int = 180
    stop_reason: str = "end_turn"
    requests: list[Request] = field(default_factory=list)

    throttled: int = 0
    """Requests answered 429 before the fake starts answering properly."""
    down: bool = False
    """When set, every request answers 500."""
    polls_before_ending: int = 0
    """Batch status checks that report ``in_progress`` before one reports ``ended``."""
    batch_result_type: str = "succeeded"
    no_text: bool = False
    """When set, the message comes back with no text block: what a refusal looks like."""

    _polled: int = 0
    _batch_id: str = "msgbatch_test"

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    # --- Reading back what happened ---

    def calls(self, method: str, path: str) -> list[Request]:
        return [
            request
            for request in self.requests
            if request.method == method and request.path == path
        ]

    @property
    def cancelled(self) -> bool:
        return any(request.path.endswith("/cancel") for request in self.requests)

    # --- Answering ---

    def _handle(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith(BASE_URL), request.url
        assert request.headers["anthropic-version"], "the API version header is required"
        assert request.headers["x-api-key"], "the credential header is required"
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        self.requests.append(Request(method=request.method, path=path, body=body))

        if self.down:
            return httpx.Response(500, json={"error": {"message": "overloaded"}})
        if self.throttled > 0:
            self.throttled -= 1
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": {}})

        if path == "/v1/messages":
            return httpx.Response(200, json=self._message())
        if path == "/v1/messages/batches" and request.method == "POST":
            return httpx.Response(
                200, json={"id": self._batch_id, "processing_status": "in_progress"}
            )
        if path.endswith("/cancel"):
            return httpx.Response(200, json=self._batch("canceling"))
        if path.endswith("/results"):
            return httpx.Response(200, text=self._results())
        if path.startswith("/v1/messages/batches/"):
            return httpx.Response(200, json=self._batch(self._status()))
        raise AssertionError(f"the fake was asked for {path}, which nothing should ask for")

    def _batch(self, status: str) -> dict[str, Any]:
        return {"id": self._batch_id, "processing_status": status}

    def _status(self) -> str:
        if self._polled < self.polls_before_ending:
            self._polled += 1
            return "in_progress"
        return "ended"

    def _results(self) -> str:
        result: dict[str, Any] = {"type": self.batch_result_type}
        if self.batch_result_type == "succeeded":
            result["message"] = self._message()
        return json.dumps({"custom_id": "anchor", "result": result})

    def _message(self) -> dict[str, Any]:
        return {
            "id": "msg_test",
            "content": [] if self.no_text else [{"type": "text", "text": self.answer}],
            "stop_reason": self.stop_reason,
            "usage": {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens},
        }
