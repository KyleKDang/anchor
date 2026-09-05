"""The provider faked at the seam's adapter boundary, per testing.md.

The LLM operations module is the fake boundary, so the fake sits directly under it: the
seam's own work - the allowlist, the readiness gate, both caps, the ledger row, the
schema check - runs for real against a scripted answer, and no automated test ever
reaches a provider.

The adapter records every call, so a test can assert which model an operation was priced
against and whether it was batched without asserting anything about a prompt's wording.
"""

import json
from dataclasses import dataclass, field
from typing import Any

from anchor import llm


@dataclass(frozen=True)
class Asked:
    """One dispatch, as the adapter saw it."""

    prompt: llm.Prompt
    model: llm.Model
    dispatch: llm.Dispatch


DEFAULT_PROSE = {
    "paragraphs": [
        "You go for films that take their time.",
        "What leaves you cold is a big finish that has not been earned.",
    ]
}
"""What the fake answers when a test scripted nothing.

Prose, because that is the operation with a consumer in this ticket, and a test about
whether a regeneration happened at all should not have to invent one to find out. Every
test that cares what came back scripts its own answer.
"""


@dataclass
class FakeLlm:
    """A scripted adapter. Queue answers with ``will_say``; read back what was asked."""

    provider: str = "anthropic"
    input_tokens: int = 1000
    output_tokens: int = 200
    asked: list[Asked] = field(default_factory=list)
    answers: list[str] = field(default_factory=list)
    failure: Exception | None = None
    """Raised instead of answering, for the provider-is-down and no-credential paths."""

    def will_say(self, **payload: Any) -> "FakeLlm":
        """Queue one answer, as the JSON the provider would have returned."""
        self.answers.append(json.dumps(payload))
        return self

    def will_say_exactly(self, text: str) -> "FakeLlm":
        """Queue one raw answer, for the case where it is not valid against the schema."""
        self.answers.append(text)
        return self

    def costs(self, *, input_tokens: int, output_tokens: int) -> "FakeLlm":
        self.input_tokens, self.output_tokens = input_tokens, output_tokens
        return self

    def will_fail(self, error: Exception) -> "FakeLlm":
        self.failure = error
        return self

    @property
    def dispatched(self) -> int:
        return len(self.asked)

    @property
    def last(self) -> Asked:
        assert self.asked, "nothing was dispatched"
        return self.asked[-1]

    async def complete(
        self, prompt: llm.Prompt, *, model: llm.Model, dispatch: llm.Dispatch
    ) -> llm.Completion:
        if self.failure is not None:
            raise self.failure
        self.asked.append(Asked(prompt=prompt, model=model, dispatch=dispatch))
        answer = self.answers.pop(0) if self.answers else json.dumps(DEFAULT_PROSE)
        return llm.Completion(
            text=answer, input_tokens=self.input_tokens, output_tokens=self.output_tokens
        )

    async def aclose(self) -> None:
        pass
