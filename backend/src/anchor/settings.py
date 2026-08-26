from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process configuration, read from ``ANCHOR_*`` environment variables."""

    model_config = SettingsConfigDict(env_prefix="ANCHOR_")

    database_url: str = "postgresql://anchor:anchor@localhost:5432/anchor"
    """libpq connection URL; shared by SQLAlchemy and the job queue."""

    health_worker_timeout: float = 5.0
    """Seconds the health check waits for the worker to answer its probe."""

    @property
    def sqlalchemy_url(self) -> str:
        return self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)
