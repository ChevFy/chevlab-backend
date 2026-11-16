"""add planner calendar-todo tables

Revision ID: a1b2c3d4e5f6
Revises: 117c250e2f62
Create Date: 2026-11-15

Creates the four planner tables in FK-dependency order:
events -> todos -> reminders -> reminder_deliveries (stage-03 §9.2). Hand-authored
because no DB was reachable to run Alembic autogenerate in the stage-05 sandbox;
mirrors src/modules/planner/models.py exactly (columns, types, constraint names
from the core naming convention, indexes incl. the (status, fire_at) scan index).
Datetimes are tz-aware (store-UTC). lead_time is a Postgres INTERVAL; status /
lead_source / channel are String + CHECK.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "117c250e2f62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. events (no outgoing FK)
    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("all_day", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
    )
    op.create_index(
        "ix_events_start_end", "events", ["start_at", "end_at"], unique=False
    )

    # 2. todos (FK -> events)
    op.create_table(
        "todos",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("event_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=True),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("all_day", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("done", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name=op.f("fk_todos_event_id_events"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_todos")),
        sa.UniqueConstraint("event_id", name="uq_todos_event_id"),
    )
    op.create_index(
        "ix_todos_done_start_at", "todos", ["done", "start_at"], unique=False
    )

    # 3. reminders (FK -> todos, events)
    op.create_table(
        "reminders",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("todo_id", sa.BigInteger(), nullable=False),
        sa.Column("event_id", sa.BigInteger(), nullable=True),
        sa.Column("lead_time", sa.Interval(), nullable=False),
        sa.Column(
            "lead_source",
            sa.String(length=16),
            server_default="default",
            nullable=False,
        ),
        sa.Column("fire_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending','sending','sent','failed','expired','acknowledged')",
            name="status",
        ),
        sa.CheckConstraint(
            "lead_source IN ('default','parsed','manual')",
            name="lead_source",
        ),
        sa.ForeignKeyConstraint(
            ["todo_id"],
            ["todos.id"],
            name=op.f("fk_reminders_todo_id_todos"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name=op.f("fk_reminders_event_id_events"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reminders")),
    )
    op.create_index(
        "ix_reminders_status_fire_at",
        "reminders",
        ["status", "fire_at"],
        unique=False,
    )
    op.create_index("ix_reminders_todo_id", "reminders", ["todo_id"], unique=False)

    # 4. reminder_deliveries (FK -> reminders)
    op.create_table(
        "reminder_deliveries",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("reminder_id", sa.BigInteger(), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("attempts", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "channel IN ('telegram','in_app')",
            name="channel",
        ),
        sa.CheckConstraint(
            "status IN ('pending','sent','failed')",
            name="status",
        ),
        sa.ForeignKeyConstraint(
            ["reminder_id"],
            ["reminders.id"],
            name=op.f("fk_reminder_deliveries_reminder_id_reminders"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reminder_deliveries")),
        sa.UniqueConstraint(
            "reminder_id",
            "channel",
            name="uq_reminder_deliveries_reminder_id_channel",
        ),
    )
    op.create_index(
        "ix_reminder_deliveries_inapp_feed",
        "reminder_deliveries",
        ["channel", "status", "delivered_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema (reverse FK order)."""
    op.drop_index("ix_reminder_deliveries_inapp_feed", table_name="reminder_deliveries")
    op.drop_table("reminder_deliveries")

    op.drop_index("ix_reminders_todo_id", table_name="reminders")
    op.drop_index("ix_reminders_status_fire_at", table_name="reminders")
    op.drop_table("reminders")

    op.drop_index("ix_todos_done_start_at", table_name="todos")
    op.drop_table("todos")

    op.drop_index("ix_events_start_end", table_name="events")
    op.drop_table("events")
