"""Central configuration. Everything is driven by environment variables (a .env file is loaded)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT / "backend"

load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def _env_bool(name: str, default: bool) -> bool:
    value = _env(name)
    if not value:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # --- site / scheduling
    site_name: str = "The Signal"
    base_url: str = "http://localhost:8000"
    timezone: str = "Asia/Kolkata"
    send_time: str = "07:30"
    refresh_hours: int = 3
    send_grace_hours: int = 6
    scheduler_enabled: bool = True

    # --- files
    db_path: Path = BACKEND_DIR / "data" / "signal.db"
    outbox_dir: Path = BACKEND_DIR / "data" / "outbox"
    topics_file: Path = BACKEND_DIR / "config" / "topics.yaml"
    sources_file: Path = BACKEND_DIR / "config" / "sources.yaml"
    frontend_dir: Path = ROOT / "frontend"

    # --- http
    trust_proxy: bool = False  # honour X-Forwarded-For (only enable behind your own reverse proxy)

    # --- newsletter
    admin_token: str = ""
    double_opt_in: bool = True
    send_welcome_digest: bool = True
    digest_word_budget: int = 1050  # ~4.7 minutes at 225 wpm
    send_delay_seconds: float = 0.2

    # --- SMTP
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_security: str = "starttls"  # starttls | ssl | none
    mail_from: str = ""
    mail_from_name: str = "The Signal"

    # --- LLM (optional)
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5-5"
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # --- News API (optional)
    news_api_key: str = ""

    @property
    def smtp_enabled(self) -> bool:
        return bool(self.smtp_host)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.groq_api_key or self.anthropic_api_key)

    @property
    def from_address(self) -> str:
        return self.mail_from or self.smtp_user or "signal@localhost"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            site_name=_env("SITE_NAME", "The Signal"),
            base_url=_env("BASE_URL", "http://localhost:8000").rstrip("/"),
            timezone=_env("TIMEZONE", "Asia/Kolkata"),
            send_time=_env("SEND_TIME", "07:30"),
            refresh_hours=max(1, _env_int("REFRESH_HOURS", 3)),
            send_grace_hours=max(1, _env_int("SEND_GRACE_HOURS", 6)),
            scheduler_enabled=_env_bool("SCHEDULER_ENABLED", True),
            db_path=Path(_env("DB_PATH", str(BACKEND_DIR / "data" / "signal.db"))),
            outbox_dir=Path(_env("OUTBOX_DIR", str(BACKEND_DIR / "data" / "outbox"))),
            trust_proxy=_env_bool("TRUST_PROXY", False),
            admin_token=_env("ADMIN_TOKEN"),
            double_opt_in=_env_bool("DOUBLE_OPT_IN", True),
            send_welcome_digest=_env_bool("SEND_WELCOME_DIGEST", True),
            digest_word_budget=_env_int("DIGEST_WORD_BUDGET", 1050),
            send_delay_seconds=_env_float("SEND_DELAY_SECONDS", 0.2),
            smtp_host=_env("SMTP_HOST"),
            smtp_port=_env_int("SMTP_PORT", 587),
            smtp_user=_env("SMTP_USER"),
            smtp_password=_env("SMTP_PASSWORD"),
            smtp_security=_env("SMTP_SECURITY", "starttls").lower(),
            mail_from=_env("MAIL_FROM"),
            mail_from_name=_env("MAIL_FROM_NAME", "The Signal"),
            anthropic_api_key=_env("ANTHROPIC_API_KEY"),
            anthropic_model=_env("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
            groq_api_key=_env("GROQ_API_KEY"),
            groq_model=_env("GROQ_MODEL", "llama-3.3-70b-versatile"),
            news_api_key=_env("NEWS_API_KEY"),
        )

# signal-refactor-8: verified modular pipeline binding

# signal-refactor-10: verified modular pipeline binding
