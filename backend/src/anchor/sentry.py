"""Error reporting to Sentry: a no-op unless ``ANCHOR_SENTRY_DSN`` is set."""

import sentry_sdk

from anchor.settings import Settings


def install(settings: Settings) -> None:
    if settings.sentry_dsn:
        sentry_sdk.init(dsn=settings.sentry_dsn)
