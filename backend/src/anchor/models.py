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


class WorkerProbe(Base):
    """A health-check round trip: the web process asks, the worker answers."""

    __tablename__ = "worker_probes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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


class Film(Base):
    """A film in the shared catalog, as one bundled TMDB call gave it.

    Account operations never touch this table: it is shared across every account and
    refilled from TMDB, so it carries no ownership. Only image *paths* are stored -
    the bytes stay on TMDB's CDN (ADR 0003) - and ``fetched_at`` is what the rolling
    re-sync measures staleness against.
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
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
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


class UnlockState(enum.StrEnum):
    """How far the Watchlist's one-time unlock dot has got.

    The dot is the only nav-level marker in the whole product and it fires once ever
    (surfacing.md), which is precisely the kind of fact that cannot be derived: readiness
    is a pure function of the evidence and would light the dot again on every read.
    """

    locked = "locked"
    pending = "pending"
    """Ready has been reached and the owner has not been to the Watchlist since."""
    seen = "seen"


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
    unlock_state: Mapped[UnlockState] = mapped_column(
        Enum(UnlockState, name="unlock_state"), server_default="locked", nullable=False
    )


class TieGroupSlot(Base):
    """One slot of the ordering: the films the owner has judged equal, at one position.

    The ordering is this table read in ``position`` order, best to worst (ADR 0001):
    explicit persisted state, never derived from the comparison log and never moved by
    the advisory math. Positions are dense and start at 0, so inserting a slot shifts
    everything below it down - which is why the uniqueness of (account, position) is
    deferred to commit time, since a shift is momentarily two slots on one position.
    """

    __tablename__ = "tie_group_slots"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "position",
            deferrable=True,
            initially="DEFERRED",
            name="uq_tie_group_slots_account_id_position",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    """0 is the owner's best film; a slot never sits empty."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PlacementTrust(enum.StrEnum):
    """How much a placement's position is trusted; the advisory math may graduate it."""

    provisional = "provisional"
    full = "full"


class PlacementProvenance(enum.StrEnum):
    """What produced the placement; import-seeded ones arrive with the seed import (#29)."""

    import_seeded = "import_seeded"
    early_bail = "early_bail"
    completed = "completed"


class Placement(Base):
    """Where one rated film sits, and how much that position is trusted.

    A rated film has exactly one placement and a placement's film is exactly one slot's
    member, so this row is also what makes a film rated. The rating itself is never
    stored: it derives from the slot's position against the dividers.
    """

    __tablename__ = "placements"
    __table_args__ = (UniqueConstraint("account_film_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    account_film_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("account_films.id", ondelete="CASCADE"), nullable=False
    )
    slot_id: Mapped[uuid.UUID] = mapped_column(
        # Deferred rather than RESTRICT: the guard wanted here is "a slot is never
        # dropped out from under its members", but the account-realm wipe deletes
        # slots and placements in one transaction, in whatever order the cascades
        # happen to fire. Checking at commit refuses the bug and allows the wipe.
        ForeignKey(
            "tie_group_slots.id",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        index=True,
        nullable=False,
    )
    trust: Mapped[PlacementTrust] = mapped_column(
        Enum(PlacementTrust, name="placement_trust"), nullable=False
    )
    provenance: Mapped[PlacementProvenance] = mapped_column(
        Enum(PlacementProvenance, name="placement_provenance"), nullable=False
    )
    placed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Divider(Base):
    """The stored boundary between two adjacent bands: at most nine per account.

    ``upper_band`` names the pair - the divider carrying 4.0 is the 4.0/3.5 boundary -
    and ``boundary`` is an index into the ordering: the slots above the divider are the
    ones at indices below it, and the slots from ``boundary`` down are below it. There
    is no row at all while a divider is unpinned, which is what makes a film's band
    honestly underivable rather than quietly guessed.

    A divider moves only as the direct consequence of a band judgment, and
    ``pinned_by_id`` is which one, so every position it has ever held is auditable back
    to the answer that put it there. Inserting a slot above a divider renumbers it, but
    that is not a move: it says exactly what it said before, about the same two slots.
    """

    __tablename__ = "dividers"
    __table_args__ = (UniqueConstraint("account_id", "upper_band"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    upper_band: Mapped[float] = mapped_column(Float, nullable=False)
    """The better of the two bands this divider separates; the worse is the next one down."""
    boundary: Mapped[int] = mapped_column(Integer, nullable=False)
    pinned_by_id: Mapped[uuid.UUID] = mapped_column(
        # Deferred for the same reason the placement's slot reference is: the guard
        # wanted is "a divider always names a judgment that exists", but the
        # account-realm wipe deletes the log and the dividers in one transaction, in
        # whatever order the cascades fire.
        ForeignKey(
            "comparison_log_entries.id",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=False,
    )
    """The band judgment that last moved this divider: what makes the move auditable."""
    moved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AnchorStatus(enum.StrEnum):
    """Whether a designation is the band's anchor, or only the intent behind a re-placement."""

    current = "current"
    intended = "intended"


class AnchorDesignation(Base):
    """The owner's canonical exemplar of a band - and the intent aiming at one.

    Current-only: retiring an anchor clears the row rather than closing it, so no
    designation history is kept, and clearing changes no rating and no divider.

    An ``intended`` row is not an anchor. It is the intent a designation-mismatch
    re-placement runs under, held here because that flow spans several requests and the
    placement search deliberately keeps no state of its own: losing the intent would
    silently cancel a designation the owner asked for. It becomes current if the film
    lands in the band and is dropped if it lands anywhere else, and either way the
    re-placement's own result stands.
    """

    __tablename__ = "anchor_designations"
    __table_args__ = (
        # At most one anchor per band. An intended designation is not an anchor yet, so
        # it deliberately does not contend with the current anchor of the band it aims at.
        Index(
            "uq_anchor_designations_current_band",
            "account_id",
            "band",
            unique=True,
            postgresql_where=text("status = 'current'"),
        ),
        # One film anchors one band: designating it elsewhere retires it here first.
        Index(
            "uq_anchor_designations_current_film",
            "account_id",
            "account_film_id",
            unique=True,
            postgresql_where=text("status = 'current'"),
        ),
        # One re-placement at a time, so the intent is per account rather than per band.
        Index(
            "uq_anchor_designations_intended",
            "account_id",
            unique=True,
            postgresql_where=text("status = 'intended'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    band: Mapped[float] = mapped_column(Float, nullable=False)
    account_film_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("account_films.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[AnchorStatus] = mapped_column(
        Enum(AnchorStatus, name="anchor_status"), nullable=False
    )
    designated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ComparisonKind(enum.StrEnum):
    """The log's row types; band and criteria answers ride here as typed siblings.

    ``sliver`` and ``band`` are both band judgments - "this film is a 4.0" - and differ
    only in how the question was put: a sliver answer is the owner picking which of two
    canonical exemplars the film sits closer to, so it names one; a band answer is a
    plain pick off a list of bands, which names none.
    """

    overall = "overall"
    sliver = "sliver"
    band = "band"
    criteria = "criteria"


class ComparisonVerdict(enum.StrEnum):
    """An overall comparison's four answers. ``skip`` records no judgment, on purpose."""

    a = "a"
    b = "b"
    tied = "tied"
    skip = "skip"


class ComparisonContext(enum.StrEnum):
    """The moment that produced a judgment. Only placement exists before drift (#31)."""

    placement = "placement"
    re_placement = "re_placement"
    keep_comparing = "keep_comparing"
    drift_check = "drift_check"
    warmup = "warmup"
    spontaneous = "spontaneous"
    seed_import = "seed_import"
    """The one-time Letterboxd import: its ratings count as the owner's band judgments."""


class ComparisonStatus(enum.StrEnum):
    """Whether a judgment still stands against the ordering."""

    active = "active"
    in_tension = "in_tension"
    superseded = "superseded"


class ComparisonLogEntry(Base):
    """One judgment, appended and never deleted (ADR 0010: evidence, not an event source).

    Nothing here is ever rewritten except ``status``, which is how a later resolution
    records that a judgment was settled against without erasing that it was made. The
    account-realm wipe is the one thing that removes a row.
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
    context: Mapped[ComparisonContext] = mapped_column(
        Enum(ComparisonContext, name="comparison_context"), nullable=False
    )
    status: Mapped[ComparisonStatus] = mapped_column(
        Enum(ComparisonStatus, name="comparison_status"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


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
    re_placed = "re_placed"
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


class DriftStage(enum.StrEnum):
    """How loud a flag is allowed to be. Escalation stops here: no auto-move, ever."""

    quiet = "quiet"
    """Thin evidence: the app may slip a targeted drift check into a comparison moment."""
    surfaced = "surfaced"
    """The owner sees it, and the film is benched as an opponent - a doubted ruler is bent."""


class DriftOutcome(enum.StrEnum):
    """What closed a flag. ``self_resolved`` is the one nobody chose."""

    re_placed = "re_placed"
    kept = "kept"
    re_pointed = "re_pointed"
    """The owner said the opponent is the misplaced one, so the tension moved to it."""
    self_resolved = "self_resolved"
    """The evidence stopped contradicting on its own, so the flag had nothing left to stand on."""


class DriftFlag(Base):
    """The per-film aggregation of in-tension judgments: drift, tracked where it lands.

    Drift is a condition of a *film*, not of a judgment, which is why this is a row of
    its own rather than a status on the log: several judgments can implicate one film,
    and the owner resolves the film once rather than each judgment separately.

    A flag never moves anything (ADR 0001). It escalates from quiet to surfaced, benches
    the film as an opponent, and offers the owner three choices - and that is the whole
    of its power. Closed flags are kept: the outcome is the record of what the owner
    decided, and the judgments themselves keep their own statuses in the log.
    """

    __tablename__ = "drift_flags"
    __table_args__ = (
        # At most one open flag per film. A second one could only ever say the same
        # thing, and the owner would have to resolve the same doubt twice.
        Index(
            "uq_drift_flags_open_film",
            "account_id",
            "account_film_id",
            unique=True,
            postgresql_where=text("closed_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    account_film_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("account_films.id", ondelete="CASCADE"), index=True, nullable=False
    )
    stage: Mapped[DriftStage] = mapped_column(Enum(DriftStage, name="drift_stage"), nullable=False)
    re_placing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """When the owner chose re-place, which is what makes the placement flow a re-placement.

    The placement search deliberately keeps no state of its own, so the one thing it
    cannot re-derive from the log is which flow the owner thinks they are in. This is
    that, and nothing more: it is cleared when the re-placement lands.
    """
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[DriftOutcome | None] = mapped_column(Enum(DriftOutcome, name="drift_outcome"))
    """Set exactly when ``closed_at`` is; an open flag has no outcome yet."""


class DriftEvidence(Base):
    """Which in-tension judgment hangs on which open flag.

    The pointer exists because a contradiction implicates *two* films and the flag sits
    on one of them - so "the in-tension judgments touching this film" is not the same
    set as "this flag's evidence", and re-pointing at the opponent moves a judgment from
    one flag to the other without changing the judgment at all.

    Rows live only as long as the flag is open: closing it drops them, because what
    became of each judgment is recorded where it belongs, on the judgment's own status
    in the append-only log. Nothing auditable is lost, and ``entry_id`` stays unique.
    """

    __tablename__ = "drift_evidence"
    __table_args__ = (UniqueConstraint("entry_id", name="uq_drift_evidence_entry_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    flag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("drift_flags.id", ondelete="CASCADE"), index=True, nullable=False
    )
    entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("comparison_log_entries.id", ondelete="CASCADE"), nullable=False
    )
    attached_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


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
    explicit_comparisons: Mapped[int] = mapped_column(Integer, nullable=False)
    settled_films: Mapped[int] = mapped_column(Integer, nullable=False)
    """Rated films whose position rests on the owner's own comparisons, not a seed or a bail."""
    bands_spanned: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


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
