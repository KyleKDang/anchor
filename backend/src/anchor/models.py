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
