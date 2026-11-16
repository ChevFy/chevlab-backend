"""Channel abstraction + fan-out dispatcher (FR-30/31).

A ``Channel`` delivers a reminder to one destination and returns an independent
``ChannelResult``. ``dispatch`` calls every enabled channel and collects results
so one failing channel never blocks another (NFR-20). Adding/removing a channel
does not touch scan/scheduling logic.
"""

from dataclasses import dataclass
from typing import Protocol

from ..models import Reminder


@dataclass(slots=True)
class ChannelResult:
    channel: str
    ok: bool
    error: str | None = None


class Channel(Protocol):
    name: str

    async def deliver(self, reminder: Reminder, *, text: str) -> ChannelResult: ...


def build_message(reminder: Reminder, *, title: str, is_late: bool) -> str:
    when = reminder.fire_at.isoformat()
    prefix = "(late) " if is_late else ""
    return f"{prefix}Reminder: {title} — due context fired at {when}"
