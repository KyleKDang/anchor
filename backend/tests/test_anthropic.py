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
from fakeanthropic import FakeAnthropic

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


async def test_a_box_with_no_credential_skips_rather_than_fails():
    """The dev default: the app runs, and nothing it shows ever refreshes."""
    built = llm.build_adapter(Settings())

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
