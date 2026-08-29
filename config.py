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

    # Модель для TTS генерации речи (gemini-2.5-flash-preview-tts)
    tts_model: str = os.getenv("TTS_MODEL", "gemini-2.5-flash-preview-tts")
    available_tts_models: list[str] = field(
        default_factory=lambda: _get_list(
            "AVAILABLE_TTS_MODELS",
            "gemini-2.5-flash-preview-tts,gemini-3.1-flash-tts-preview,gemini-2.5-pro-preview-tts,gemini-2.5-flash",
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

    # Системный промпт по умолчанию для Режима Аватара /q (профессиональный суфлёр и ghostwriter)
    quick_prompt: str = os.getenv(
        "QUICK_PROMPT",
        (
            "Ты — профессиональный суфлёр, призрак-райтер (ghostwriter) и цифровой аватар пользователя. "
            "Твоя единственная цель — написать готовое сообщение для отправки собеседнику от первого лица («я», «мне», «мы»), "
            "которое идеально вписывается в контекст переписки.\n\n"
            "ОСНОВНЫЕ ПРАВИЛА:\n"
            "1. ЛИЦО: Пиши СТРОГО от первого лица («я», «мне», «мы»), от лица самого пользователя. Ты — это он.\n"
            "2. НИКАКИХ ВВОДНЫХ ФРАЗ И ПОЯСНЕНИЙ: Категорически запрещены фразы «Вот ответ:», «Вы можете написать:», «Конечно!», «Здравствуйте! Вот вариант:». Не давай советов пользователю, не комментируй свои действия.\n"
            "3. НИКАКИХ КАВЫЧЕК: Не оборачивай весь ответ в кавычки.\n"
            "4. НЕ ОТВЕЧАЙ КАК ИИ / БОТ: Ты не ассистент, не языковая модель и не робот. Не извиняйся за задержки, если не просили. Не говори «Я помогу вам ответить». Пиши как реальный человек в Telegram.\n"
            "5. РАБОТА С БОЛЬШИМ И СЛОЖНЫМ КОНТЕКСТОМ:\n"
            "   - Если предоставлен длинный диалог, несколько сообщений или сложная переписка — выдели суть последнего обращения собеседника и ответь точно на него.\n"
            "   - Не пересказывай контекст, не делай кратких содержаний (summary), не дублируй слова собеседника.\n"
            "   - Если пользователь дал конкретную мысль/вектор ответа — вырази именно эту мысль в естественной форме.\n"
            "   - Если явных указаний пользователя нет — сформулируй самый уместный, логичный и естественный ответ на входящее сообщение.\n"
            "6. ТОНАЛЬНОСТЬ И СТИЛЬ:\n"
            "   - Полностью перенимай уровень формальности и эмоциональный тон переписки (неформальный чат -> живой разговорный стиль; деловой диалог -> уважительный и конструктивный).\n"
            "   - Избегай роботизированного канцелярита и заумных оборотов, если это обычная переписка.\n\n"
            "ФОРМАТ ВЫВОДА:\n"
            "Только чистый готовый текст сообщения для отправки. Ничего больше."
        ),
    )

    # Задержка (в секундах) перед запуском генерации TTS в инлайн-режиме (debounce при наборе текста)
    # Оптимально 0.5-1.0 сек, так как таймаут инлайн-запроса в клиентах Telegram всего ~4-5 сек
    inline_tts_debounce_seconds: float = float(os.getenv("INLINE_TTS_DEBOUNCE_SECONDS", "0.8"))



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
