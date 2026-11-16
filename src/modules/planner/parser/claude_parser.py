"""Optional cloud fallback parser: Claude API (FR-3b).

Selected by configuration ONLY (``CALENDAR_PARSER=claude``); there is NO silent
runtime auto-fallback (OQ-9, privacy NFR-12). Requires ``ANTHROPIC_API_KEY``.
Same interface and the same hybrid contract as the Ollama parser: it extracts
title + rough date/reminder phrases and does NOT resolve dates.

This is a documented, minimal implementation using ``httpx`` against the Anthropic
Messages API. It is the ONLY place the Claude provider is referenced (NFR-13).
"""

import json
import logging
from datetime import datetime

import httpx

from core.config import settings

from .base import ParseFailed, ParserUnavailable, ParseTimeout, RawExtraction

logger = logging.getLogger(__name__)

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

SYSTEM_PROMPT = (
    "You extract structured fields from a single short line of text that may be "
    "Thai or English. Reply with ONLY a JSON object with keys: title (string), "
    "date_expr (string or null), reminder_expr (string or null). date_expr is the "
    "rough date/time phrase exactly as written; do NOT compute or resolve dates."
)


class ClaudeParser:
    async def available(self) -> bool:
        return bool(settings.ANTHROPIC_API_KEY)

    async def extract(self, text: str, *, now: datetime) -> RawExtraction:
        if not settings.ANTHROPIC_API_KEY:
            raise ParserUnavailable("cloud parser not configured")

        payload = {
            "model": settings.CLAUDE_MODEL,
            "max_tokens": 256,
            "system": SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"Today is {now.isoformat()} (Asia/Bangkok). "
                        f"Extract from: {text!r}"
                    ),
                }
            ],
        }
        headers = {
            "x-api-key": settings.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=settings.PARSE_TIMEOUT_S) as client:
                resp = await client.post(ANTHROPIC_URL, json=payload, headers=headers)
                resp.raise_for_status()
                body = resp.json()
        except httpx.TimeoutException as exc:
            raise ParseTimeout("cloud parser timed out") from exc
        except httpx.HTTPError as exc:
            logger.warning("claude request failed: %s", exc)
            raise ParserUnavailable("cloud parser unreachable") from exc

        try:
            content = body["content"][0]["text"]
            data = json.loads(content)
        except (KeyError, IndexError, json.JSONDecodeError, TypeError) as exc:
            raise ParseFailed("cloud parser returned no usable structure") from exc

        title = (data.get("title") or "").strip()
        if not title:
            raise ParseFailed("cloud parser produced no title")

        return RawExtraction(
            title=title,
            date_expr=(data.get("date_expr") or None),
            reminder_expr=(data.get("reminder_expr") or None),
        )
