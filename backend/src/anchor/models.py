import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from anchor.db import Base


class CriteriaFrequency(enum.StrEnum):
    """How often the owner wants the optional criteria bonus card after a placement.

    ``adaptive`` is the default and one option among the manual ones: it reads the
    owner's engagement with recent offers and moves the gap itself, which is what the
    spec asks for. The manual levels are fixed gaps, and ``off`` is a complete switch -
    no card is offered and no offer is recorded.
    """

    adaptive = "adaptive"
    often = "often"
    sometimes = "sometimes"
    rarely = "rarely"
    off = "off"


class Account(Base):
    """A registered user of Anchor: credentials and email-verification state.

    An unverified account is fully inert, and the account row is the only row it may
    have, so the pending verification token lives here (hashed) rather than in a table
    of its own. The demo account has no password hash and can never log in.
    """

    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_token_hash: Mapped[str | None] = mapped_column(String(64))
    verification_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_demo: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    criteria_frequency: Mapped[CriteriaFrequency] = mapped_column(
        Enum(CriteriaFrequency, name="criteria_frequency"),
        server_default=CriteriaFrequency.adaptive.value,
        nullable=False,
    )
    """Anchor's one owner preference, so it sits on the account rather than in a settings
    table of its own - a table would be one row per account forever, holding one enum."""
    qualities_picked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """When the owner last answered the quality picker. None means never.

    Not derivable from the constraints: answering the picker with nothing ticked is a
    real answer, and it writes no rows at all. Without this the account that ticked
    nothing and the account that never opened the picker would look identical, and Anchor
    would keep pre-ticking guesses over an answer the owner already gave.
    """
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    @property
    def verified(self) -> bool:
        return self.verified_at is not None


class AuthSession(Base):
    """A server-side login session; the browser holds only its token in an httpOnly cookie."""

    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LlmOperation(enum.StrEnum):
    """Anchor's four LLM jobs, which are the whole surface of the seam (architecture.md).

    The seam is operations-shaped rather than a generic prompt wrapper, so this enum is
    the closed list of things Anchor is ever willing to spend a provider call on - and,
    being the ledger's own column, it is also the list spend can ever be attributed to.
    """

    rerank_candidates = "rerank_candidates"
    regenerate_prose_profile = "regenerate_prose_profile"
    tag_film_qualities = "tag_film_qualities"
    suggest_qualities = "suggest_qualities"


class SpendLedgerEntry(Base):
    """One LLM call's cost, appended and never rewritten.

    The ledger is what makes the two monthly caps enforceable: the seam sums this table
    month-to-date before every dispatch, per account and platform-wide, and declines
    rather than spends past either (architecture.md). A row is written for every call
    that reached a provider, including one whose answer turned out to be unusable -
    the tokens were bought either way, and a ledger that only records useful calls
    would under-report the bill by exactly the amount nobody meant to spend.

    ``account_id`` is the scope: an account's own work, or NULL for shared work like
    quality tags, which are account-independent and paid for once for everybody.

    Cost is stored in millionths of a dollar rather than as a float, because it is
    summed against a cap and money that is added up must not drift. A cheap-tier call
    is a few thousand of these, so the unit is resolution rather than pedantry.

    Deleting an account takes its rows with it, like every other account-realm table.
    Its spend then leaves the global month-to-date sum too, which is the honest reading:
    the realm wipe means the account's history is gone, not archived elsewhere.
    """

    __tablename__ = "spend_ledger_entries"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    """Whose budget this call is against. NULL is the shared scope: work for everyone."""
    operation: Mapped[LlmOperation] = mapped_column(
        Enum(LlmOperation, name="llm_operation"), nullable=False
    )
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    """The provider model id, verbatim: what the cost below was priced against."""
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_micros: Mapped[int] = mapped_column(Integer, nullable=False)
    """Millionths of a US dollar, computed from the tokens at the configured tier prices."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class Film(Base):
    """A film in the shared catalog, as one bundled TMDB call gave it.

    Account operations never touch this table: it is shared across every account and
    refilled from TMDB, so it carries no ownership. Only image *paths* are stored -
    the bytes stay on TMDB's CDN (ADR 0003) - and ``fetched_at`` is what the rolling
    re-sync measures staleness against.

    Every column but one is TMDB's answer, verbatim. ``tagged_at`` is the exception and
    is deliberately not: it is the once-ever marker for the shared quality tags below,
    and it lives here because a film with no tags is a real answer that must be told
    apart from a film nobody has asked about yet. The rolling re-sync leaves it alone -
    a tag is a fact about the film rather than about the metadata, so re-fetching the
    metadata is not a reason to buy the tags a second time.
    """

    __tablename__ = "films"

    tmdb_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    release_year: Mapped[int | None] = mapped_column(Integer, index=True)
    overview: Mapped[str] = mapped_column(String, nullable=False, server_default="")
    poster_path: Mapped[str | None] = mapped_column(String)
    backdrop_path: Mapped[str | None] = mapped_column(String)
    runtime: Mapped[int | None] = mapped_column(Integer)
    genres: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    keywords: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    credits: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    vote_average: Mapped[float] = mapped_column(Float, nullable=False)
    vote_count: Mapped[int] = mapped_column(Integer, nullable=False)
    original_language: Mapped[str | None] = mapped_column(String(16))
    """TMDB's ISO code for the language the film was made in.

    Stored for exactly one reader: a profile constraint with a language footprint is
    enforced mechanically in the discovery prefilter (taste-profile.md), and a rule about
    languages needs a column that names one. Nullable because the catalog predates it.
    """
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    tagged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """When the shared quality tags were computed for this film, or NULL for never.

    The whole of "once per film ever": the tagging job reads this before it spends, and
    a film that came back with no tags at all is stamped just the same, so the answer
    "this film is not notable for any of them" is bought once rather than every time
    somebody places it.
    """


class QualityTag(Base):
    """An account-independent marker that a film is known for a vocabulary quality.

    Shared catalog, not account realm: a tag is a fact about the film rather than about
    anybody's taste, so it is bought once for everybody, its ledger row carries no
    account, and deleting an account takes none of it with them (architecture.md).

    ``quality`` is a name from :data:`BUILT_IN_QUALITIES` and nothing else. Tags draw
    from the closed built-in vocabulary only (taste-profile.md), which is what makes them
    shareable at all - a custom quality belongs to one account's list, so it could never
    be a fact about the film, and it reaches a criteria question only through the
    rotation fallback. The seam already filters a provider's answer to the vocabulary it
    offered; storing the name rather than a foreign key is what keeps this table free of
    any account's list.
    """

    __tablename__ = "quality_tags"

    film_id: Mapped[int] = mapped_column(
        ForeignKey("films.tmdb_id", ondelete="CASCADE"), primary_key=True
    )
    quality: Mapped[str] = mapped_column(String(64), primary_key=True)
    """One of :data:`BUILT_IN_QUALITIES`, verbatim."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LifecycleState(enum.StrEnum):
    """A film's one exclusive state within an account; untracked films have no row at all."""

    backlog = "backlog"
    watched_unrated = "watched_unrated"
    rated = "rated"


class TierZone(enum.StrEnum):
    """Which half of the ranked tier a seat is in.

    The up-next zone is a real "watch these next" statement, so its order is strict; the
    pool is the rest of the top thirty and its order floats freely (watchlist.md).
    """

    up_next = "up_next"
    pool = "pool"


class Unlock(enum.StrEnum):
    """The two readiness unlocks, which are the only things that ever get a nav dot."""

    discovery = "discovery"
    """Lit at readiness *forming*."""
    watchlist = "watchlist"
    """Lit at readiness *ready*, when the ranked tier appears."""


class UnlockMark(Base):
    """One account's one-time dot for one unlock; absence is the locked state.

    The dot is the only nav-level marker in the whole product and it fires once ever
    (surfacing.md), which is precisely the kind of fact that cannot be derived: readiness
    is a pure function of the evidence and would light the dot again on every read.

    A row appears when the bar is crossed and carries ``seen_at`` once the owner has
    visited the screen it points at, the way a warmup mark does: a state gains meaning by
    appearing, so an account that has crossed nothing owns no rows at all. An import that
    clears both bars at once writes both rows in the same breath, which is what earns it
    both dots (onboarding-and-import.md).
    """

    __tablename__ = "unlock_marks"
    __table_args__ = (UniqueConstraint("account_id", "unlock"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    unlock: Mapped[Unlock] = mapped_column(Enum(Unlock, name="unlock"), nullable=False)
    armed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """When the owner first arrived at the unlocked screen. None while the dot is showing."""


class AccountFilm(Base):
    """One (account, film) pair, holding that film's lifecycle state in that account."""

    __tablename__ = "account_films"
    __table_args__ = (UniqueConstraint("account_id", "film_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    film_id: Mapped[int] = mapped_column(
        ForeignKey("films.tmdb_id", ondelete="RESTRICT"), index=True, nullable=False
    )
    state: Mapped[LifecycleState] = mapped_column(
        Enum(LifecycleState, name="lifecycle_state"), nullable=False
    )
    rate_later: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    """The rate-later seat: meaningful only while the state is watched-unrated."""
    last_synced_rating: Mapped[float | None] = mapped_column(Float)
    """What Letterboxd holds for this film, as far as Anchor knows.

    Meaningful only on the rated state. The seed import initialises it from the export
    and nothing but the owner marking the film synced ever writes it again, so the sync
    list can be derived - never stored - as the films whose band has moved off it.
    """
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # --- Tier bookkeeping ---
    #
    # The ranked tier is persisted visible state hanging off backlog account-films, never
    # derived at read time (data-model.md), so it lives here rather than in a table of its
    # own: pin and veto apply to any backlog film whether or not it holds a seat, and the
    # cooldown marks have to outlive the seat they were earned by.

    tier_zone: Mapped[TierZone | None] = mapped_column(Enum(TierZone, name="tier_zone"))
    """The seat this film holds, or None for a backlog film the tier does not hold."""
    tier_position: Mapped[int | None] = mapped_column(Integer)
    """Rank within the whole tier, best first; pins occupy the front of the up-next zone."""
    tier_entered_watch: Mapped[int | None] = mapped_column(Integer)
    """The watch clock when this seat was taken.

    Two measures are read off it rather than counted alongside it. Staleness is the
    watches this film survived without being picked - which is exactly the watches since
    it sat down, because picking a tier film means watching it and a watched film is not
    in the backlog at all. And the enter cooldown, the "no immediate drops" half of the
    damping, is the same subtraction against a smaller number. A stored counter would be
    a second copy of one fact, free to disagree with it.
    """
    tier_reentry_watch: Mapped[int | None] = mapped_column(Integer)
    """The watch clock before which this film may not take a seat again: no bounce-backs.

    Set when the engine drops a film or the owner says not-now, and deliberately not when
    a veto pushes it out - lifting a veto is an owner action and answers immediately.
    """
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """When the owner pinned this film. Pins sit above the engine's picks in pin order."""
    vetoed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """When the owner barred this film from the tier. Reversible, and never distaste."""


class TierState(Base):
    """The account-level half of the tier bookkeeping: what the last refresh saw.

    There is no staged next tier (data-model.md). A session boundary is a moment rather
    than a record, so what is stored is not a plan but the fingerprint of the inputs the
    one persisted tier was last computed against - the fit it was scored with, and the
    watch clock its cooldowns were measured against. A refresh whose fingerprint has not
    moved has nothing to do, which is what keeps re-reading the screen from quietly
    spending another swap budget under the owner's cursor.
    """

    __tablename__ = "tier_states"
    __table_args__ = (UniqueConstraint("account_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    refreshed_trained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """The fit the seated tier was scored with; None where it was scored without one."""
    refreshed_watch_clock: Mapped[int | None] = mapped_column(Integer)
    """The watch clock the last refresh ran at, and None until one ever has.

    Nullable rather than zero-defaulted so that "never refreshed" is a state of its own.
    A brand-new account has no fit and no watches, and a zero here would read as a tier
    already up to date with exactly that - which is how an account can sit at *ready*
    looking at an empty tier that nothing will ever fill.
    """
    due: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    """The next boundary has work to do that the fingerprint cannot see.

    An override changes who is *eligible* without touching the fit or the clock: lifting
    a veto puts a film back in the running, and unpinning hands a seat back to the engine.
    The immediate half of both already happened; this is what makes the engine reconsider
    the rest of the list at the next boundary rather than at the next request.
    """


BANDS: tuple[float, ...] = (5.0, 4.5, 4.0, 3.5, 3.0, 2.5, 2.0, 1.5, 1.0, 0.5)
"""The ten half-star bands, best first: the fixed vocabulary a rating is drawn from.

In code rather than in a table for the same reason the quality vocabulary is: it is
account-independent, closed, and never changes at runtime (data-model.md). A band is
also a row of the wall, so this tuple is the wall's order as well as the scale's.
"""


class Placement(Base):
    """Where one rated film sits: its band, and its rank inside that band.

    The ordering is the set of an account's placements, read band by band and rank by
    rank (ADR 0001, ADR 0013). It is explicit persisted state: the band is the rating
    the owner chose and the rank is where the owner put the film, or where the default
    order seated it until they move it. Nothing derives either, and nothing but the
    owner's own picks, moves, re-rates and marks ever writes them.

    Ranks are dense from 1 within each band, so a move shifts the films it passes -
    which is why (account, band, rank) is deferred to commit time, the same way the
    old sequence's positions were: a shift is momentarily two films on one rank.
    """

    __tablename__ = "placements"
    __table_args__ = (
        UniqueConstraint("account_film_id"),
        UniqueConstraint(
            "account_id",
            "band",
            "rank",
            deferrable=True,
            initially="DEFERRED",
            name="uq_placements_account_id_band_rank",
        ),
        # The band is the rating, and the scale is closed: a value off the half-star
        # grid is not a rating anybody could have chosen.
        CheckConstraint(
            "band IN (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0)",
            name="ck_placements_band",
        ),
        CheckConstraint("rank >= 1", name="ck_placements_rank"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    account_film_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("account_films.id", ondelete="CASCADE"), nullable=False
    )
    band: Mapped[float] = mapped_column(Float, nullable=False)
    """One of the ten half-star values: the film's rating, stored because the owner chose it."""
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    """Position within the band, 1 the best. Dense: a band's ranks run 1..n with no gaps."""
    anchored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """When the owner marked this film an anchor, or None while it is not one.

    A timestamp rather than a flag because the exemplar set caps a large pool to a few
    per band, most recently marked first (taste-profile.md), and "most recently" needs a
    moment to read. Cleared by retiring the mark and by any write that carries the film
    into another band, which is what makes "an anchor is always in the band it was
    marked in" true by construction rather than by policing.
    """
    placed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    """The last placement or re-rate: the "recently rated" clock."""
    moved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """The last move, and None while the film still holds the rank the default order gave it."""


class ComparisonKind(enum.StrEnum):
    """The log's three row types (rating-system.md, "The comparison log").

    A band comparison and a band pick are the two halves of rating a film: the pick is
    the answer - "this film is a 4.0" - and a comparison is one of the questions that
    narrowed a range down to it, set against an anchor or a stand-in.
    """

    band_comparison = "band_comparison"
    """The film being rated against one film standing for a band: better, worse, same, skip."""
    band_pick = "band_pick"
    """The band chosen: outright, at the boundary question, or as a range's last resort."""
    criteria = "criteria"
    """Which of two films had the better quality. Feeds the taste profile only (ADR 0007)."""


class ComparisonVerdict(enum.StrEnum):
    """A comparison's four answers. ``skip`` records no judgment, on purpose.

    A criteria row is born ``skip`` - the offer was made and nothing has been said about
    it yet - and stays that way if the owner dismisses the card or simply walks off,
    which the spec requires be recorded identically.
    """

    a = "a"
    b = "b"
    tied = "tied"
    skip = "skip"


class ComparisonContext(enum.StrEnum):
    """The moment that produced a judgment."""

    placement = "placement"
    """The band picker, run on a film that was not rated."""
    re_rate = "re_rate"
    """The band picker, run again on a rated film from its page or a rewatch."""
    warmup = "warmup"
    spontaneous = "spontaneous"
    """A criteria session, opened from a film's own page."""
    seed_import = "seed_import"
    """The one-time Letterboxd import: its ratings count as the owner's band picks."""


class ComparisonLogEntry(Base):
    """One judgment, appended and never edited (ADR 0010: evidence, not an event source).

    No row here has a status. A judgment the ordering has since been moved past is not
    flagged or superseded: whoever reads it reads it against the ordering as it stands,
    and the ordering wins (ADR 0013). The account-realm wipe is the one thing that
    removes a row.
    """

    __tablename__ = "comparison_log_entries"
    __table_args__ = (
        # Every judgment answers exactly one kind of question, so exactly one of the two
        # answer columns is filled. Enforced here rather than left to the callers: a row
        # with neither is a judgment that says nothing, and a row with both is two
        # judgments wearing one timestamp.
        CheckConstraint(
            "(band IS NULL) <> (verdict IS NULL)",
            name="ck_comparison_log_entries_one_answer",
        ),
        # A criteria row is a judgment *about a quality*, so it is meaningless without
        # one; every other kind is a judgment about overall betterness, where naming a
        # quality would claim the owner said something they did not.
        CheckConstraint(
            "(quality_id IS NOT NULL) = (kind = 'criteria')",
            name="ck_comparison_log_entries_criteria_quality",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[ComparisonKind] = mapped_column(
        Enum(ComparisonKind, name="comparison_kind"), nullable=False
    )
    subject_film_id: Mapped[int] = mapped_column(
        ForeignKey("films.tmdb_id", ondelete="RESTRICT"), index=True, nullable=False
    )
    """Whose moment this was: "the placement of film X"."""
    film_a_id: Mapped[int] = mapped_column(
        ForeignKey("films.tmdb_id", ondelete="RESTRICT"), nullable=False
    )
    film_b_id: Mapped[int | None] = mapped_column(ForeignKey("films.tmdb_id", ondelete="RESTRICT"))
    """The other film, or None where the judgment involved one: a plain band pick."""
    verdict: Mapped[ComparisonVerdict | None] = mapped_column(
        Enum(ComparisonVerdict, name="comparison_verdict")
    )
    """How a comparison was answered. None on a band judgment, whose answer is ``band``."""
    band: Mapped[float | None] = mapped_column(Float)
    """The band a band judgment asserts. None on a comparison, whose answer is ``verdict``."""
    quality_id: Mapped[uuid.UUID | None] = mapped_column(
        # Deferred rather than RESTRICT, for the reason a placement's slot is: the guard
        # wanted is "a quality is never dropped out from under a question that was asked
        # about it", but account deletion cascades into both tables in whatever order it
        # likes. Checking at commit refuses the bug and allows the deletion.
        ForeignKey(
            "quality_list_entries.id",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        index=True,
    )
    """Which quality a criteria row asked about. None on every other kind."""
    context: Mapped[ComparisonContext] = mapped_column(
        Enum(ComparisonContext, name="comparison_context"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Dismissal(Base):
    """The owner's "not interested" on a discovery suggestion (discovery.md).

    Deliberately orthogonal to the lifecycle state: a dismissed film can later be
    hand-added or watched, at which point the suppression is moot - the feed never
    suggests a tracked film anyway - but the record stays, because the dismissal is a
    fact about their taste and the accumulated pattern is the one queue signal in Anchor
    that feeds the prose profile (ADR 0006).

    Permanent until lifted, and lifting stamps rather than deletes, the way a profile
    constraint does: the owner changing their mind is itself evidence. The feed only ever
    reads the unlifted ones. Rows are written by the feed's actions (#39); what lives
    here is the invariant the shelf is built under - only untracked, undismissed films
    are ever suggested - and the column that lets it be enforced.
    """

    __tablename__ = "dismissals"
    __table_args__ = (UniqueConstraint("account_id", "film_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    film_id: Mapped[int] = mapped_column(
        ForeignKey("films.tmdb_id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    lifted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """When the owner took it back. None means the film is still suppressed."""


class WatchStanding(enum.StrEnum):
    """Where the film stood on the watchlist when the watch was logged.

    Everything is plain backlog until the ranked tier exists (#33); a pinned film counts
    as the owner's pick, never the engine's.
    """

    up_next = "up_next"
    pool = "pool"
    pinned = "pinned"
    plain_backlog = "plain_backlog"


class WatchOrigin(enum.StrEnum):
    """How the film reached the owner's world. Only hand-added exists before #29 and #32."""

    discovery_accept = "discovery_accept"
    hand_added = "hand_added"
    import_seeded = "import_seeded"


class RewatchOutcome(enum.StrEnum):
    """The still-feel-the-same answer: the three the data model names.

    Offered once, at the rewatch moment, and never chased: "skipped" is a first-class
    answer rather than a missing one, because the question is an offer (rating-system.md).
    """

    confirmed = "confirmed"
    re_rated = "re_rated"
    skipped = "skipped"


class WatchEvent(Base):
    """One timestamped watch, appended per account; history, not truth about watched-ness.

    The standing and origin stamps are capture-or-lose-forever (evaluation.md): tier
    membership churns and keeps no history, so provenance is recorded at watch time and
    is never reconstructable afterwards. The account's watch clock is the count of these.
    """

    __tablename__ = "watch_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    film_id: Mapped[int] = mapped_column(
        ForeignKey("films.tmdb_id", ondelete="RESTRICT"), index=True, nullable=False
    )
    watched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    standing: Mapped[WatchStanding] = mapped_column(
        Enum(WatchStanding, name="watch_standing"), nullable=False
    )
    origin: Mapped[WatchOrigin] = mapped_column(
        Enum(WatchOrigin, name="watch_origin"), nullable=False
    )
    rewatch: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    """The owner had seen this film before. Imported diary rows carry Letterboxd's flag."""
    rewatch_outcome: Mapped[RewatchOutcome | None] = mapped_column(
        Enum(RewatchOutcome, name="rewatch_outcome")
    )
    """How the still-feel-the-same question was answered, on a rewatch that asked it.

    None means the question is still open, which is what the film page reads to know it
    has one to ask. An imported diary rewatch is never asked, so it is None forever -
    the offer belongs to the moment, and the moment is long past.
    """


BUILT_IN_QUALITIES: tuple[str, ...] = (
    "Acting",
    "Screenplay",
    "Direction",
    "Shots",
    "Score",
    "Message",
    "Tension",
    "Pacing",
    "Emotional impact",
    "Ending",
    "Humor",
    "Rewatchability",
)
"""The closed built-in vocabulary: six craft qualities, then six feel ones.

It lives in code rather than in a table because it is account-independent and never
changes at runtime - it is the key space that quality tags and seeded list entries
reference (data-model.md). Craft and feel only: mood-framed qualities ("which would you
rewatch tonight") are banned, and the timeless form is the admissible one.
"""


class QualityOrigin(enum.StrEnum):
    """Where a list entry came from. Downstream, the two are treated identically."""

    built_in = "built_in"
    custom = "custom"
    """An owner addition through the quality picker's free text (#35)."""


class QualityListEntry(Base):
    """One entry in the account's canonical quality list.

    One list per account behind both the quality picker and criteria questions. The
    built-in dozen is seeded at account creation and owner customs are added later; both
    are askable and treated identically everywhere downstream. The origin exists only so
    the shared, account-independent quality tags can join against built-in entries - a
    custom quality is never tagged, so it reaches a criteria question only through the
    rotation fallback.

    The system never invents entries: everything here is either the built-in vocabulary
    or something the owner typed.
    """

    __tablename__ = "quality_list_entries"
    __table_args__ = (
        # One list, so a name appears once on it: a second "Acting" would split the
        # rotation and the picker between two entries that mean the same thing.
        UniqueConstraint("account_id", "name", name="uq_quality_list_entries_account_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    origin: Mapped[QualityOrigin] = mapped_column(
        Enum(QualityOrigin, name="quality_origin"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    """Where the entry sits in the list, and so in the criteria rotation. Seeded entries
    take the vocabulary's own order; a custom entry lands after everything present."""
    suggested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """When Anchor last guessed the owner cares about this. None means it did not.

    Current-only, like the weight vector and for the same reason: the guess is derived
    from the account as it stands, every refresh rewrites the whole set, and a history of
    what Anchor used to think would be churn nothing reads.

    A guess is not a constraint. It pre-ticks a checkbox and nothing more, so it never
    reaches a regeneration as an instruction and stops being made once the owner answers.
    """
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ConstraintKind(enum.StrEnum):
    """The two shapes a durable owner-stated fact about their taste can take."""

    quality_pick = "quality_pick"
    """A quality the owner selected in the picker, naming one of their list entries."""
    prose_correction = "prose_correction"
    """A claim in the prose profile the owner thumbed down, held structurally."""


class ProfileConstraint(Base):
    """A durable owner-stated fact about their taste, stored structurally, never as text.

    Structural is the whole point. The prose profile is regenerated from scratch every
    time, so a correction kept as an edit to its text would be clobbered by the next
    regeneration; kept as a row, it is an input the regeneration has to respect, and it
    survives however many times the prose is rewritten.

    Lifting a constraint stamps ``lifted_at`` rather than deleting the row: the owner
    changing their mind is itself a fact about their taste, and an active constraint is
    simply one that has not been lifted.

    The picker that writes these arrives with #37; what lives here is the concept every
    regeneration already has to honour, and the read that makes it honour it.
    """

    __tablename__ = "profile_constraints"
    __table_args__ = (
        # Each kind is defined by exactly the field that carries its content, so a row
        # of one kind holding the other's payload is a constraint that says two things.
        CheckConstraint(
            "(quality_id IS NOT NULL) = (kind = 'quality_pick')",
            name="ck_profile_constraints_quality_pick",
        ),
        CheckConstraint(
            "(content IS NOT NULL) = (kind = 'prose_correction')",
            name="ck_profile_constraints_prose_correction",
        ),
        # One live pick per quality: a second would say the same thing twice, and every
        # regeneration would read the owner's one selection as two pieces of evidence.
        Index(
            "uq_profile_constraints_active_quality",
            "account_id",
            "quality_id",
            unique=True,
            postgresql_where=text("lifted_at IS NULL AND quality_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[ConstraintKind] = mapped_column(
        Enum(ConstraintKind, name="constraint_kind"), nullable=False
    )
    quality_id: Mapped[uuid.UUID | None] = mapped_column(
        # Deferred for the reason the comparison log's quality reference is: the guard
        # wanted is "a quality is never dropped out from under a constraint naming it",
        # but account deletion cascades into both tables in whatever order it likes.
        ForeignKey(
            "quality_list_entries.id",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        index=True,
    )
    """The quality a picker selection names. None on a prose correction."""
    content: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    """A thumbed-down claim, structured. None on a picker selection."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    lifted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """When the owner took it back. None means the constraint is active."""


class WeightVector(Base):
    """The numeric taste artifact: a learned weight per symbolic film feature (ADR 0004).

    Current-only, one row per account: it retrains from scratch on every ordering change
    in milliseconds, so a history of superseded fits would be churn nobody reads.

    ``weights`` is the fit itself, keyed by feature name so it can be read - "westerns
    +0.4, this director -0.2" is the whole point of choosing a linear scorer. ``space``
    is the vocabulary those names index: which symbols earned a column, what a present
    one is worth, and where the priors were centred. Both are needed to score a film,
    and neither means anything without the other, so they live and die together.
    """

    __tablename__ = "weight_vectors"
    __table_args__ = (UniqueConstraint("account_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    weights: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    space: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    training_pairs: Mapped[int] = mapped_column(Integer, nullable=False)
    """How many pairs the fit saw: the one number that says how much it can be trusted."""
    trained_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ExemplarRole(enum.StrEnum):
    """Why a film stands for the owner's taste: designated, or found at an end."""

    anchor = "anchor"
    best = "best"
    worst = "worst"


class Exemplar(Base):
    """One film standing for the owner's taste: an anchor, or an end of the ordering.

    Current-only and regenerated wholesale, never patched: the set is a mechanical
    reading of the anchors and the ordering, so recomputing it is cheaper than working
    out which rows a change invalidated - and cannot leave a stale row behind.

    ``rank`` orders the set within its role: the band's place on the scale for an anchor,
    and the distance from the end for an extreme, so ``best`` rank 0 is the owner's
    favourite film. It is what a prompt reads to say "because you loved X and Y".
    """

    __tablename__ = "exemplars"
    __table_args__ = (UniqueConstraint("account_id", "role", "rank"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    film_id: Mapped[int] = mapped_column(
        ForeignKey("films.tmdb_id", ondelete="RESTRICT"), nullable=False
    )
    role: Mapped[ExemplarRole] = mapped_column(
        Enum(ExemplarRole, name="exemplar_role"), nullable=False
    )
    band: Mapped[float | None] = mapped_column(Float)
    """The band an anchor is the exemplar of. None for an extreme, which stands for no band."""
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProseTrigger(enum.StrEnum):
    """What accumulated far enough to be worth a regeneration.

    The three middle ones are taste-profile.md's own list; ``first`` is the account
    earning its very first prose, and ``staleness`` is the backstop that catches an
    owner whose re-rating never lands enough *new* films to trip anything else.
    """

    first = "first"
    placements = "placements"
    anchors = "anchors"
    constraints = "constraints"
    staleness = "staleness"


class ProseProfileVersion(Base):
    """One prose regeneration, appended and never rewritten (data-model.md).

    The latest row is the live prose; the older ones are kept because the version number
    is not bookkeeping. Discovery caches its verdicts keyed by (film, profile version),
    so the bump *is* the cache invalidation and the batch-rerank trigger, which is why
    this is a real row rather than a counter on the account.

    The rest of the columns are the watermark: what the account looked like at the moment
    this text was written. The next regeneration is decided by comparing the account's
    current state against the newest watermark, which is what makes "accumulated change"
    a measurement rather than a guess - and what keeps prose off the per-comparison path,
    since a single answer moves no counter far enough to matter.

    Nothing here is denominated in calendar time. Spend is earned by engagement (ADR
    0004), so an account that has not been touched since March is exactly as due as it
    was in March: not at all.
    """

    __tablename__ = "prose_profile_versions"
    __table_args__ = (UniqueConstraint("account_id", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    """Monotonic per account, starting at 1. The key discovery caches verdicts against."""
    text: Mapped[str] = mapped_column(String, nullable=False)
    trigger: Mapped[ProseTrigger] = mapped_column(
        Enum(ProseTrigger, name="prose_trigger"), nullable=False
    )
    placements: Mapped[int] = mapped_column(Integer, nullable=False)
    """Rated films: what "N new placements" counts (taste-profile.md)."""
    judgments: Mapped[int] = mapped_column(Integer, nullable=False)
    """Every comparison-log row. The staleness backstop's measure, and it catches what
    the placement count cannot: a re-rate appends a pick without adding a film."""
    anchors: Mapped[str] = mapped_column(String(64), nullable=False)
    constraints: Mapped[str] = mapped_column(String(64), nullable=False)
    """The two set-shaped dimensions, as digests. Anchors and constraints are current-only
    - retiring a mark clears it - so there is no count that changing them reliably moves,
    and comparing digests catches a swap that leaves the count alone."""
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TasteMetrics(Base):
    """One retrain's numbers, appended and never rewritten (evaluation.md).

    The fast metric: held-out pairwise accuracy, plus the evidence counts that say what
    it is accuracy *of*. Fifty-eight percent means one thing over four hundred answered
    comparisons and nothing at all over nine, so the counts ride in the same row rather
    than being joined back to a moving present.

    Readiness is deliberately not a column. It is a pure function of these counts and the
    configured thresholds, so deriving it wherever it is needed keeps it what the spec
    says it is - never stored authoritatively - while the counts stay the durable record.
    """

    __tablename__ = "taste_metrics"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    held_out_accuracy: Mapped[float | None] = mapped_column(Float)
    """None where the account had no answered comparisons to hold any back from."""
    held_out_pairs: Mapped[int] = mapped_column(Integer, nullable=False)
    training_pairs: Mapped[int] = mapped_column(Integer, nullable=False)
    """The fit the accuracy was earned on. With ``held_out_pairs`` this partitions the
    evidence; the stored vector is trained on both halves and carries its own count."""
    rated_films: Mapped[int] = mapped_column(Integer, nullable=False)
    bands_spanned: Mapped[int] = mapped_column(Integer, nullable=False)
    """The two readiness dimensions, which are the whole of what gates the features."""
    band_comparisons: Mapped[int] = mapped_column(Integer, nullable=False)
    """Band comparisons the owner answered: the explicit half of the held-out slice.

    Not a readiness dimension (ADR 0013 removed the comparison bar) but still the context
    the accuracy has to be read against, since those answers are what it is measured on.
    """
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class FitBucket(enum.StrEnum):
    """How well the reranker thought a film fits this owner, coarsely.

    Internal, always (discovery.md): the bucket decides whether a film may reach the
    shelf and in which half of it, and only the explanation is ever shown. A bucket on a
    card would be a rating-shaped prediction about an unwatched film, which ADR 0005
    bars outright.
    """

    strong_fit = "strong_fit"
    plausible = "plausible"
    poor_fit = "poor_fit"
    """Cached as a negative: never shown, and never sent to the reranker again."""


class Verdict(Base):
    """The precomputed judgment behind one suggestion, keyed by profile version.

    Append-only across versions (data-model.md): a regeneration bumps the version and
    every verdict written against the old one stays exactly where it is. That is what
    makes the bump both the cache invalidation *and* the degraded-mode fallback - under a
    spend cap the feed still has last version's judgment of the film, and discovery.md
    says a stale verdict stays usable rather than being thrown away.

    ``rank`` is the listwise rank context: the film's place in the window it was judged
    in. It is not comparable across windows on its own, which is why the shelf orders by
    bucket first and breaks ties on the linear scorer.
    """

    __tablename__ = "verdicts"
    __table_args__ = (UniqueConstraint("account_id", "film_id", "profile_version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    film_id: Mapped[int] = mapped_column(
        ForeignKey("films.tmdb_id", ondelete="RESTRICT"), nullable=False
    )
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    """The prose profile version this judgment was made against; the whole cache key.

    Not a foreign key: the version is the account's own monotonic counter, and a verdict
    outliving the pruning of an ancient prose row is housekeeping rather than a
    contradiction.
    """
    fit: Mapped[FitBucket] = mapped_column(Enum(FitBucket, name="fit_bucket"), nullable=False)
    explanation: Mapped[str] = mapped_column(String, nullable=False)
    """The exemplar-grounded pitch, precomputed because no screen may wait on one."""
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Suggestion(Base):
    """One film currently on the feed's shelf.

    The shelf is persisted rather than computed per read, because it is the statement the
    owner acted on: engine-driven changes land at session boundaries only (discovery.md),
    and a list recomputed on every request would move under their cursor.

    **Invariants**: every suggestion points at a verdict, so a film with no verdict can
    never reach the shelf (the never-pad rule); only untracked, undismissed films are
    suggested; the shelf runs short rather than pad.
    """

    __tablename__ = "suggestions"
    __table_args__ = (
        UniqueConstraint("account_id", "film_id"),
        # Deferred like the ordering's positions: rebuilding the shelf rewrites the whole
        # run of them, and mid-rewrite two rows momentarily share a position.
        UniqueConstraint(
            "account_id",
            "position",
            deferrable=True,
            initially="DEFERRED",
            name="uq_suggestions_account_id_position",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    film_id: Mapped[int] = mapped_column(
        ForeignKey("films.tmdb_id", ondelete="RESTRICT"), nullable=False
    )
    verdict_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("verdicts.id", ondelete="CASCADE"), nullable=False
    )
    """The judgment this card is standing on, and where its pitch is read from."""
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    """Dense from 0, best first. Position is the entire public statement (ADR 0005)."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FeedState(Base):
    """The per-account bookkeeping the feed's economy runs on.

    One column so far, and it is a spend gate: a restock sources a few hundred candidates
    from TMDB and reranks the ones it has no verdict for, so it may not run again simply
    because a screen was loaded twice. Stamping the profile version it ran for makes the
    pipeline idempotent per version - which is exactly the granularity the verdict cache
    is keyed at, so a restock that would find nothing new to judge never starts.
    """

    __tablename__ = "feed_states"
    __table_args__ = (UniqueConstraint("account_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    restocked_profile_version: Mapped[int | None] = mapped_column(Integer)
    """The version the last restock ran for; None until one ever has."""
    restocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImportStatus(enum.StrEnum):
    """How far the one-time seed import has got."""

    matching = "matching"
    """Rows are parsed and the matcher is still working through them."""
    complete = "complete"
    """Every row has reached a match state; whatever is left is the owner's to resolve."""


class Import(Base):
    """The account's seed import: which export it came from, and how far it has got.

    At most one per account, enforced here rather than by convention: importing again is
    a hard reset that wipes the account realm first and rebuilds from the new export
    alone, so a second row could only mean a merge, and there is never a merge.
    """

    __tablename__ = "imports"
    __table_args__ = (UniqueConstraint("account_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    """The uploaded file's name, which is the only identity an export has."""
    status: Mapped[ImportStatus] = mapped_column(
        Enum(ImportStatus, name="import_status"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ImportRowKind(enum.StrEnum):
    """Which CSV a row came out of, which is what says what binding it does.

    The five that matter; every other file in the export is discarded unread.
    """

    rating = "rating"
    watchlist = "watchlist"
    watched = "watched"
    diary = "diary"
    profile_favorite = "profile_favorite"


class ImportRowState(enum.StrEnum):
    """Whether a row found its film, and who decided.

    ``pending`` is the one state data-model.md does not name, because it never outlives
    the matching job: it is a row the matcher has not reached yet.
    """

    pending = "pending"
    auto_matched = "auto_matched"
    """The matcher was sure enough to bind without asking."""
    review_pending = "review_pending"
    """Candidates exist but none dominates, so the owner picks."""
    bound = "bound"
    """The owner bound it: from the review screen, a manual search, or the rescue."""
    unmatched_open = "unmatched_open"
    """Nothing matched. It affects nothing and stays on the list indefinitely."""
    dismissed = "dismissed"
    """The owner gave up on it for good."""


class ImportRow(Base):
    """One CSV line that matters, and what became of it.

    The raw line is kept rather than discarded once bound: an unmatched row has nothing
    but its name and year to be found again by, and the review screen is answering
    "which film is this line?" long after the file itself is gone.
    """

    __tablename__ = "import_rows"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    import_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("imports.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[ImportRowKind] = mapped_column(
        Enum(ImportRowKind, name="import_row_kind"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    """The film's name exactly as Letterboxd wrote it, before any normalization."""
    year: Mapped[int | None] = mapped_column(Integer)
    letterboxd_uri: Mapped[str | None] = mapped_column(String(500))
    """The boxd.it short link: what the per-row rescue resolves through."""
    rating: Mapped[float | None] = mapped_column(Float)
    """The owner's half-star value, on a ratings.csv row and nowhere else."""
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """When the row's own event happened: watched, added to the watchlist, or rated.

    Letterboxd exports dates in New Zealand time, so a day of skew is possible and is
    accepted rather than corrected - nothing in Anchor is denominated in calendar time.
    """
    rewatch: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    state: Mapped[ImportRowState] = mapped_column(
        Enum(ImportRowState, name="import_row_state"), nullable=False
    )
    film_id: Mapped[int | None] = mapped_column(ForeignKey("films.tmdb_id", ondelete="RESTRICT"))
    """The film the row resolved to, once it has one."""
    candidates: Mapped[list[int]] = mapped_column(
        ARRAY(Integer), nullable=False, server_default="{}"
    )
    """The films the review screen offers, best-known first: ranked by popularity."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WarmupMark(enum.StrEnum):
    """One thing the owner did with onboarding that leaves no other trace.

    Everything else the warmup shows is derived - which bands have anchors, how many
    comparisons are answered, what is in the backlog - so the only facts worth a row are
    the ones whose whole content is "the owner moved past this and does not want asking
    again". A skip is exactly that: it records no judgment (onboarding-and-import.md).
    """

    entered = "entered"
    """The entry fork has been answered, whichever way. It is never asked twice."""
    anchors = "anchors"
    """Marking skipped: for one band with a ``band``, for the phase without one."""
    rating = "rating"
    """The fresh fill's rate-some-films phase skipped, however few were rated."""
    backlog = "backlog"
    """The backlog phase skipped."""
    dismissed = "dismissed"
    """The whole warmup put away. The app was fully usable before this and after it."""


class WarmupProgress(Base):
    """One warmup mark, appended per account; absence is the unanswered state.

    Deliberately not a per-account row with a column per step: a step gains meaning by
    appearing, so an account that never opened the warmup owns no rows at all, and the
    unverified-inert invariant holds without anything having to remember to skip it.
    """

    __tablename__ = "warmup_progress"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    mark: Mapped[WarmupMark] = mapped_column(Enum(WarmupMark, name="warmup_mark"), nullable=False)
    band: Mapped[float | None] = mapped_column(Float)
    """The band a skipped designation prompt was for; None for every other mark."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # Two partial indexes rather than one over both columns: in Postgres NULLs are
        # distinct, so a plain unique constraint would let "entered" be marked twice.
        Index(
            "uq_warmup_progress_phase",
            "account_id",
            "mark",
            unique=True,
            postgresql_where=text("band IS NULL"),
        ),
        Index(
            "uq_warmup_progress_band",
            "account_id",
            "mark",
            "band",
            unique=True,
            postgresql_where=text("band IS NOT NULL"),
        ),
    )
