"""Настройки бота. Всё берётся из переменных окружения / файла .env."""

import logging
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _get_list(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass
class Settings:
    telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")

    available_models: list[str] = field(
        default_factory=lambda: _get_list(
            "AVAILABLE_MODELS",
            "gemini-2.5-flash,gemini-2.5-pro,gemini-2.0-flash",
        )
    )
    default_model: str = os.getenv("DEFAULT_MODEL", "gemini-2.5-flash")

    max_history_messages: int = int(os.getenv("MAX_HISTORY_MESSAGES", "20"))

    rate_limit_per_minute: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "10"))
    rate_limit_per_day: int = int(os.getenv("RATE_LIMIT_PER_DAY", "100"))

    # Список Telegram user_id, которым разрешено пользоваться ботом.
    # Пусто = бот отвечает всем (публичный режим).
    allowed_user_ids: list[int] = field(
        default_factory=lambda: [int(x) for x in _get_list("ALLOWED_USER_IDS", "")]
    )

    # System instruction для Gemini — тон/язык/персона ответов. Пусто = без
    # system_instruction вообще (поведение Gemini по умолчанию). Можно
    # переопределить на лету командой /prompt, не трогая .env — тот
    # останется значением по умолчанию при рестарте контейнера.
    system_prompt: str = os.getenv("SYSTEM_PROMPT", "")


settings = Settings()

if not settings.telegram_token:
    raise RuntimeError("TELEGRAM_BOT_TOKEN не задан. Проверь файл .env")
if not settings.gemini_api_key:
    raise RuntimeError("GEMINI_API_KEY не задан. Проверь файл .env")
if settings.default_model not in settings.available_models:
    # чтобы не выстрелить себе в ногу опечаткой в .env
    settings.available_models.insert(0, settings.default_model)
if not settings.allowed_user_ids:
    logging.getLogger(__name__).warning(
        "ALLOWED_USER_IDS не задан — бот отвечает ЛЮБОМУ пользователю Telegram, "
        "доступ ничем не ограничен."
    )
