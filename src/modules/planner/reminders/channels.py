"""Concrete delivery channels: Telegram (via notify) and in-app (DB).

- ``TelegramChannel`` → ``modules.notify.service.send_message`` — the single
  Telegram path (NFR-22). If notify is unconfigured/unreachable it returns a
  failure result; the reminder still succeeds in-app.
- ``InAppChannel`` → marks the ``in_app`` delivery row sent so the polled feed
  surfaces it (FR-30/31a). This never performs network I/O, so it is the reliable
  fallback.

Both update/create the per-channel ``reminder_deliveries`` sub-row inside the
caller's session (idempotent via the ``(reminder_id, channel)`` unique).
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.notify import service as notify

from ..models import Reminder, ReminderDelivery
from .delivery import ChannelResult

logger = logging.getLogger(__name__)


async def _get_or_create_delivery(
    session: AsyncSession, *, reminder_id: int, channel: str
) -> ReminderDelivery:
    delivery = await session.scalar(
        select(ReminderDelivery).where(
            ReminderDelivery.reminder_id == reminder_id,
            ReminderDelivery.channel == channel,
        )
    )
    if delivery is None:
        delivery = ReminderDelivery(
            reminder_id=reminder_id, channel=channel, status="pending", attempts=0
        )
        session.add(delivery)
    return delivery


class TelegramChannel:
    name = "telegram"

    async def deliver(
        self, session: AsyncSession, reminder: Reminder, *, text: str
    ) -> ChannelResult:
        delivery = await _get_or_create_delivery(
            session, reminder_id=reminder.id, channel=self.name
        )
        delivery.attempts += 1
        ok = await notify.send_message(text)
        if ok:
            delivery.status = "sent"
            delivery.delivered_at = datetime.now(UTC)
            delivery.error = None
        else:
            delivery.status = "failed"
            delivery.error = "telegram send failed or not configured"
        return ChannelResult(channel=self.name, ok=ok, error=delivery.error)


class InAppChannel:
    name = "in_app"

    async def deliver(
        self, session: AsyncSession, reminder: Reminder, *, text: str
    ) -> ChannelResult:
        delivery = await _get_or_create_delivery(
            session, reminder_id=reminder.id, channel=self.name
        )
        delivery.attempts += 1
        delivery.status = "sent"
        delivery.delivered_at = datetime.now(UTC)
        delivery.error = None
        return ChannelResult(channel=self.name, ok=True)


# Enabled channels for v1: both Telegram and in-app are fixed (OQ-14 default).
CHANNELS = (TelegramChannel(), InAppChannel())
