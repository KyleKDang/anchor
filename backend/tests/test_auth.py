"""Accounts and auth: the flows a visitor walks from signup to a live session."""

import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from anchor import jobs
from anchor.main import create_app
from anchor.models import Account, AuthSession
from invariants import assert_realm_empty, assert_realm_wiped, realm_row_counts

EMAIL = "owner@example.com"
PASSWORD = "correct horse battery staple"


async def sign_up(client, resend, email=EMAIL, password=PASSWORD):
    response = await client.post("/api/auth/signup", json={"email": email, "password": password})
    assert response.status_code == 201, response.text
    return response.json()


async def verify(client, resend, email=EMAIL):
    token = resend.verification_token(email)
    response = await client.post("/api/auth/verify", json={"token": token})
    assert response.status_code == 200, response.text


async def log_in(client, email=EMAIL, password=PASSWORD):
    response = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response


async def test_visitor_signs_up_verifies_logs_in_and_logs_out(client, resend):
    account = await sign_up(client, resend)
    assert account == {"id": account["id"], "email": EMAIL, "verified": False}

    await verify(client, resend)

    response = await log_in(client)
    assert response.json() == {"id": account["id"], "email": EMAIL, "verified": True}
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("anchor_session=")
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Secure" in cookie

    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == EMAIL

    response = await client.post("/api/auth/logout")
    assert response.status_code == 204
    assert 'anchor_session=""' in response.headers["set-cookie"]

    assert (await client.get("/api/auth/me")).status_code == 401


async def test_unverified_account_is_inert(client, resend, db):
    account = await sign_up(client, resend)

    response = await client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "email_unverified"
    assert "set-cookie" not in response.headers
    assert (await client.get("/api/auth/me")).status_code == 401

    await assert_realm_empty(db, uuid.UUID(account["id"]))
    [message] = resend.sent_to(EMAIL)
    assert "/verify?token=" in message["text"]


async def test_signup_fails_whole_when_the_verification_mail_cannot_be_sent(client, resend, db):
    resend.down = True
    response = await client.post("/api/auth/signup", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "mail_unavailable"
    async with db.sessions() as session:
        assert await session.scalar(select(Account).where(Account.email == EMAIL)) is None

    resend.down = False
    await sign_up(client, resend)
    await verify(client, resend)
    await log_in(client)


async def test_verification_link_is_single_use(client, resend):
    await sign_up(client, resend)
    token = resend.verification_token(EMAIL)
    assert (await client.post("/api/auth/verify", json={"token": token})).status_code == 200

    response = await client.post("/api/auth/verify", json={"token": token})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_token"


async def test_verification_link_expires(client, resend, db):
    account = await sign_up(client, resend)
    async with db.sessions() as session:
        await session.execute(
            update(Account)
            .where(Account.id == uuid.UUID(account["id"]))
            .values(verification_sent_at=datetime.now(UTC) - timedelta(hours=25))
        )
        await session.commit()

    response = await client.post(
        "/api/auth/verify", json={"token": resend.verification_token(EMAIL)}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "expired_token"


async def test_signing_up_again_before_verifying_reissues_the_link(client, resend):
    await sign_up(client, resend)
    first_token = resend.verification_token(EMAIL)
    await sign_up(client, resend, password="a different password")
    second_token = resend.verification_token(EMAIL)
    assert second_token != first_token

    assert (await client.post("/api/auth/verify", json={"token": first_token})).status_code == 400
    assert (await client.post("/api/auth/verify", json={"token": second_token})).status_code == 200
    await log_in(client, password="a different password")


async def test_a_verified_email_cannot_be_signed_up_again(client, resend):
    await sign_up(client, resend)
    await verify(client, resend)

    response = await client.post("/api/auth/signup", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_taken"
    assert len(resend.sent_to(EMAIL)) == 1


async def test_wrong_password_and_unknown_email_are_refused_alike(client, resend):
    await sign_up(client, resend)
    await verify(client, resend)

    for email, password in [(EMAIL, "not the password"), ("nobody@example.com", PASSWORD)]:
        response = await client.post("/api/auth/login", json={"email": email, "password": password})
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_credentials"
        assert "set-cookie" not in response.headers


async def test_email_is_normalized_and_password_length_is_bounded(client, resend):
    await sign_up(client, resend, email="  Owner@Example.COM ")
    assert resend.sent_to(EMAIL)
    await verify(client, resend)
    await log_in(client, email="OWNER@example.com")

    short = await client.post(
        "/api/auth/signup", json={"email": "x@example.com", "password": "short"}
    )
    assert short.status_code == 422


async def test_passwords_are_stored_as_argon2id_hashes(client, resend, db):
    account = await sign_up(client, resend)

    async with db.sessions() as session:
        stored = await session.scalar(
            select(Account.password_hash).where(Account.id == uuid.UUID(account["id"]))
        )
    assert stored.startswith("$argon2id$")
    assert PASSWORD not in stored


async def test_logout_revokes_the_session_instantly_even_for_a_copied_cookie(
    client, client_from, resend
):
    await sign_up(client, resend)
    await verify(client, resend)
    cookie = (await log_in(client)).cookies["anchor_session"]

    async with client_from("10.0.0.2") as other_browser:
        other_browser.headers["Cookie"] = f"anchor_session={cookie}"
        assert (await other_browser.get("/api/auth/me")).status_code == 200

        await client.post("/api/auth/logout")

        assert (await other_browser.get("/api/auth/me")).status_code == 401


async def test_deleting_the_account_wipes_its_realm_and_nothing_else(
    client, client_from, resend, db
):
    account = await sign_up(client, resend)
    await verify(client, resend)
    await log_in(client)
    async with client_from("10.0.0.3") as neighbour:
        other = await sign_up(neighbour, resend, email="neighbour@example.com")
        await verify(neighbour, resend, email="neighbour@example.com")
        await log_in(neighbour, email="neighbour@example.com")

        wrong = await client.request("DELETE", "/api/account", json={"password": "not it"})
        assert wrong.status_code == 401

        response = await client.request("DELETE", "/api/account", json={"password": PASSWORD})
        assert response.status_code == 204
        assert 'anchor_session=""' in response.headers["set-cookie"]

        await assert_realm_wiped(db, uuid.UUID(account["id"]))
        assert (await client.get("/api/auth/me")).status_code == 401
        assert (await neighbour.get("/api/auth/me")).status_code == 200
        assert (await realm_row_counts(db, uuid.UUID(other["id"])))["auth_sessions"] == 1


async def test_the_demo_account_is_unreachable_through_the_login_form(client, db):
    async with db.sessions() as session:
        session.add(Account(email="demo@example.com", is_demo=True, verified_at=datetime.now(UTC)))
        await session.commit()

    for password in ["", "anything at all"]:
        response = await client.post(
            "/api/auth/login", json={"email": "demo@example.com", "password": password}
        )
        assert response.status_code in (401, 422)
        assert "set-cookie" not in response.headers


async def test_without_a_resend_key_mail_is_logged_never_sent(settings, caplog):
    """The dev default: no key configured, so the verification link goes to the log."""
    assert settings.resend_api_key is None
    app = create_app(settings)
    transport = ASGITransport(app=app)
    with caplog.at_level(logging.INFO, logger="anchor.mail"):
        async with (
            LifespanManager(app),
            AsyncClient(transport=transport, base_url="https://test") as client,
        ):
            response = await client.post(
                "/api/auth/signup", json={"email": EMAIL, "password": PASSWORD}
            )
            assert response.status_code == 201

    [record] = [r for r in caplog.records if r.name == "anchor.mail"]
    assert EMAIL in record.getMessage() and "/verify?token=" in record.getMessage()


@pytest.mark.settings(signup_rate_limit=2, login_rate_limit=2, verify_rate_limit=2)
async def test_signup_login_and_verification_are_rate_limited_per_ip(client, client_from, resend):
    credentials = {"email": EMAIL, "password": PASSWORD}
    attempts = [("signup", credentials), ("login", credentials), ("verify", {"token": "nope"})]

    for endpoint, body in attempts:
        statuses = [
            (await client.post(f"/api/auth/{endpoint}", json=body)).status_code for _ in range(3)
        ]
        assert statuses[:2] != [429, 429] and 429 not in statuses[:2], (endpoint, statuses)
        assert statuses[2] == 429, (endpoint, statuses)

    limited = await client.post("/api/auth/login", json=credentials)
    assert limited.json()["error"]["code"] == "rate_limited"
    assert int(limited.headers["retry-after"]) > 0

    async with client_from("203.0.113.9") as other_visitor:
        for endpoint, body in attempts:
            response = await other_visitor.post(f"/api/auth/{endpoint}", json=body)
            assert response.status_code != 429, endpoint


async def test_an_expired_session_is_refused_and_pruned(client, resend, db, jobs_app, run_jobs):
    await sign_up(client, resend)
    await verify(client, resend)
    await log_in(client)
    async with db.sessions() as session:
        await session.execute(update(AuthSession).values(expires_at=datetime.now(UTC)))
        await session.commit()

    assert (await client.get("/api/auth/me")).status_code == 401

    await jobs_app.configure_task(name=jobs.task_name(jobs.prune_expired_sessions)).defer_async(
        timestamp=0
    )
    await run_jobs()
    async with db.sessions() as session:
        assert (await session.scalars(select(AuthSession))).all() == []
