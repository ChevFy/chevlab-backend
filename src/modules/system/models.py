from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from core.models import Base


class SystemMetric(Base):
    __tablename__ = "system_metric"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, index=True
    )
    cpu_percent: Mapped[float] = mapped_column(sa.REAL, nullable=False)
    memory_percent: Mapped[float] = mapped_column(sa.REAL, nullable=False)
    swap_percent: Mapped[float] = mapped_column(sa.REAL, nullable=False)
    disk_percent: Mapped[float] = mapped_column(sa.REAL, nullable=False)
    cpu_temp: Mapped[float | None] = mapped_column(sa.REAL)
    net_recv_bytes_per_sec: Mapped[float | None] = mapped_column(sa.REAL)
    net_sent_bytes_per_sec: Mapped[float | None] = mapped_column(sa.REAL)
