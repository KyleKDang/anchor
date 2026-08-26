"""Outbound mail through Resend's HTTP API.

The HTTP edge is the fake boundary in tests (a transport is injected); without an
API key configured, the dev default, messages go to the log and never to a mailbox.
"""

import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

from anchor.settings import Settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    text: str


class Mailer(Protocol):
    async def send(self, message: Message) -> None: ...

    async def aclose(self) -> None: ...


class ResendMailer:
    def __init__(
        self,
        api_key: str,
        sender: str,
        base_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._sender = sender
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
            timeout=10.0,
        )

    async def send(self, message: Message) -> None:
        response = await self._client.post(
            "/emails",
            json={
                "from": self._sender,
                "to": [message.to],
                "subject": message.subject,
                "text": message.text,
            },
        )
        response.raise_for_status()

    async def aclose(self) -> None:
        await self._client.aclose()


class LogMailer:
    """No Resend key configured: the message is logged, so nothing ever leaves the box."""

    async def send(self, message: Message) -> None:
        log.info("Mail to %s: %s\n%s", message.to, message.subject, message.text)

    async def aclose(self) -> None:
        pass


def build_mailer(settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> Mailer:
    """Resend when a key is configured or a transport is injected; otherwise the log."""
    if transport is None and settings.resend_api_key is None:
        return LogMailer()
    return ResendMailer(
        api_key=settings.resend_api_key or "unset",
        sender=settings.mail_from,
        base_url=settings.resend_base_url,
        transport=transport,
    )


def verification_message(to: str, link: str) -> Message:
    return Message(
        to=to,
        subject="Verify your Anchor email",
        text=(
            "Welcome to Anchor.\n\n"
            f"Finish signing up by opening this link and entering your password:\n{link}\n\n"
            "The link is good for one use. If you did not sign up, ignore this message."
        ),
    )
