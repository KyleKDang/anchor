"""The LLM seam: Anchor's four jobs, what they cost, and who is allowed to run them.

*Operations-shaped, not a prompt wrapper.* The surface is ``rerank_candidates``,
``regenerate_prose_profile``, ``tag_film_qualities`` and ``suggest_qualities`` and
nothing else (architecture.md). Each owns its prompt and its answer schema, so a caller
cannot ask the provider a question Anchor did not design, and every answer is parsed
into a typed value before any of it reaches the database.

*Worker-only.* Nothing in the web process imports this module, which is what makes the
precompute-only rule (taste-profile.md) a structural property rather than a convention:
an interactive request path cannot wait on an LLM call because the code that makes one
is not loaded in the process serving it. The jobs that do use it import it inside the
job function, the way the trainer is imported.

*The money lives here.* Three gates stand in front of every dispatch, in this order:
the provider must be on ADR 0003's no-training allowlist, the account must have earned
spend by reaching *forming*, and neither monthly cap may already be spent. Past them,
the call is made and a ledger row is written - written for every call that reached a
provider, including one whose answer turns out to be unusable, because the tokens were
bought either way.

*Every refusal is a skip, never a failure.* Cap reached, no credential, provider down:
all of them raise :class:`Skipped`, and every caller answers it by serving what it has
cached. That is the invisible degradation ADR 0004 designed in - a stale prose profile
and classical-scorer ordering, never a broken screen and never a runaway bill.
"""

import asyncio
import enum
import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from time import monotonic
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import prose, readiness
from anchor.db import Database
from anchor.models import Film, LlmOperation, SpendLedgerEntry
from anchor.settings import Settings

log = logging.getLogger(__name__)

ALLOWED_PROVIDERS = frozenset({"anthropic", "openai", "gemini_paid"})
"""Providers whose terms bar training on customer API inputs by default (ADR 0003).

Verified 2026-08-02 in docs/research/llm-provider-data-use.md: the Anthropic API, the
OpenAI API, and Gemini's paid tier qualify; Gemini's free tier and Voyage AI at their
defaults do not, and so are absent rather than commented out. Every future provider gets
the same check at integration time, and adding a name here is the moment to do it - the
owner's taste profile and TMDB metadata are what a dispatch sends.
"""

BATCH_DISCOUNT = 0.5
"""What Message Batches cost against the same tokens dispatched immediately."""

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


class Dispatch(enum.StrEnum):
    """How a call is sent. Batches are half price and asynchronous; nothing waits."""

    immediate = "immediate"
    batch = "batch"


class Skipped(Exception):
    """The operation did not run, and the caller should serve what it has cached.

    Every subclass is an ordinary condition rather than a bug: the caps are meant to be
    hit, a box without a credential is meant to keep working, and a provider is
    sometimes down. Callers catch this class, not its members.
    """


class NotEarned(Skipped):
    """The account has not reached *forming*, so no spend is warranted on it yet."""


class CapReached(Skipped):
    """A monthly cap is already spent: this month's work is over for that scope."""


class Unconfigured(Skipped):
    """No provider credential on this box; the dev default."""


class ProviderUnavailable(Skipped):
    """The provider could not answer: down, still throttling, or too slow to wait for."""


class ProviderRefused(Exception):
    """A provider that is not on the no-training allowlist. A bug, and loud (ADR 0003)."""


class BadAnswer(Exception):
    """The provider answered something the operation's schema does not accept."""


@dataclass(frozen=True)
class Model:
    """One model and what it costs, as the operator configured them."""

    id: str
    input_usd_per_mtok: float
    output_usd_per_mtok: float


@dataclass(frozen=True)
class Prompt:
    """One question, ready to be asked of whichever model the tier picks."""

    system: str
    user: str
    schema: dict[str, Any]
    """The JSON schema the provider is asked to answer in. The pydantic model beside it
    is what actually validates the answer, so a provider that ignores this is caught."""
    max_tokens: int


@dataclass(frozen=True)
class Completion:
    """What came back, and what it cost in tokens."""

    text: str
    input_tokens: int
    output_tokens: int


class Adapter(Protocol):
    """One provider. The seam holds exactly one, and refuses it unless it is allowlisted."""

    provider: str

    async def complete(self, prompt: Prompt, *, model: Model, dispatch: Dispatch) -> Completion: ...

    async def aclose(self) -> None: ...


# --- What the operations answer with ---


class Paragraphs(BaseModel):
    """The prose profile: a few short paragraphs addressed to the owner."""

    model_config = ConfigDict(extra="forbid")

    paragraphs: list[str] = Field(min_length=1, max_length=4)

    @property
    def text(self) -> str:
        return "\n\n".join(paragraph.strip() for paragraph in self.paragraphs)


class RankedCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tmdb_id: int
    explanation: str
    """Precomputed beside the verdict it explains, because no screen may wait on one."""


class Ranking(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ranked: list[RankedCandidate]


class Qualities(BaseModel):
    """A subset of a vocabulary that was offered. Both quality operations answer this."""

    model_config = ConfigDict(extra="forbid")

    qualities: list[str]


PARAGRAPHS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "paragraphs": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 4,
        },
    },
    "required": ["paragraphs"],
    "additionalProperties": False,
}

RANKING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ranked": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tmdb_id": {"type": "integer"},
                    "explanation": {"type": "string"},
                },
                "required": ["tmdb_id", "explanation"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["ranked"],
    "additionalProperties": False,
}

QUALITIES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"qualities": {"type": "array", "items": {"type": "string"}}},
    "required": ["qualities"],
    "additionalProperties": False,
}
"""The schemas are written out rather than derived from the models above.

They are the contract with the provider and the models are the contract with Anchor, and
the two want different things - the wire schema has to be flat and closed for structured
output to accept it, while the model carries the length bounds and the derived text. If
they ever drift, the model is what decides, because it is what runs on the answer.
"""


@dataclass(frozen=True)
class Candidate:
    """A film being judged for the discovery feed, as the reranker is shown it.

    Discovery arrives with #38; this is the shape its prompt reads, kept beside the
    operation that consumes it so the operation is complete before its caller exists.
    """

    tmdb_id: int
    title: str
    year: int | None
    genres: Sequence[str]
    directors: Sequence[str]
    overview: str


class Llm:
    """The seam. One adapter, both caps, and the four operations.

    It holds the :class:`Database` rather than a session on purpose: the ledger row for
    a call must survive whatever the caller's own transaction goes on to do, because the
    tokens are spent whether or not the work that asked for them lands.
    """

    def __init__(self, adapter: Adapter, db: Database, settings: Settings) -> None:
        _require_allowlisted(adapter)
        self._adapter = adapter
        self._db = db
        self._settings = settings

    async def aclose(self) -> None:
        await self._adapter.aclose()

    # --- The four operations ---

    async def regenerate_prose_profile(
        self, account_id: uuid.UUID, evidence: "prose.Evidence"
    ) -> str:
        """Rewrite what Anchor thinks this owner likes, in the second person.

        The mid tier, because this is the one LLM output the owner reads as prose rather
        than consumes as an ordering. Active profile constraints go in as instructions
        rather than as evidence: a regeneration that merely weighs them is a regeneration
        that may quietly clobber a correction the owner already made.
        """
        answer = await self._run(
            LlmOperation.regenerate_prose_profile,
            _prose_prompt(evidence),
            Paragraphs,
            account_id=account_id,
        )
        return answer.text

    async def rerank_candidates(
        self, account_id: uuid.UUID, profile: str, candidates: Sequence[Candidate]
    ) -> list[RankedCandidate]:
        """Order never-rated films by how much this owner would want them, with reasons.

        Listwise: the candidates are judged against each other in one call, which is what
        the cheap tier is bought for and what a per-film call could not do at any price.
        Films the answer invents or repeats are dropped - the feed may only carry what
        was actually offered.
        """
        answer = await self._run(
            LlmOperation.rerank_candidates,
            _rerank_prompt(profile, candidates),
            Ranking,
            account_id=account_id,
        )
        offered = {candidate.tmdb_id for candidate in candidates}
        return list(_first_of_each(answer.ranked, offered))

    async def tag_film_qualities(self, film: Film, vocabulary: Sequence[str]) -> list[str]:
        """Which of the built-in vocabulary a film is notable for. Shared, not per account.

        A quality tag is a fact about the film rather than about anybody's taste, so it
        is bought once for everyone and its ledger row carries no account. Answers are
        filtered to the vocabulary offered: the system never invents a quality.
        """
        answer = await self._run(
            LlmOperation.tag_film_qualities,
            _tag_prompt(film, vocabulary),
            Qualities,
            account_id=None,
        )
        return _within(answer.qualities, vocabulary)

    async def suggest_qualities(
        self, account_id: uuid.UUID, evidence: "prose.Evidence", vocabulary: Sequence[str]
    ) -> list[str]:
        """Which of the account's quality list to pre-check in the picker.

        Confirm-not-author: the owner's job is to untick what is wrong, so a suggestion
        that is merely plausible costs them a tap while a missing one costs them the
        benefit. Filtered to their own list, which the picker's free text is what adds to.
        """
        answer = await self._run(
            LlmOperation.suggest_qualities,
            _suggest_prompt(evidence, vocabulary),
            Qualities,
            account_id=account_id,
        )
        return _within(answer.qualities, vocabulary)

    # --- The gates every operation passes through ---

    async def _run[AnswerT: BaseModel](
        self,
        operation: LlmOperation,
        prompt: Prompt,
        answer: type[AnswerT],
        *,
        account_id: uuid.UUID | None,
    ) -> AnswerT:
        """Allowlist, earned spend, both caps, dispatch, ledger, schema. In that order.

        The ledger row is written before the answer is parsed, and in a transaction of
        its own, so a provider that answers nonsense still costs what it cost. Parsing
        after recording is also what lets a schema failure be loud: that is a bug in a
        prompt, not a condition to degrade quietly around.
        """
        _require_allowlisted(self._adapter)
        model = self._model_for(operation)
        dispatch = self._dispatch_for(operation)
        async with self._db.sessions() as session:
            if account_id is not None:
                await self._require_earned(session, account_id)
            await self._require_budget(session, account_id)

        completion = await self._adapter.complete(prompt, model=model, dispatch=dispatch)
        await self._record(operation, account_id, model, dispatch, completion)
        return _parse(answer, completion.text)

    def _model_for(self, operation: LlmOperation) -> Model:
        if operation.value in self._settings.mid_tier_operations:
            return Model(
                id=self._settings.llm_mid_model,
                input_usd_per_mtok=self._settings.llm_mid_input_usd_per_mtok,
                output_usd_per_mtok=self._settings.llm_mid_output_usd_per_mtok,
            )
        return Model(
            id=self._settings.llm_cheap_model,
            input_usd_per_mtok=self._settings.llm_cheap_input_usd_per_mtok,
            output_usd_per_mtok=self._settings.llm_cheap_output_usd_per_mtok,
        )

    def _dispatch_for(self, operation: LlmOperation) -> Dispatch:
        if operation.value in self._settings.batched_operations:
            return Dispatch.batch
        return Dispatch.immediate

    async def _require_earned(self, session: AsyncSession, account_id: uuid.UUID) -> None:
        """Zero spend until the account has told Anchor enough to be worth describing.

        Enforced here rather than in each account-scoped caller, because "hollow accounts
        cost nothing" (ADR 0004) is a property of the spend, and this is the one place
        every account-scoped spend passes through. Shared work is the exception it cannot
        cover: a quality tag is nobody's, so there is no account here to read, and the
        caller whose activity asks for one carries the same gate itself. Both call
        :func:`readiness.earned_spend`, so where the bar sits is stated once.
        """
        if not await readiness.earned_spend(session, account_id, self._settings):
            raise NotEarned(f"account {account_id} is still cold")

    async def _require_budget(self, session: AsyncSession, account_id: uuid.UUID | None) -> None:
        """Both month-to-date sums, checked before dispatch rather than after.

        Checked before, so a cap is a ceiling on what gets spent rather than a report on
        what already was. It is soft by exactly one call: a dispatch that starts under
        the cap is allowed to finish above it, which is the price of not knowing what a
        call costs until it returns.
        """
        if account_id is not None:
            spent = await _month_to_date(session, account_id=account_id)
            if spent >= _micros(self._settings.llm_account_monthly_cap_usd):
                raise CapReached(f"account {account_id} has spent its month's budget")
        spent = await _month_to_date(session, account_id=None)
        if spent >= _micros(self._settings.llm_global_monthly_cap_usd):
            raise CapReached("the platform has spent its month's budget")

    async def _record(
        self,
        operation: LlmOperation,
        account_id: uuid.UUID | None,
        model: Model,
        dispatch: Dispatch,
        completion: Completion,
    ) -> None:
        async with self._db.sessions() as session:
            session.add(
                SpendLedgerEntry(
                    account_id=account_id,
                    operation=operation,
                    model=model.id,
                    input_tokens=completion.input_tokens,
                    output_tokens=completion.output_tokens,
                    cost_micros=_cost_micros(model, dispatch, completion),
                )
            )
            await session.commit()


def _require_allowlisted(adapter: Adapter) -> None:
    """Checked when the seam is built, and again on every dispatch.

    Twice on purpose. The first refusal is the useful one - a misconfigured box fails at
    boot rather than at its first refresh - and the second is what makes "a
    non-allowlisted provider cannot be dispatched to" a property of the code rather than
    of one constructor, however the object was assembled.
    """
    if adapter.provider not in ALLOWED_PROVIDERS:
        raise ProviderRefused(
            f"{adapter.provider!r} is not on the no-training provider allowlist (ADR 0003)"
        )


def _cost_micros(model: Model, dispatch: Dispatch, completion: Completion) -> int:
    """Tokens at the tier's prices, in millionths of a dollar.

    The arithmetic looks too simple because the units cancel: a price of $X per million
    tokens is exactly X millionths of a dollar per token, so the configured price per
    Mtok is already the per-token cost in micros.
    """
    priced = (
        completion.input_tokens * model.input_usd_per_mtok
        + completion.output_tokens * model.output_usd_per_mtok
    )
    discount = BATCH_DISCOUNT if dispatch is Dispatch.batch else 1.0
    return round(priced * discount)


def _micros(usd: float) -> int:
    return round(usd * 1_000_000)


async def _month_to_date(session: AsyncSession, *, account_id: uuid.UUID | None) -> int:
    """This calendar month's spend in micros: one account's, or the whole platform's.

    ``account_id=None`` is the global sum over every row, shared scope included - not the
    sum of the shared-scope rows. The two caps ask different questions of the same table,
    and only the account one narrows.
    """
    query = select(func.coalesce(func.sum(SpendLedgerEntry.cost_micros), 0)).where(
        SpendLedgerEntry.created_at >= func.date_trunc("month", func.now())
    )
    if account_id is not None:
        query = query.where(SpendLedgerEntry.account_id == account_id)
    return int(await session.scalar(query) or 0)


def _parse[Answer: BaseModel](answer: type[Answer], text: str) -> Answer:
    try:
        return answer.model_validate_json(text)
    except ValidationError as error:
        raise BadAnswer(f"the provider's answer is not a valid {answer.__name__}") from error


def _within(named: Iterable[str], vocabulary: Sequence[str]) -> list[str]:
    """The answer, filtered to what was offered, in the vocabulary's own order.

    Anchor's quality list is closed to what the vocabulary holds and what the owner typed
    (taste-profile.md), so a name nobody offered is dropped rather than added. Reading it
    back in the vocabulary's order also drops duplicates and makes the answer stable.
    """
    chosen = {name.strip().casefold() for name in named}
    return [name for name in vocabulary if name.casefold() in chosen]


def _first_of_each(
    ranked: Iterable[RankedCandidate], offered: set[int]
) -> Iterable[RankedCandidate]:
    """The ranking, keeping its order, minus anything not offered and anything repeated."""
    seen: set[int] = set()
    for candidate in ranked:
        if candidate.tmdb_id in offered and candidate.tmdb_id not in seen:
            seen.add(candidate.tmdb_id)
            yield candidate


# --- The prompts, one per operation ---

ANCHOR_CONTEXT = """\
Anchor is a personal film taste engine. Its owner rates films by comparing them against \
each other rather than picking stars, so the ordering below is the owner's own judgment \
and never a prediction. Anchors are the films the owner designated as the canonical \
example of a half-star band, which is what makes the scale mean something to them."""

PROSE_SYSTEM = f"""\
{ANCHOR_CONTEXT}

Write the owner a short description of their own taste in film, addressed to them as \
"you". Two or three short paragraphs.

Rules:
- Describe the shape of their taste - what they respond to, what leaves them cold, what \
their favourites have in common - not a list of their films. Name a film only where it \
earns the point it is making.
- Say only what the evidence below supports. Never invent a film, a director, or an \
opinion they have not shown you.
- No ratings, no scores, no numbers, no percentages. The owner never sees a predicted \
rating and this is not where one starts.
- Plain second-person prose. No headings, no bullet points, no preamble, and never \
mention Anchor, this description, or that anything was generated.
- Where they have said something about themselves outright, treat it as settled fact and \
write around it. Do not argue with it or restate it back at them."""

RERANK_SYSTEM = f"""\
{ANCHOR_CONTEXT}

You are given a description of one owner's taste and a list of films they have never \
tracked. Order the films by how much this particular owner would want to watch them, \
best first, and give each a one-sentence reason grounded in what their taste actually is.

Rules:
- Every film offered appears exactly once. Never add a film that is not on the list.
- The reason speaks to the owner as "you" and says why this film, for them - not what \
the film is about. It is shown beside the suggestion, so it must stand on its own.
- No ratings, scores, numbers, or predicted stars anywhere."""

TAG_SYSTEM = """\
You are labelling one film against a closed vocabulary of qualities, for a film \
recommendation engine. Choose the qualities this film is genuinely notable for - the \
ones someone who admired it would name first.

Rules:
- Only names from the vocabulary given. Never invent one, and never rephrase one.
- Be selective. Most films are notable for two or three of these, not for all of them.
- This is about the film itself, not about anybody's taste in films."""

SUGGEST_SYSTEM = f"""\
{ANCHOR_CONTEXT}

You are pre-filling a checklist. The owner will be shown their own list of film \
qualities with some already ticked, and asked to confirm. Choose the ones the evidence \
below says they care about.

Rules:
- Only names from the list given. Never invent one, and never rephrase one.
- Tick what the evidence supports and leave the rest. The owner's job is to untick what \
is wrong, so a wrong tick costs them more than a missing one.
- Where they have already said they care about something, tick it."""


def _prose_prompt(evidence: "prose.Evidence") -> Prompt:
    return Prompt(
        system=PROSE_SYSTEM,
        user=_evidence_text(evidence),
        schema=PARAGRAPHS_SCHEMA,
        max_tokens=1200,
    )


def _rerank_prompt(profile: str, candidates: Sequence[Candidate]) -> Prompt:
    films = "\n".join(
        f"- {candidate.tmdb_id}: {candidate.title}"
        f"{f' ({candidate.year})' if candidate.year else ''}"
        f"{_listed(' | ', candidate.genres)}"
        f"{_listed(' | dir. ', candidate.directors)}"
        f"\n  {candidate.overview}"
        for candidate in candidates
    )
    return Prompt(
        system=RERANK_SYSTEM,
        user=f"Their taste:\n{profile}\n\nThe films, as id: title:\n{films}",
        schema=RANKING_SCHEMA,
        # Every candidate earns a sentence, so the ceiling has to scale with the list.
        max_tokens=200 + 120 * len(candidates),
    )


def _tag_prompt(film: Film, vocabulary: Sequence[str]) -> Prompt:
    credits = film.credits or {}
    directors = [str(person.get("name", "")) for person in credits.get("directors") or []]
    cast = [str(person.get("name", "")) for person in credits.get("cast") or []]
    described = "\n".join(
        line
        for line in (
            f"{film.title}{f' ({film.release_year})' if film.release_year else ''}",
            _listed("Genres: ", film.genres or []),
            _listed("Director: ", directors),
            _listed("Cast: ", cast[:5]),
            _listed("Keywords: ", (film.keywords or [])[:15]),
            film.overview or "",
        )
        if line
    )
    return Prompt(
        system=TAG_SYSTEM,
        user=f"The vocabulary:\n{_bulleted(vocabulary)}\n\nThe film:\n{described}",
        schema=QUALITIES_SCHEMA,
        max_tokens=300,
    )


def _suggest_prompt(evidence: "prose.Evidence", vocabulary: Sequence[str]) -> Prompt:
    return Prompt(
        system=SUGGEST_SYSTEM,
        user=f"Their list:\n{_bulleted(vocabulary)}\n\n{_evidence_text(evidence)}",
        schema=QUALITIES_SCHEMA,
        max_tokens=300,
    )


def _evidence_text(evidence: "prose.Evidence") -> str:
    """The account's taste as the two account-scoped prompts are shown it.

    The counts go in last and deliberately: they are what tells the model how much of a
    library it is looking at, and a description written as though nine films were nine
    hundred is the one failure an owner would notice immediately.
    """
    sections = (
        ("Their anchors, the films they picked to define each rating band", evidence.anchors),
        ("Their favourite films, favourite first", evidence.loved),
        ("Their least favourite films, least favourite first", evidence.disliked),
        ("Bonus questions they answered about specific qualities", evidence.criteria),
        ("What they have said about themselves outright", evidence.constraints),
    )
    written = "\n\n".join(f"{heading}:\n{_bulleted(lines)}" for heading, lines in sections if lines)
    return (
        f"{written}\n\n"
        f"They have rated {evidence.rated_films} film(s) and answered "
        f"{evidence.explicit_comparisons} comparison(s)."
    )


def _bulleted(lines: Iterable[str]) -> str:
    return "\n".join(f"- {line}" for line in lines)


def _listed(prefix: str, names: Sequence[str]) -> str:
    return f"{prefix}{', '.join(names)}" if names else ""


# --- The providers ---

BATCH_CUSTOM_ID = "anchor"
"""Anchor batches one call at a time, so the id only has to be a name results come back under."""

DEFAULT_RETRY_AFTER = 1.0
"""Waited after a 429 that names no ``Retry-After``."""


class AnthropicAdapter:
    """Anthropic's API over HTTP, the only provider v1 ships.

    Written against the endpoints directly rather than through the vendor SDK, for the
    reason the Resend and TMDB clients are: the HTTP edge is this project's fake boundary
    for every outside service (testing.md), so a transport is injected in tests and no
    automated test ever reaches a real provider. The surface used is small - one messages
    call, and create/poll/fetch for a batch - and the answer's shape is pinned by the
    operation's schema either way.

    Batch dispatch is a create, a poll, and a fetch, awaited to completion. That is a
    background job holding a connection for however long the batch takes, which is fine
    because a background job is exactly what it is - and it gives up rather than waits
    the API's full 24 hours, cancelling on the way out so an answer nobody will read is
    not also an answer nobody ledgered.
    """

    provider = "anthropic"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        version: str,
        max_attempts: int,
        poll_seconds: float,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock = monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "x-api-key": api_key,
                "anthropic-version": version,
                "content-type": "application/json",
            },
            transport=transport,
            # Generous, because a mid-tier answer of a thousand tokens genuinely takes
            # tens of seconds and nothing interactive is behind this.
            timeout=120.0,
        )
        self._max_attempts = max_attempts
        self._poll_seconds = poll_seconds
        self._timeout_seconds = timeout_seconds
        self._clock = clock
        self._sleep = sleep

    async def complete(self, prompt: Prompt, *, model: Model, dispatch: Dispatch) -> Completion:
        body = _request_body(prompt, model)
        if dispatch is Dispatch.batch:
            return await self._batched(body)
        return _completion(await self._call("POST", "/v1/messages", body))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _batched(self, body: dict[str, Any]) -> Completion:
        created = await self._call(
            "POST",
            "/v1/messages/batches",
            {"requests": [{"custom_id": BATCH_CUSTOM_ID, "params": body}]},
        )
        batch_id = str(created["id"])
        try:
            await self._wait_for(batch_id)
        except ProviderUnavailable:
            await self._cancel(batch_id)
            raise
        return _completion(_batch_message(await self._results(batch_id)))

    async def _wait_for(self, batch_id: str) -> None:
        deadline = self._clock() + self._timeout_seconds
        while True:
            batch = await self._call("GET", f"/v1/messages/batches/{batch_id}", None)
            if batch.get("processing_status") == "ended":
                return
            if self._clock() >= deadline:
                raise ProviderUnavailable(f"batch {batch_id} did not end in time")
            await self._sleep(self._poll_seconds)

    async def _results(self, batch_id: str) -> dict[str, Any]:
        """The batch's results, which arrive as JSONL rather than as one document."""
        response = await self._send("GET", f"/v1/messages/batches/{batch_id}/results", None)
        if response.is_error:
            raise ProviderUnavailable(f"Anthropic answered {response.status_code} for results")
        for line in response.text.splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("custom_id") == BATCH_CUSTOM_ID:
                return dict(row)
        raise ProviderUnavailable(f"batch {batch_id} came back without its request")

    async def _cancel(self, batch_id: str) -> None:
        """Best effort: a batch nobody is waiting for should not keep costing anything."""
        try:
            await self._send("POST", f"/v1/messages/batches/{batch_id}/cancel", None)
        except httpx.HTTPError:
            log.warning("could not cancel abandoned batch %s", batch_id)

    async def _call(self, method: str, path: str, body: dict[str, Any] | None) -> dict[str, Any]:
        """One request, retried through throttling the way the TMDB client is."""
        for attempt in range(1, self._max_attempts + 1):
            response = await self._send(method, path, body)
            retryable = response.status_code == 429 or response.status_code >= 500
            if retryable and attempt < self._max_attempts:
                await self._sleep(_retry_after(response))
                continue
            if response.is_error:
                raise ProviderUnavailable(f"Anthropic answered {response.status_code} for {path}")
            return dict(response.json())
        raise ProviderUnavailable(
            f"Anthropic kept refusing {path} after {self._max_attempts} tries"
        )

    async def _send(self, method: str, path: str, body: dict[str, Any] | None) -> httpx.Response:
        try:
            return await self._client.request(method, path, json=body)
        except httpx.HTTPError as error:
            raise ProviderUnavailable(f"Anthropic is unreachable: {error}") from error


class UnconfiguredAdapter:
    """No provider credential: every operation skips, and the app serves what it cached.

    Skipping rather than failing is the dev default working as designed. A box with no
    key runs Anchor with a prose profile that never refreshes and a discovery feed on the
    classical scorer, which is the same degradation a spent cap produces - so the
    unconfigured path is exercised by the same code every cap already exercises.

    It still declares the configured provider, so a box misconfigured to a provider that
    is not allowlisted is refused at boot rather than the first time it has a key.
    """

    def __init__(self, provider: str) -> None:
        self.provider = provider

    async def complete(self, prompt: Prompt, *, model: Model, dispatch: Dispatch) -> Completion:
        raise Unconfigured("no LLM provider credential is configured on this box")

    async def aclose(self) -> None:
        pass


def build_adapter(settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> Adapter:
    """The real client when a key is configured or a transport is injected."""
    if settings.llm_provider != "anthropic":
        # Refused here rather than silently unconfigured: a box naming a provider nobody
        # wrote an adapter for is misconfigured, and the allowlist below would pass it.
        raise ProviderRefused(f"no adapter exists for provider {settings.llm_provider!r}")
    if transport is None and settings.anthropic_api_key is None:
        return UnconfiguredAdapter(settings.llm_provider)
    return AnthropicAdapter(
        api_key=settings.anthropic_api_key or "unset",
        base_url=settings.anthropic_base_url,
        version=settings.anthropic_version,
        max_attempts=settings.llm_max_attempts,
        poll_seconds=settings.llm_batch_poll_seconds,
        timeout_seconds=settings.llm_batch_timeout_seconds,
        transport=transport,
    )


def build_llm(
    db: Database, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> Llm:
    """The seam as the worker process builds it, once, at startup."""
    return Llm(build_adapter(settings, transport), db, settings)


def _request_body(prompt: Prompt, model: Model) -> dict[str, Any]:
    """One Messages request. Structured output is what makes the schema the wire contract."""
    return {
        "model": model.id,
        "max_tokens": prompt.max_tokens,
        "system": prompt.system,
        "messages": [{"role": "user", "content": prompt.user}],
        "output_config": {"format": {"type": "json_schema", "schema": prompt.schema}},
    }


def _batch_message(row: dict[str, Any]) -> dict[str, Any]:
    """One batch result row, unwrapped. Anything but a success is the provider failing."""
    result = row.get("result") or {}
    if result.get("type") != "succeeded":
        raise ProviderUnavailable(f"the batched request came back {result.get('type')!r}")
    return dict(result.get("message") or {})


def _completion(message: dict[str, Any]) -> Completion:
    """The answer's text and what it cost. A message with no text block is a refusal."""
    usage = message.get("usage") or {}
    text = next(
        (
            str(block.get("text") or "")
            for block in message.get("content") or []
            if block.get("type") == "text"
        ),
        None,
    )
    if text is None:
        raise BadAnswer(f"the provider returned no text ({message.get('stop_reason')!r})")
    return Completion(
        text=text,
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
    )


def _retry_after(response: httpx.Response) -> float:
    try:
        return max(0.0, float(response.headers.get("Retry-After", "")))
    except ValueError:
        return DEFAULT_RETRY_AFTER
