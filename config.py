"""Настройки бота. Всё берётся из переменных окружения / файла .env."""

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

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
            "gemini-3.5-flash-lite,gemini-3.8-flash,gemini-3.8-flash-lite,gemini-3.6-flash,gemini-4-argon",
        )
    )
    default_model: str = os.getenv("DEFAULT_MODEL", "gemini-3.5-flash-lite")
    default_avatar_model: Optional[str] = os.getenv("DEFAULT_AVATAR_MODEL", None) or None
    default_business_model: Optional[str] = os.getenv("DEFAULT_BUSINESS_MODEL", None) or None
    default_stt_model: Optional[str] = os.getenv("DEFAULT_STT_MODEL", None) or None

    # Таймаут запросов к Gemini API в секундах
    gemini_timeout_seconds: float = float(os.getenv("GEMINI_TIMEOUT_SECONDS", "35.0"))

    # Интервал авто-синхронизации каталога моделей с Google GenAI API в часах (0 = отключено)
    models_sync_interval_hours: int = int(os.getenv("MODELS_SYNC_INTERVAL_HOURS", "24"))

    # Задержка дебаунса для Secretary Mode в секундах (склейка лесенок сообщений клиентов)
    business_debounce_seconds: float = float(os.getenv("BUSINESS_DEBOUNCE_SECONDS", "3.5"))

    max_history_messages: int = int(os.getenv("MAX_HISTORY_MESSAGES", "20"))

    rate_limit_per_minute: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "15"))
    rate_limit_per_day: int = int(os.getenv("RATE_LIMIT_PER_DAY", "500"))

    # Список Telegram user_id, которым разрешено пользоваться ботом.
    # Пусто = бот отвечает всем (публичный режим).
    allowed_user_ids: list[int] = field(
        default_factory=lambda: [int(x) for x in _get_list("ALLOWED_USER_IDS", "")]
    )

    # Список Telegram user_id администраторов бота (суперадмины)
    admin_ids: list[int] = field(
        default_factory=lambda: [
            int(x) for x in _get_list("ADMIN_IDS", os.getenv("ADMIN_ID", "")) if x
        ]
    )

    # Максимальное количество параллельных запросов к Gemini API (глобальная очередь)
    max_concurrent_requests: int = int(os.getenv("MAX_CONCURRENT_REQUESTS", "5"))

    # Путь к файлу SQLite базы данных
    db_path: str = os.getenv("DB_PATH", "data/bot.db")

    # Модель для TTS генерации речи (gemini-3.8-flash-tts)
    tts_model: str = os.getenv("TTS_MODEL", "gemini-3.8-flash-tts")
    available_tts_models: list[str] = field(
        default_factory=lambda: _get_list(
            "AVAILABLE_TTS_MODELS",
            "gemini-3.8-flash-tts,gemini-3.8-flash-lite-tts",
        )
    )

    # Пресет голоса для Gemini Audio / TTS (Puck, Charon, Kore, Fenrir, Aoede)
    tts_voice: str = os.getenv("TTS_VOICE", "Aoede")
    available_voices: list[str] = field(
        default_factory=lambda: _get_list(
            "AVAILABLE_VOICES",
            "Aoede,Kore,Puck,Fenrir,Charon",
        )
    )

    # System instruction для Gemini — тон/язык/персона ответов. Пусто = без
    # system_instruction вообще (поведение Gemini по умолчанию).
    # Пользователь может переопределить промпт индивидуально командой /prompt.
    system_prompt: str = os.getenv("SYSTEM_PROMPT", "")

    # Базовый стиль Режима Аватара по умолчанию (если пользователь не выбрал свою Личность).
    # Все мета-правила суфлёра и защита от роли слуги вшиты в движок бота автоматически.
    quick_prompt: str = os.getenv(
        "AVATAR_PROMPT",
        os.getenv(
            "QUICK_PROMPT",
            "Write naturally, wittily, concisely, and confidently, matching the emotional tone and level of formality of the conversation.",
        ),
    )

    # Задержка (в секундах) перед запуском генерации TTS в инлайн-режиме (debounce при наборе текста)
    # Оптимально 0.5-1.0 сек, так как таймаут инлайн-запроса в клиентах Telegram всего ~4-5 сек
    inline_tts_debounce_seconds: float = float(os.getenv("INLINE_TTS_DEBOUNCE_SECONDS", "0.8"))
    tts_dump_chat_id: int = int(os.getenv("TTS_DUMP_CHAT_ID", "0") or "0")
    inline_buttons_ttl_seconds: int = int(os.getenv("INLINE_BUTTONS_TTL_SECONDS", "30") or "30")



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
