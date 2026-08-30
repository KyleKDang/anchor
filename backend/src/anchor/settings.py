from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process configuration, read from ``ANCHOR_*`` environment variables."""

    model_config = SettingsConfigDict(env_prefix="ANCHOR_")

    database_url: str = "postgresql://anchor:anchor@localhost:5432/anchor"
    """libpq connection URL; shared by SQLAlchemy and the job queue."""

    health_worker_timeout: float = 5.0
    """Seconds the health check waits for the worker to answer its probe."""

    public_url: str = "http://localhost"
    """Where the app is reached from a browser; the base of every emailed link."""

    resend_api_key: str | None = None
    """Resend API key. Unset (the dev default), mail is logged instead of sent."""

    resend_base_url: str = "https://api.resend.com"
    mail_from: str = "Anchor <anchor@localhost>"

    sentry_dsn: str | None = None
    """Sentry error reporting. Unset (the dev default), errors are not reported."""

    session_ttl_hours: int = 24 * 30
    """How long a login session lives."""

    verification_ttl_hours: int = 24
    """How long an emailed verification link stays valid."""

    cookie_secure: bool = True
    """Mark the session cookie Secure (browsers still accept it on http://localhost)."""

    rate_limit_window_seconds: float = 15 * 60
    """The sliding window the per-IP limits below count within."""

    signup_rate_limit: int = 5
    login_rate_limit: int = 10
    verify_rate_limit: int = 10

    @property
    def sqlalchemy_url(self) -> str:
        return self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)
