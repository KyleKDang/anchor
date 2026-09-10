"""The Anthropic adapter at its HTTP edge, the way the TMDB and Resend clients are tested.

The seam's own tests never reach an adapter; these are about the wire - that a request
carries the model, the answer schema and the credential, that throttling is waited out,
and that a batch is created, polled, fetched and abandoned properly. No automated test
calls a real provider (testing.md), so the transport is faked and the real client gets at
most a tiny manual smoke check.
"""

import json

import pytest

from anchor import llm
from anchor.settings import Settings
from fakeanthropic import FakeAnthropic, Rejection
from schemacontract import RejectedSchema, assert_schema_is_accepted

MODEL = llm.Model(id="claude-haiku-4-5", input_usd_per_mtok=1.0, output_usd_per_mtok=5.0)

PROMPT = llm.Prompt(
    system="be brief",
    user="describe their taste",
    schema=llm.PARAGRAPHS_SCHEMA,
    max_tokens=500,
)


@pytest.fixture
def anthropic() -> FakeAnthropic:
    return FakeAnthropic()


def adapter(
    anthropic: FakeAnthropic, *, poll_seconds: float = 0.0, timeout_seconds: float = 60.0
) -> llm.AnthropicAdapter:
    """The real adapter over the fake's transport, with its waits collapsed to nothing.

    Sleep is injected the way the TMDB client's throttle injects it: a test about retry
    and polling should assert what happened, not spend the seconds it would have taken.
    """
    return llm.AnthropicAdapter(
        api_key="test-key",
        base_url="https://api.anthropic.com",
        version="2023-06-01",
        max_attempts=3,
        poll_seconds=poll_seconds,
        timeout_seconds=timeout_seconds,
        transport=anthropic.transport(),
        sleep=_no_wait,
    )


async def _no_wait(seconds: float) -> None:
    pass


# --- One immediate call ---


async def test_a_call_asks_for_the_model_the_tier_chose(anthropic):
    await adapter(anthropic).complete(PROMPT, model=MODEL, dispatch=llm.Dispatch.immediate)

    (request,) = anthropic.calls("POST", "/v1/messages")
    assert request.body["model"] == "claude-haiku-4-5"
    assert request.body["max_tokens"] == 500
    assert request.body["messages"] == [{"role": "user", "content": "describe their taste"}]


async def test_a_call_puts_the_operations_schema_on_the_wire(anthropic):
    """Structured output is what makes the schema a contract rather than a hope."""
    await adapter(anthropic).complete(PROMPT, model=MODEL, dispatch=llm.Dispatch.immediate)

    (request,) = anthropic.calls("POST", "/v1/messages")
    assert request.body["output_config"]["format"] == {
        "type": "json_schema",
        "schema": llm.PARAGRAPHS_SCHEMA,
    }


async def test_a_call_comes_back_with_its_text_and_its_tokens(anthropic):
    anthropic.answer = '{"paragraphs": ["You like slow films."]}'
    anthropic.input_tokens, anthropic.output_tokens = 900, 40

    completion = await adapter(anthropic).complete(
        PROMPT, model=MODEL, dispatch=llm.Dispatch.immediate
    )

    assert json.loads(completion.text) == {"paragraphs": ["You like slow films."]}
    assert (completion.input_tokens, completion.output_tokens) == (900, 40)


async def test_a_refusal_is_not_an_answer(anthropic):
    """A message with no text block has nothing the schema could accept."""
    anthropic.no_text = True
    anthropic.stop_reason = "refusal"

    with pytest.raises(llm.BadAnswer):
        await adapter(anthropic).complete(PROMPT, model=MODEL, dispatch=llm.Dispatch.immediate)


# --- Throttling and outages ---


async def test_throttling_is_waited_out_and_retried(anthropic):
    anthropic.throttled = 2

    await adapter(anthropic).complete(PROMPT, model=MODEL, dispatch=llm.Dispatch.immediate)

    assert len(anthropic.calls("POST", "/v1/messages")) == 3


async def test_a_provider_that_stays_down_is_a_skip_not_a_crash(anthropic):
    """Everything the provider can do to us degrades to serving cached results."""
    anthropic.down = True

    with pytest.raises(llm.Skipped):
        await adapter(anthropic).complete(PROMPT, model=MODEL, dispatch=llm.Dispatch.immediate)


# --- A request the provider will not accept ---


async def test_a_rejected_request_carries_what_the_provider_objected_to(anthropic):
    """#117: the status code alone cannot tell a bug from bad weather.

    The body the provider sent back is the only thing that names the mistake, and
    discarding it meant diagnosing #116 through a droplet console instead of a log line.
    """
    anthropic.rejection = Rejection()

    with pytest.raises(llm.BadRequest) as raised:
        await adapter(anthropic).complete(PROMPT, model=MODEL, dispatch=llm.Dispatch.immediate)

    assert "'maxItems' is not supported" in str(raised.value)
    assert "req_011CerciLqADx3pZj9MEs8rq" in str(raised.value)
    assert "400" in str(raised.value)


async def test_a_rejected_request_still_only_skips(anthropic):
    """Our own malformed request must not break a feed any more than a busy provider."""
    anthropic.rejection = Rejection()

    with pytest.raises(llm.Skipped):
        await adapter(anthropic).complete(PROMPT, model=MODEL, dispatch=llm.Dispatch.immediate)


async def test_a_rejected_request_is_not_asked_again(anthropic):
    """Retrying is the answer to weather and the wrong answer to a bug: the same
    malformed request fails identically however many times it is sent."""
    anthropic.rejection = Rejection()

    with pytest.raises(llm.BadRequest):
        await adapter(anthropic).complete(PROMPT, model=MODEL, dispatch=llm.Dispatch.immediate)

    assert len(anthropic.calls("POST", "/v1/messages")) == 1


async def test_a_rejection_says_nothing_about_the_credential_or_the_prompt(anthropic):
    """The complaint is loud and the evidence behind it is the owner's, so only what came
    back travels: never the key, and never what was asked."""
    anthropic.rejection = Rejection(status=401, message="invalid x-api-key")

    with pytest.raises(llm.BadRequest) as raised:
        await adapter(anthropic).complete(PROMPT, model=MODEL, dispatch=llm.Dispatch.immediate)

    complaint = str(raised.value)
    assert "test-key" not in complaint
    assert PROMPT.user not in complaint
    assert PROMPT.system not in complaint


async def test_a_rejection_without_a_readable_body_still_names_what_it_can(anthropic):
    """A gateway in front of the provider answers HTML and stamps the id on the header."""
    anthropic.rejection = Rejection(
        status=403, body="<html><body>Forbidden</body></html>", in_header=True
    )

    with pytest.raises(llm.BadRequest) as raised:
        await adapter(anthropic).complete(PROMPT, model=MODEL, dispatch=llm.Dispatch.immediate)

    assert "403" in str(raised.value)
    assert "Forbidden" in str(raised.value)
    assert "req_011CerciLqADx3pZj9MEs8rq" in str(raised.value)


async def test_a_rejection_is_short_enough_to_read_in_a_log_line(anthropic):
    """A body of any size lands in a worker log; what makes it useful is the first
    sentence of it, not all of it."""
    anthropic.rejection = Rejection(message="x" * 4000)

    with pytest.raises(llm.BadRequest) as raised:
        await adapter(anthropic).complete(PROMPT, model=MODEL, dispatch=llm.Dispatch.immediate)

    assert len(str(raised.value)) < 500


async def test_throttling_that_never_lets_up_is_weather_not_a_bug(anthropic):
    """A 429 is the one 4xx that is the provider's condition rather than our mistake."""
    anthropic.throttled = 99

    with pytest.raises(llm.ProviderUnavailable):
        await adapter(anthropic).complete(PROMPT, model=MODEL, dispatch=llm.Dispatch.immediate)

    assert len(anthropic.calls("POST", "/v1/messages")) == 3


async def test_a_provider_outage_says_what_the_provider_said(anthropic):
    """Weather is logged quietly, but it is still worth knowing what came back."""
    anthropic.down = True

    with pytest.raises(llm.ProviderUnavailable) as raised:
        await adapter(anthropic).complete(PROMPT, model=MODEL, dispatch=llm.Dispatch.immediate)

    assert "overloaded" in str(raised.value)


async def test_a_box_with_no_credential_skips_rather_than_fails():
    """The dev default: the app runs, and nothing it shows ever refreshes."""
    built = llm.build_adapter(Settings())

    with pytest.raises(llm.Unconfigured):
        await built.complete(PROMPT, model=MODEL, dispatch=llm.Dispatch.immediate)


async def test_an_empty_credential_is_no_credential():
    """#109: the deploy renders every ``ANCHOR_*`` line whether or not its secret is set.

    An unset repo secret therefore reaches the container as an empty string rather than
    as nothing at all, and a blank key builds a real client whose every call 401s - which
    is a worse failure than skipping, and one ``/api/health`` would call ``configured``.
    """
    assert Settings(anthropic_api_key="   ").llm_credential_configured is False

    built = llm.build_adapter(Settings(anthropic_api_key=""))

    with pytest.raises(llm.Unconfigured):
        await built.complete(PROMPT, model=MODEL, dispatch=llm.Dispatch.immediate)


async def test_a_provider_nobody_wrote_an_adapter_for_is_refused():
    with pytest.raises(llm.ProviderRefused):
        llm.build_adapter(Settings(llm_provider="openai"))


# --- Batches ---


async def test_a_batched_call_is_created_polled_and_fetched(anthropic):
    anthropic.polls_before_ending = 2

    completion = await adapter(anthropic).complete(PROMPT, model=MODEL, dispatch=llm.Dispatch.batch)

    (created,) = anthropic.calls("POST", "/v1/messages/batches")
    assert created.body["requests"][0]["params"]["model"] == "claude-haiku-4-5"
    assert len(anthropic.calls("GET", "/v1/messages/batches/msgbatch_test")) == 3
    assert completion.input_tokens == anthropic.input_tokens


async def test_a_batch_that_never_ends_is_abandoned_and_cancelled(anthropic):
    """An answer nobody will read should not also be an answer nobody ledgered."""
    anthropic.polls_before_ending = 1000

    with pytest.raises(llm.ProviderUnavailable):
        await adapter(anthropic, timeout_seconds=0.0).complete(
            PROMPT, model=MODEL, dispatch=llm.Dispatch.batch
        )

    assert anthropic.cancelled


async def test_a_batched_request_that_failed_is_a_skip(anthropic):
    anthropic.batch_result_type = "expired"

    with pytest.raises(llm.Skipped):
        await adapter(anthropic).complete(PROMPT, model=MODEL, dispatch=llm.Dispatch.batch)


async def test_a_batch_polled_with_a_rejected_id_is_still_cancelled(anthropic):
    """The create landed, so there is a batch out there costing money whatever the poll
    came back as. Only the reason for giving up differs."""
    anthropic.rejection = Rejection(status=404, message="batch not found", after=1)

    with pytest.raises(llm.BadRequest):
        await adapter(anthropic).complete(PROMPT, model=MODEL, dispatch=llm.Dispatch.batch)

    assert anthropic.cancelled


async def test_a_batch_row_that_errored_says_what_the_provider_objected_to(anthropic):
    """#117 in the batched half of the seam: the row carries the same explanation, and a
    batch is where losing it hurts most - the wait is spent before anyone finds out."""
    anthropic.batch_result_type = "errored"
    anthropic.batch_error = {
        "type": "error",
        "error": {"type": "invalid_request_error", "message": "max_tokens is required"},
    }

    with pytest.raises(llm.BadRequest) as raised:
        await adapter(anthropic).complete(PROMPT, model=MODEL, dispatch=llm.Dispatch.batch)

    assert "max_tokens is required" in str(raised.value)


async def test_a_batch_row_the_provider_broke_on_is_weather(anthropic):
    """The same row, an error type that is theirs rather than ours."""
    anthropic.batch_result_type = "errored"
    anthropic.batch_error = {
        "type": "error",
        "error": {"type": "overloaded_error", "message": "overloaded"},
    }

    with pytest.raises(llm.ProviderUnavailable) as raised:
        await adapter(anthropic).complete(PROMPT, model=MODEL, dispatch=llm.Dispatch.batch)

    assert "overloaded" in str(raised.value)


# --- A schema the provider will actually accept ---


@pytest.mark.parametrize(
    "schema",
    [llm.PARAGRAPHS_SCHEMA, llm.RANKING_SCHEMA, llm.QUALITIES_SCHEMA],
    ids=["paragraphs", "ranking", "qualities"],
)
def test_every_operations_schema_is_one_structured_outputs_accepts(schema):
    """The gap that let #116 ship: the suite asserted a schema was sent, never that it was legal."""
    assert_schema_is_accepted(schema)


async def test_a_schema_the_provider_would_refuse_fails_here_rather_than_in_production(anthropic):
    """A 400 on the wire is a ``Skipped``, which reads as success - so the fake must shout."""
    prompt = llm.Prompt(
        system="be brief",
        user="describe their taste",
        schema={
            "type": "object",
            "properties": {
                "paragraphs": {"type": "array", "items": {"type": "string"}, "maxItems": 4}
            },
            "required": ["paragraphs"],
            "additionalProperties": False,
        },
        max_tokens=500,
    )

    with pytest.raises(RejectedSchema, match="maxItems"):
        await adapter(anthropic).complete(prompt, model=MODEL, dispatch=llm.Dispatch.immediate)


async def test_a_batched_call_has_its_schema_checked_too(anthropic):
    """A batch wraps the same body, so a bad schema must not slip in through the other door."""
    prompt = llm.Prompt(
        system="be brief",
        user="describe their taste",
        schema={"type": "object", "properties": {}, "additionalProperties": True},
        max_tokens=500,
    )

    with pytest.raises(RejectedSchema, match="additionalProperties"):
        await adapter(anthropic).complete(prompt, model=MODEL, dispatch=llm.Dispatch.batch)


@pytest.mark.parametrize(
    ("schema", "rejected"),
    [
        ({"type": "array", "items": {"type": "string"}, "minItems": 4}, "minItems"),
        (
            {
                "type": "object",
                "properties": {"n": {"type": "integer", "minimum": 0}},
                "additionalProperties": False,
            },
            "minimum",
        ),
        (
            {
                "type": "object",
                "properties": {"s": {"type": "string", "pattern": "^a"}},
                "additionalProperties": False,
            },
            "pattern",
        ),
        (
            {
                "type": "object",
                "properties": {"s": {"type": "string", "format": "slug"}},
                "additionalProperties": False,
            },
            "format",
        ),
        (
            {"type": "object", "properties": {}, "additionalProperties": False, "minProperties": 1},
            "minProperties",
        ),
        ({"type": ["string", "null"]}, "union type"),
    ],
    ids=[
        "min-items-above-one",
        "numeric-bound",
        "regex",
        "unknown-format",
        "property-count",
        "union-type",
    ],
)
def test_the_keywords_structured_outputs_rejects_are_refused(schema, rejected):
    with pytest.raises(RejectedSchema, match=rejected):
        assert_schema_is_accepted(schema)


def test_the_two_min_items_values_the_provider_does_accept_are_allowed():
    """0 and 1 are legal, and only the bound above them is not."""
    for value in (0, 1):
        assert_schema_is_accepted({"type": "array", "items": {"type": "string"}, "minItems": value})


@pytest.mark.parametrize(
    "schema",
    [
        {
            "type": "object",
            "properties": {"when": {"type": "string", "format": "date-time"}},
            "additionalProperties": False,
        },
        {
            "$defs": {"leaf": {"type": "object", "properties": {}, "additionalProperties": False}},
            "type": "object",
            "properties": {"leaf": {"$ref": "#/$defs/leaf"}},
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {"either": {"anyOf": [{"type": "string"}, {"type": "integer"}]}},
            "additionalProperties": False,
        },
    ],
    ids=["supported-string-format", "internal-ref", "any-of-branch"],
)
def test_what_structured_outputs_does_accept_is_not_refused(schema):
    """The check has to be exact in both directions - refusing a legal schema is its own bug.

    A ``$ref`` node and an ``anyOf`` branch carry no ``type``, and a supported string
    ``format`` is legal, so all three are the cases a keyword allowlist gets wrong first.
    """
    assert_schema_is_accepted(schema)
