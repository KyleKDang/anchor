"""Accounts and auth: signup, email verification, login, logout, account deletion.

Sessions are server-side rows; the browser holds only a random token in an httpOnly
cookie, and the row stores that token's hash. ``current_account`` is the one door every
authenticated endpoint hangs off, and it admits only a verified account, so an
unverified account can do nothing at all.

Verification takes the emailed token together with the password chosen at signup:
holding the link alone (a mailbox) or the password alone (whoever last signed the
address up) activates nothing, so an address cannot be taken over before its owner
verifies it.
"""

import hashlib
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Self

import httpx
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select

from anchor import qualities
from anchor.deps import AppMailer, AppSettings, DbSession
from anchor.errors import ApiError
from anchor.mail import verification_message
from anchor.models import Account, AuthSession
from anchor.ratelimit import limited
from anchor.settings import Settings

router = APIRouter(prefix="/api")

SESSION_COOKIE = "anchor_session"
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PASSWORD_MAX_LENGTH = 128

hasher = PasswordHasher()
# Verified against when the email is unknown, so a login attempt costs the same either way.
_UNKNOWN_ACCOUNT_HASH = hasher.hash(secrets.token_urlsafe(32))


# --- Wire shapes ---


def _normalize_email(value: str) -> str:
    value = value.strip().lower()
    if not EMAIL_PATTERN.match(value):
        raise ValueError("not an email address")
    return value


class SignupCredentials(BaseModel):
    email: Annotated[str, Field(max_length=320)]
    password: Annotated[str, Field(min_length=8, max_length=PASSWORD_MAX_LENGTH)]

    normalize_email = field_validator("email")(_normalize_email)


class LoginCredentials(BaseModel):
    """Login refuses any wrong password alike; the length rule is signup's alone."""

    email: Annotated[str, Field(max_length=320)]
    password: Annotated[str, Field(max_length=PASSWORD_MAX_LENGTH)]

    normalize_email = field_validator("email")(_normalize_email)


class Verification(BaseModel):
    token: Annotated[str, Field(min_length=1, max_length=128)]
    password: Annotated[str, Field(max_length=PASSWORD_MAX_LENGTH)]


class PasswordConfirmation(BaseModel):
    password: Annotated[str, Field(max_length=PASSWORD_MAX_LENGTH)]


class AccountOut(BaseModel):
    id: uuid.UUID
    email: str
    verified: bool
    demo: bool
    """The shared read-only demo account, whose wall has no edit mode (demo-account.md)."""

    @classmethod
    def of(cls, account: Account) -> Self:
        return cls(
            id=account.id, email=account.email, verified=account.verified, demo=account.is_demo
        )


# --- The door ---


async def current_account(request: Request, db: DbSession) -> Account:
    """The verified account of the live session named by the cookie, or 401."""
    token = request.cookies.get(SESSION_COOKIE)
    if token is None:
        raise _unauthenticated()
    account = await db.scalar(
        select(Account)
        .join(AuthSession, AuthSession.account_id == Account.id)
        .where(
            AuthSession.token_hash == _digest(token),
            AuthSession.expires_at > _now(),
            Account.verified_at.is_not(None),
        )
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
    body: SignupCredentials, db: DbSession, settings: AppSettings, mailer: AppMailer
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
        await mailer.send(verification_message(account.email, link))
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
async def verify(
    body: Verification, db: DbSession, settings: AppSettings, response: Response
) -> AccountOut:
    """Activate the account behind the emailed link, and log its owner in."""
    account = await db.scalar(
        select(Account).where(Account.verification_token_hash == _digest(body.token))
    )
    if account is None or account.verification_sent_at is None:
        raise ApiError(400, "invalid_token", "This verification link is not valid.")
    if _now() > account.verification_sent_at + timedelta(hours=settings.verification_ttl_hours):
        raise ApiError(
            400, "expired_token", "This verification link has expired; sign up again for a new one."
        )
    if account.password_hash is None or not _check_password(account.password_hash, body.password):
        raise _wrong_password()

    account.verified_at = _now()
    account.verification_token_hash = None
    account.verification_sent_at = None
    # The first rows the account is allowed to have. Until this moment it was inert and
    # the account record was the only row it could own (data-model.md), so account
    # creation - the point the quality list is spec'd to be seeded at - is here.
    await qualities.seed(db, account.id)
    await _open_session(db, account, settings, response)
    return AccountOut.of(account)


@router.post(
    "/auth/login", dependencies=[limited("login", lambda settings: settings.login_rate_limit)]
)
async def login(
    body: LoginCredentials, db: DbSession, settings: AppSettings, response: Response
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
    await _open_session(db, account, settings, response)
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
        raise _wrong_password()
    await db.execute(delete(Account).where(Account.id == account.id))
    await db.commit()
    _clear_cookie(response)


# --- Helpers ---


async def _open_session(
    db: DbSession, account: Account, settings: Settings, response: Response
) -> None:
    """Mint a session for ``account``, commit, and hand the browser its cookie."""
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


def _wrong_password() -> ApiError:
    return ApiError(401, "wrong_password", "That password is not correct.")
