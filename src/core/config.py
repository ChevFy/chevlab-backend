from datetime import timedelta
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    POSTGRES_DB: str = Field(min_length=1)
    POSTGRES_USER: str = Field(min_length=1)
    POSTGRES_PASSWORD: str = Field(min_length=1)
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432

    SECRET_KEY: str = Field(min_length=32)
    ADMIN_USERNAME: str = Field(min_length=1)
    ADMIN_PASSWORD_HASH: str = Field(min_length=1)

    # --- Planner: NL parser (stage 05) ---
    # Which parser implementation is active (local SLM vs optional cloud).
    CALENDAR_PARSER: Literal["ollama", "claude"] = "ollama"
    # Local SLM via Ollama (default path).
    OLLAMA_HOST: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5:3b-instruct"
    # Short keep_alive so the ~2.3 GB model unloads when idle (NFR-5).
    OLLAMA_KEEP_ALIVE: str = "5m"
    # Bounded per-parse timeout in seconds (NFR-2).
    PARSE_TIMEOUT_S: float = 15.0
    # Upper bound on raw input length (NFR-8); matches schema max.
    PARSE_MAX_INPUT_CHARS: int = 500
    # Optional cloud fallback (Claude API) — opt-in, config-selected only.
    ANTHROPIC_API_KEY: str | None = None
    CLAUDE_MODEL: str = "claude-haiku-4-5"
    # Timezone operator input is resolved in before storing UTC (FR-4).
    PLANNER_RESOLVE_TZ: str = "Asia/Bangkok"

    # --- Planner: reminders (stage 05) ---
    # Default reminder lead time applied when none stated (FR-24/25).
    REMINDER_DEFAULT_LEAD: timedelta = timedelta(days=1)
    # Reminder scan cadence in seconds (~30-60s, NFR-19).
    REMINDER_SCAN_INTERVAL_S: int = 30
    # Catch-up window: deliver a missed reminder only if less than this late
    # (else mark expired) (FR-34, OQ-13).
    REMINDER_CATCHUP_WINDOW: timedelta = timedelta(hours=6)
    # Grace before a stale 'sending' row is re-claimable after a crash.
    REMINDER_CLAIM_GRACE: timedelta = timedelta(minutes=5)
    # Daily prune of fired reminders older than this (NFR-23).
    REMINDER_RETENTION_DAYS: int = 30

    # --- Telegram (notify module, stage 05) ---
    TELEGRAM_BOT_TOKEN: str | None = None
    TELEGRAM_CHAT_ID: str | None = None

    @property
    def DATABASE_URL(self) -> URL:
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD,
            host=self.POSTGRES_HOST,
            port=self.POSTGRES_PORT,
            database=self.POSTGRES_DB,
        )


settings = Settings()
