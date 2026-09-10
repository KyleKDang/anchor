"""Anthropic faked at the adapter's HTTP edge, the way TMDB and Resend are.

The seam's own tests script :mod:`fakellm` and never reach an adapter at all; this one
exists for the adapter itself - that the request carries the model, the schema and the
right headers, that a 429 is waited out, and that a batch is created, polled and
fetched. No automated test calls a real provider (testing.md); the real client gets at
most a tiny manual smoke check.

It also refuses a structured-output schema the real API would refuse (#116), by the rules
in :mod:`schemacontract`. Asserting that a request *carries* a schema is not the same as
asserting the schema is one the provider accepts, and the gap between the two hid a
malformed ``PARAGRAPHS_SCHEMA`` behind a green suite for as long as the schema existed.
"""

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from schemacontract import assert_schema_is_accepted

BASE_URL = "https://api.anthropic.com"


@dataclass
class Rejection:
    """One 4xx as Anthropic renders it: a status, what it objected to, and its id.

    A 4xx is the provider explaining that our request is wrong, and the explanation is in
    the body rather than in the status (#117), so the fake carries one.
    """

    status: int = 400
    message: str = "output_config.format.schema: 'maxItems' is not supported"
    request_id: str | None = "req_011CerciLqADx3pZj9MEs8rq"
    in_header: bool = False
    """Where the id rides. Anthropic stamps the header on every response, body or not."""
    body: str | None = None
    """Raw text answered instead of the documented shape: what a gateway in front of the
    provider sends when it turns a request away before the provider ever sees it."""
    after: int = 0
    """Requests answered normally before the rejection starts."""


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
    rejection: Rejection | None = None
    """When set, requests past its ``after`` are turned away rather than answered."""
    batch_error: dict[str, Any] | None = None
    """The ``error`` an ``errored`` batch result row carries, when it carries one."""
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
        self._check_schemas(body)

        if self.down:
            return httpx.Response(500, json={"error": {"message": "overloaded"}})
        if self.rejection is not None:
            if self.rejection.after > 0:
                self.rejection.after -= 1
            else:
                return self._rejected(self.rejection)
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

    def _check_schemas(self, body: dict[str, Any] | None) -> None:
        """Every schema in the request, whether it came alone or inside a batch.

        Checked before the throttle and the outage, so a bad schema is caught on the
        retry path too - the provider validates the request whatever else it is doing.
        """
        if not isinstance(body, dict):
            return
        batched = [one["params"] for one in body.get("requests") or () if "params" in one]
        for message in (*batched, body):
            schema = ((message.get("output_config") or {}).get("format") or {}).get("schema")
            if schema is not None:
                assert_schema_is_accepted(schema)

    def _rejected(self, rejection: Rejection) -> httpx.Response:
        """One turned-away request, as Anthropic or a gateway in front of it answers."""
        headers = {}
        if rejection.request_id is not None and rejection.in_header:
            headers["request-id"] = rejection.request_id
        if rejection.body is not None:
            return httpx.Response(rejection.status, text=rejection.body, headers=headers)
        body: dict[str, Any] = {
            "type": "error",
            "error": {"type": "invalid_request_error", "message": rejection.message},
        }
        if rejection.request_id is not None and not rejection.in_header:
            body["request_id"] = rejection.request_id
        return httpx.Response(rejection.status, json=body, headers=headers)

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
        elif self.batch_error is not None:
            result["error"] = self.batch_error
        return json.dumps({"custom_id": "anchor", "result": result})

    def _message(self) -> dict[str, Any]:
        return {
            "id": "msg_test",
            "content": [] if self.no_text else [{"type": "text", "text": self.answer}],
            "stop_reason": self.stop_reason,
            "usage": {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens},
        }
