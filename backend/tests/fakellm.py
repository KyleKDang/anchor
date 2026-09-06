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


DEFAULTS: dict[str, dict[str, Any]] = {
    "paragraphs": {
        "paragraphs": [
            "You go for films that take their time.",
            "What leaves you cold is a big finish that has not been earned.",
        ]
    },
    "qualities": {"qualities": []},
    "ranked": {"ranked": []},
}
"""What the fake answers when a test scripted nothing, keyed by what was asked for.

One per answer shape rather than one for all, because a run that dispatches two
operations would otherwise hand a prose answer to a quality question and fail the schema
check for a reason no test meant to be about schemas. The defaults are deliberately
empty where empty is meaningful - no suggestions, no ranking - so a test that cares what
came back has to script it.
"""


@dataclass
class FakeLlm:
    """A scripted adapter. Queue answers with ``will_say``; read back what was asked."""

    provider: str = "anthropic"
    input_tokens: int = 1000
    output_tokens: int = 200
    asked: list[Asked] = field(default_factory=list)
    answers: dict[str, list[str]] = field(default_factory=dict)
    raw: list[tuple[str | None, str]] = field(default_factory=list)
    failure: Exception | None = None
    """Raised instead of answering, for the provider-is-down and no-credential paths."""

    def will_say(self, **payload: Any) -> "FakeLlm":
        """Queue one answer, as the JSON the provider would have returned.

        Queued against the answer shape rather than in one flat line, so a test scripts
        the operation it means: a run that regenerates prose and refreshes the picker's
        suggestions dispatches both, and a single queue would hand the first answer to
        whichever went first and make every such test an assertion about job order.
        """
        (shape,) = payload
        self.answers.setdefault(shape, []).append(json.dumps(payload))
        return self

    def will_say_exactly(self, text: str, system: str | None = None) -> "FakeLlm":
        """Queue one raw answer, for the case where it is not valid against the schema.

        It cannot be queued by shape the way a valid answer is, because the whole point of
        it is that it does not parse into one. Left unaimed it answers the next dispatch
        whatever that is, which is all a test driving the seam directly needs. Naming an
        operation's system prompt aims it at that operation instead, which is what a test
        driving a *flow* needs: a run that regenerates prose, refreshes the picker's
        suggestions and tags a film dispatches three times, and an unaimed answer would
        break whichever went first.
        """
        self.raw.append((system, text))
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

    def asked_of(self, system: str) -> list[Asked]:
        """Every dispatch of one operation, in order.

        Matched on the operation's own system prompt, which is the only thing about a
        dispatch that names which operation it was - the adapter is told a prompt, a
        model and a dispatch mode, and nothing else. A run that regenerates prose and
        refreshes the picker's suggestions dispatches twice, so a test asserting what one
        of them was shown has to say which one it means.
        """
        return [one for one in self.asked if one.prompt.system == system]

    def last_of(self, system: str) -> Asked:
        asked = self.asked_of(system)
        assert asked, "that operation was never dispatched"
        return asked[-1]

    async def complete(
        self, prompt: llm.Prompt, *, model: llm.Model, dispatch: llm.Dispatch
    ) -> llm.Completion:
        if self.failure is not None:
            raise self.failure
        self.asked.append(Asked(prompt=prompt, model=model, dispatch=dispatch))
        answer = self._answer_to(prompt)
        return llm.Completion(
            text=answer, input_tokens=self.input_tokens, output_tokens=self.output_tokens
        )

    async def aclose(self) -> None:
        pass

    def _answer_to(self, prompt: llm.Prompt) -> str:
        """A raw answer if one is meant for this, else the next of this shape, else the default."""
        for index, (system, text) in enumerate(self.raw):
            if system is None or system == prompt.system:
                self.raw.pop(index)
                return text
        (shape,) = prompt.schema["required"]
        queued = self.answers.get(shape)
        return queued.pop(0) if queued else json.dumps(DEFAULTS[shape])
