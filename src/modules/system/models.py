import sqlalchemy as sa
from datetime import datetime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    username: Mapped[str] = mapped_column(sa.String(25), nullable=False)
    password_hash: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    password_salt: Mapped[str] = mapped_column(sa.String(255), nullable=False)


class SystemMetric(Base):
    __tablename__ = "system_metric"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    cpu_percent: Mapped[float] = mapped_column(sa.REAL, nullable=False)
    memory_percent: Mapped[float] = mapped_column(sa.REAL, nullable=False)
    swap_percent: Mapped[float] = mapped_column(sa.REAL, nullable=False)
    disk_percent: Mapped[float] = mapped_column(sa.REAL, nullable=False)
    cpu_temp: Mapped[float] = mapped_column(sa.REAL)
    net_recv_bytes_per_sec: Mapped[float] = mapped_column(sa.REAL)
    net_sent_bytes_per_sec: Mapped[float] = mapped_column(sa.REAL)
