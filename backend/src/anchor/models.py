import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
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
