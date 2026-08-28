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
            "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-3.7-flash,gemini-3.5-flash,gemini-2.5-flash",
        )
    )
    default_model: str = os.getenv("DEFAULT_MODEL", "gemini-3.5-flash-lite")

    max_history_messages: int = int(os.getenv("MAX_HISTORY_MESSAGES", "20"))

    rate_limit_per_minute: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "15"))
    rate_limit_per_day: int = int(os.getenv("RATE_LIMIT_PER_DAY", "500"))

    # Список Telegram user_id, которым разрешено пользоваться ботом.
    # Пусто = бот отвечает всем (публичный режим).
    allowed_user_ids: list[int] = field(
        default_factory=lambda: [int(x) for x in _get_list("ALLOWED_USER_IDS", "")]
    )

    # Путь к файлу SQLite базы данных
    db_path: str = os.getenv("DB_PATH", "data/bot.db")

    # Модель для TTS генерации речи (gemini-2.5-flash)
    tts_model: str = os.getenv("TTS_MODEL", "gemini-2.5-flash")

    # Пресет голоса для Gemini Audio / TTS (Puck, Charon, Kore, Fenrir, Aoede)
    tts_voice: str = os.getenv("TTS_VOICE", "Aoede")

    # System instruction для Gemini — тон/язык/персона ответов. Пусто = без
    # system_instruction вообще (поведение Gemini по умолчанию).
    # Пользователь может переопределить промпт индивидуально командой /prompt.
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
