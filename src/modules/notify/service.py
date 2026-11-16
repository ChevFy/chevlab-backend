"""Minimal, config-gated Telegram sender (stage 05 decision).

This is the SINGLE Telegram path (NFR-22). Reminder delivery reuses this rather
than opening a second Telegram integration. It is gated on
``TELEGRAM_BOT_TOKEN`` / ``TELEGRAM_CHAT_ID``: if unconfigured it reports
"not configured" and the caller degrades gracefully (in-app channel still works).
"""

import logging

import httpx

from core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT_S = 10.0


def is_configured() -> bool:
    return bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID)


async def send_message(text: str) -> bool:
    """Send a Telegram message. Returns True on success, False otherwise.

    Never raises on delivery failure — the caller treats a False as a per-channel
    failure so other channels (in-app) are unaffected (FR-31, NFR-20). Secrets
    (bot token) are never logged (NFR-21).
    """
    if not is_configured():
        logger.info("telegram not configured; skipping send")
        return False

    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": settings.TELEGRAM_CHAT_ID, "text": text}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
        return True
    except httpx.HTTPStatusError as exc:
        # Do NOT log `exc` — its string embeds the token-bearing request URL.
        logger.warning("telegram send failed: HTTP %s", exc.response.status_code)
        return False
    except httpx.HTTPError as exc:
        # Log the exception type only; the message/repr can carry the request URL.
        logger.warning("telegram send failed: %s", type(exc).__name__)
        return False
