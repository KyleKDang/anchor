import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
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
    """What produced the placement. Only completed placements exist before bands (#28)."""

    import_seeded = "import_seeded"
    early_bail = "early_bail"
    completed = "completed"


class Placement(Base):
    """Where one rated film sits, and how much that position is trusted.

    A rated film has exactly one placement and a placement's film is exactly one slot's
    member, so this row is also what makes a film rated. The rating itself is never
    stored: it derives from the slot's position against the dividers (#28).
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


class ComparisonKind(enum.StrEnum):
    """The log's row types; sliver and criteria answers ride here as typed siblings."""

    overall = "overall"
    sliver = "sliver"
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
    film_b_id: Mapped[int] = mapped_column(
        ForeignKey("films.tmdb_id", ondelete="RESTRICT"), nullable=False
    )
    verdict: Mapped[ComparisonVerdict] = mapped_column(
        Enum(ComparisonVerdict, name="comparison_verdict"), nullable=False
    )
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
