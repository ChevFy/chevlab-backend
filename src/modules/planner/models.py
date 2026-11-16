from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.models import Base


class Todo(Base):
    __tablename__ = "todos"
    __table_args__ = (
        sa.UniqueConstraint("event_id", name="uq_todos_event_id"),
        sa.Index("ix_todos_done_start_at", "done", "start_at"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    event_id: Mapped[int | None] = mapped_column(
        sa.BigInteger,
        sa.ForeignKey("events.id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    source_text: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    start_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    end_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    all_day: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false()
    )
    done: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )

    event: Mapped["Event | None"] = relationship(
        back_populates="todo", foreign_keys=[event_id]
    )
    reminders: Mapped[list["Reminder"]] = relationship(
        back_populates="todo",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (sa.Index("ix_events_start_end", "start_at", "end_at"),)

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    start_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False
    )
    end_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    all_day: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false()
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )

    todo: Mapped["Todo | None"] = relationship(
        back_populates="event", foreign_keys="Todo.event_id"
    )
    reminders: Mapped[list["Reminder"]] = relationship(
        back_populates="event",
        passive_deletes=True,
    )


class Reminder(Base):
    __tablename__ = "reminders"
    __table_args__ = (
        sa.Index("ix_reminders_status_fire_at", "status", "fire_at"),
        sa.Index("ix_reminders_todo_id", "todo_id"),
        sa.CheckConstraint(
            "status IN ('pending','sending','sent','failed','expired','acknowledged')",
            name="status",
        ),
        sa.CheckConstraint(
            "lead_source IN ('default','parsed','manual')",
            name="lead_source",
        ),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    todo_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        sa.ForeignKey("todos.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_id: Mapped[int | None] = mapped_column(
        sa.BigInteger,
        sa.ForeignKey("events.id", ondelete="CASCADE"),
        nullable=True,
    )
    lead_time: Mapped[timedelta] = mapped_column(sa.Interval, nullable=False)
    lead_source: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, server_default="default"
    )
    fire_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, server_default="pending"
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    claimed_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )

    todo: Mapped["Todo"] = relationship(back_populates="reminders")
    event: Mapped["Event | None"] = relationship(back_populates="reminders")
    deliveries: Mapped[list["ReminderDelivery"]] = relationship(
        back_populates="reminder",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ReminderDelivery(Base):
    __tablename__ = "reminder_deliveries"
    __table_args__ = (
        sa.UniqueConstraint(
            "reminder_id",
            "channel",
            name="uq_reminder_deliveries_reminder_id_channel",
        ),
        sa.Index(
            "ix_reminder_deliveries_inapp_feed",
            "channel",
            "status",
            "delivered_at",
        ),
        sa.CheckConstraint(
            "channel IN ('telegram','in_app')",
            name="channel",
        ),
        sa.CheckConstraint(
            "status IN ('pending','sent','failed')",
            name="status",
        ),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    reminder_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        sa.ForeignKey("reminders.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, server_default="pending"
    )
    attempts: Mapped[int] = mapped_column(
        sa.SmallInteger, nullable=False, default=0, server_default="0"
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )

    reminder: Mapped["Reminder"] = relationship(back_populates="deliveries")
