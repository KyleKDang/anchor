"""Anthropic faked at the adapter's HTTP edge, the way TMDB and Resend are.

The seam's own tests script :mod:`fakellm` and never reach an adapter at all; this one
exists for the adapter itself - that the request carries the model, the schema and the
right headers, that a 429 is waited out, and that a batch is created, polled and
fetched. No automated test calls a real provider (testing.md); the real client gets at
most a tiny manual smoke check.

It also refuses a structured-output schema the real API would refuse (#116). Asserting
that a request *carries* a schema is not the same as asserting the schema is one the
provider accepts, and the gap between the two hid a malformed ``PARAGRAPHS_SCHEMA``
behind a green suite for as long as the schema existed.
"""

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

BASE_URL = "https://api.anthropic.com"

# --- What structured outputs accepts ---
#
# Transcribed from the structured-outputs documentation's supported-keyword tables. The
# fake enforces it because the provider does: a schema carrying anything outside this set
# comes back 400, and a 400 here is a ``Skipped`` that reads as success (#116).

_UNIVERSAL_KEYWORDS = frozenset(
    # The structural keywords are universal rather than object-only on purpose: a node
    # that is only a ``{"$ref": ...}`` or an ``anyOf`` branch carries no ``type`` at all,
    # and filing them under objects would refuse a schema the provider accepts.
    {"type", "enum", "const", "default", "description", "title", "anyOf", "allOf", "$ref", "$defs"}
)
_OBJECT_KEYWORDS = frozenset({"properties", "required", "additionalProperties"})
_ARRAY_KEYWORDS = frozenset({"items", "minItems"})
_STRING_KEYWORDS = frozenset({"format"})

_SUPPORTED_STRING_FORMATS = frozenset(
    {"date-time", "time", "date", "duration", "email", "hostname", "uri", "ipv4", "ipv6", "uuid"}
)


class RejectedSchema(AssertionError):
    """A schema the real API would answer 400 for.

    Deliberately an ``AssertionError`` rather than a 400 response. A 400 is what the
    provider sends, but the adapter turns one into ``ProviderUnavailable``, which is a
    ``Skipped``, which every caller treats as a success with nothing to write - exactly
    the silence this class exists to break. A test that sends a bad schema should fail,
    loudly, naming the key.
    """


def assert_schema_is_accepted(schema: Any, *, where: str = "schema") -> None:
    """Refuse what structured outputs refuses, so CI catches it instead of production."""
    if not isinstance(schema, dict):
        raise RejectedSchema(
            f"{where}: a JSON schema must be an object, not {type(schema).__name__}"
        )

    kind = schema.get("type")
    allowed = set(_UNIVERSAL_KEYWORDS)
    if kind == "object" or "properties" in schema:
        allowed |= _OBJECT_KEYWORDS
    if kind == "array":
        allowed |= _ARRAY_KEYWORDS
    if kind == "string":
        allowed |= _STRING_KEYWORDS

    for key in schema:
        if key not in allowed:
            raise RejectedSchema(
                f"{where}: property {key!r} is not supported for {kind!r} type - "
                "structured outputs answers 400 for it"
            )

    if kind == "object" or "properties" in schema:
        if schema.get("additionalProperties") is not False:
            raise RejectedSchema(
                f"{where}: 'additionalProperties' must be False on an object, "
                f"not {schema.get('additionalProperties')!r}"
            )
        for name, child in (schema.get("properties") or {}).items():
            assert_schema_is_accepted(child, where=f"{where}.{name}")

    if kind == "array":
        # The one bounded exception in the whole set: 0 and 1 are accepted, nothing above.
        if "minItems" in schema and schema["minItems"] not in (0, 1):
            raise RejectedSchema(
                f"{where}: 'minItems' supports only 0 and 1, not {schema['minItems']!r}"
            )
        if "items" in schema:
            assert_schema_is_accepted(schema["items"], where=f"{where}.items")

    if kind == "string" and schema.get("format") not in (None, *_SUPPORTED_STRING_FORMATS):
        raise RejectedSchema(f"{where}: string format {schema['format']!r} is not supported")

    for value in schema.get("enum") or ():
        if not isinstance(value, (str, int, float, bool)) and value is not None:
            raise RejectedSchema(f"{where}: only scalars may appear in an enum, not {value!r}")

    for branch, name in ((schema.get("anyOf"), "anyOf"), (schema.get("allOf"), "allOf")):
        for index, child in enumerate(branch or ()):
            assert_schema_is_accepted(child, where=f"{where}.{name}[{index}]")

    for name, child in (schema.get("$defs") or {}).items():
        assert_schema_is_accepted(child, where=f"{where}.$defs.{name}")


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
        self._check_schemas(body)

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
