"""Accounts and auth: signup, email verification, login, logout, account deletion.

Sessions are server-side rows; the browser holds only a random token in an httpOnly
cookie, and the row stores that token's hash. ``current_account`` is the one door every
authenticated endpoint hangs off, and a session can only be minted for a verified
account, so an unverified account can do nothing at all.
"""

import hashlib
import re
import secrets
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Self

import httpx
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.errors import ApiError
from anchor.mail import Mailer, verification_message
from anchor.models import Account, AuthSession
from anchor.ratelimit import limited
from anchor.settings import Settings

router = APIRouter(prefix="/api")

SESSION_COOKIE = "anchor_session"
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

hasher = PasswordHasher()
# Verified against when the email is unknown, so a login attempt costs the same either way.
_UNKNOWN_ACCOUNT_HASH = hasher.hash(secrets.token_urlsafe(32))


# --- Wire shapes ---


class Credentials(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not EMAIL_PATTERN.match(value):
            raise ValueError("not an email address")
        return value


class VerificationToken(BaseModel):
    token: str = Field(min_length=1, max_length=128)


class PasswordConfirmation(BaseModel):
    password: str = Field(max_length=128)


class AccountOut(BaseModel):
    id: uuid.UUID
    email: str
    verified: bool

    @classmethod
    def of(cls, account: Account) -> Self:
        return cls(id=account.id, email=account.email, verified=account.verified)


# --- Dependencies ---


def settings_of(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def mailer_of(request: Request) -> Mailer:
    return request.app.state.mailer  # type: ignore[no-any-return]


async def db_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.db.sessions() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(db_session)]
AppSettings = Annotated[Settings, Depends(settings_of)]


async def current_account(request: Request, db: DbSession) -> Account:
    """The account of the live session named by the cookie, or 401."""
    token = request.cookies.get(SESSION_COOKIE)
    if token is None:
        raise _unauthenticated()
    account = await db.scalar(
        select(Account)
        .join(AuthSession, AuthSession.account_id == Account.id)
        .where(AuthSession.token_hash == _digest(token), AuthSession.expires_at > _now())
    )
    if account is None:
        raise _unauthenticated()
    return account


CurrentAccount = Annotated[Account, Depends(current_account)]


# --- Endpoints ---


@router.post(
    "/auth/signup",
    status_code=201,
    dependencies=[limited("signup", lambda settings: settings.signup_rate_limit)],
)
async def signup(
    body: Credentials, db: DbSession, settings: AppSettings, request: Request
) -> AccountOut:
    """Register, or re-issue the verification link for a still-unverified email."""
    account = await db.scalar(select(Account).where(Account.email == body.email))
    if account is None:
        account = Account(email=body.email)
        db.add(account)
    elif account.verified or account.is_demo:
        raise ApiError(409, "email_taken", "An account with this email already exists.")

    token = secrets.token_urlsafe(32)
    account.password_hash = hasher.hash(body.password)
    account.verification_token_hash = _digest(token)
    account.verification_sent_at = _now()
    await db.flush()

    link = f"{settings.public_url}/verify?token={token}"
    try:
        await mailer_of(request).send(verification_message(account.email, link))
    except httpx.HTTPError as error:
        # The session rolls back with the request: no account without its link.
        raise ApiError(
            503, "mail_unavailable", "We could not send the verification email; try again soon."
        ) from error
    await db.commit()
    return AccountOut.of(account)


@router.post(
    "/auth/verify", dependencies=[limited("verify", lambda settings: settings.verify_rate_limit)]
)
async def verify(body: VerificationToken, db: DbSession, settings: AppSettings) -> AccountOut:
    account = await db.scalar(
        select(Account).where(Account.verification_token_hash == _digest(body.token))
    )
    if account is None:
        raise ApiError(400, "invalid_token", "This verification link is not valid.")
    assert account.verification_sent_at is not None
    if _now() > account.verification_sent_at + timedelta(hours=settings.verification_ttl_hours):
        raise ApiError(
            400, "expired_token", "This verification link has expired; sign up again for a new one."
        )

    account.verified_at = _now()
    account.verification_token_hash = None
    account.verification_sent_at = None
    await db.commit()
    return AccountOut.of(account)


@router.post(
    "/auth/login", dependencies=[limited("login", lambda settings: settings.login_rate_limit)]
)
async def login(
    body: Credentials, db: DbSession, settings: AppSettings, response: Response
) -> AccountOut:
    account = await db.scalar(select(Account).where(Account.email == body.email))
    if account is None or account.password_hash is None:
        _check_password(_UNKNOWN_ACCOUNT_HASH, body.password)
        raise _invalid_credentials()
    if not _check_password(account.password_hash, body.password):
        raise _invalid_credentials()
    if not account.verified:
        raise ApiError(
            403, "email_unverified", "Verify your email through the link we sent you first."
        )

    if hasher.check_needs_rehash(account.password_hash):
        account.password_hash = hasher.hash(body.password)
    token = secrets.token_urlsafe(32)
    ttl = timedelta(hours=settings.session_ttl_hours)
    db.add(AuthSession(token_hash=_digest(token), account_id=account.id, expires_at=_now() + ttl))
    await db.commit()

    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(ttl.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )
    return AccountOut.of(account)


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, db: DbSession, response: Response) -> None:
    """Revoke the cookie's session, if any, and clear the cookie; always succeeds."""
    token = request.cookies.get(SESSION_COOKIE)
    if token is not None:
        await db.execute(delete(AuthSession).where(AuthSession.token_hash == _digest(token)))
        await db.commit()
    _clear_cookie(response)


@router.get("/auth/me")
async def me(account: CurrentAccount) -> AccountOut:
    return AccountOut.of(account)


@router.delete("/account", status_code=204)
async def delete_account(
    body: PasswordConfirmation, account: CurrentAccount, db: DbSession, response: Response
) -> None:
    """Delete the account and, through cascading foreign keys, its whole realm."""
    if account.password_hash is None or not _check_password(account.password_hash, body.password):
        raise _invalid_credentials()
    await db.execute(delete(Account).where(Account.id == account.id))
    await db.commit()
    _clear_cookie(response)


# --- Helpers ---


def _now() -> datetime:
    return datetime.now(UTC)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _check_password(password_hash: str, password: str) -> bool:
    try:
        return hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def _clear_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def _unauthenticated() -> ApiError:
    return ApiError(401, "unauthenticated", "Log in to continue.")


def _invalid_credentials() -> ApiError:
    return ApiError(401, "invalid_credentials", "That email and password do not match.")
