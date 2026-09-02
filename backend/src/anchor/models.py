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
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
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
