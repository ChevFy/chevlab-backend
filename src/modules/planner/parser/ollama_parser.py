"""Default parser: local SLM via Ollama (NFR-4).

One model call per parse (NFR-3), structured JSON output, a tight system prompt
that instructs the model to extract a title + rough date/reminder phrases and to
NOT compute dates (FR-3a). Uses the async ``ollama`` client so the network wait
yields the event loop (the heavy work runs in the Ollama process, not ours —
NFR-6). Short ``keep_alive`` so the ~2.3 GB model unloads when idle (NFR-5).
Bounded timeout (NFR-2) → ``ParseTimeout``.

This is the ONLY place the ``ollama`` provider is imported (NFR-13).
"""

import asyncio
import json
import logging
from datetime import datetime

import httpx
from ollama import AsyncClient, ResponseError

from core.config import settings

from .base import ParseFailed, ParserUnavailable, ParseTimeout, RawExtraction

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You extract structured fields from a single short line of text that may be "
    "Thai or English. Return ONLY JSON with keys: title (string), date_expr "
    "(string or null), reminder_expr (string or null). "
    "title is the task/event name with time words removed. "
    "date_expr is the ROUGH date/time phrase exactly as written (e.g. "
    "'พรุ่งนี้บ่าย 3 โมง', 'tomorrow 9am', 'next Monday'); do NOT compute or "
    "resolve any dates or produce a timestamp. "
    "Keep the FULL Thai clock phrase intact in date_expr: forms like "
    "'N โมงเช้า', 'N โมงเย็น', 'N ทุ่ม', 'ตี N', 'N นาฬิกา', 'บ่าย N โมง' must be "
    "copied whole — NEVER reduce a time to a bare number (do not emit just '8'). "
    "Keep the relative-day word (วันนี้/พรุ่งนี้/มะรืน/...) together with the "
    "clock phrase in date_expr. "
    "Example: input 'ตื่นเวลา 8โมงเช้า' -> title 'ตื่น', date_expr '8 โมงเช้า'. "
    "Example: input 'ประชุมพรุ่งนี้บ่าย 2 โมง' -> title 'ประชุม', date_expr "
    "'พรุ่งนี้บ่าย 2 โมง'. "
    "reminder_expr is any reminder/lead-time phrase (e.g. 'เตือนก่อน 2 ชั่วโมง', "
    "'remind me 1 day before') or null if none. "
    "If there is no date/time phrase, set date_expr to null."
)


class OllamaParser:
    def __init__(self) -> None:
        self._client = AsyncClient(host=settings.OLLAMA_HOST)

    async def available(self) -> bool:
        try:
            await asyncio.wait_for(
                self._client.list(), timeout=settings.PARSE_TIMEOUT_S
            )
            return True
        except (TimeoutError, httpx.HTTPError, ConnectionError, OSError):
            return False
        except Exception:
            logger.warning("ollama availability check failed", exc_info=True)
            return False

    async def extract(self, text: str, *, now: datetime) -> RawExtraction:
        user_prompt = (
            f"Today is {now.isoformat()} (Asia/Bangkok). Extract from: {text!r}"
        )
        try:
            response = await asyncio.wait_for(
                self._client.chat(
                    model=settings.OLLAMA_MODEL,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    format="json",
                    keep_alive=settings.OLLAMA_KEEP_ALIVE,
                    options={"temperature": 0},
                ),
                timeout=settings.PARSE_TIMEOUT_S,
            )
        except TimeoutError as exc:
            logger.warning("ollama parse timed out after %ss", settings.PARSE_TIMEOUT_S)
            raise ParseTimeout("parser timed out") from exc
        except ResponseError as exc:
            # model not pulled / server error → treat as unavailable
            logger.warning("ollama response error: %s", exc)
            raise ParserUnavailable("parser returned an error") from exc
        except (httpx.HTTPError, ConnectionError, OSError) as exc:
            logger.warning("ollama unreachable: %s", exc)
            raise ParserUnavailable("parser unreachable") from exc

        content = (response.get("message") or {}).get("content") or ""
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.info("ollama returned non-JSON content: %r", content)
            raise ParseFailed("parser returned no usable structure") from exc

        title = (data.get("title") or "").strip()
        if not title:
            raise ParseFailed("parser produced no title")

        return RawExtraction(
            title=title,
            date_expr=(data.get("date_expr") or None),
            reminder_expr=(data.get("reminder_expr") or None),
        )
