import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand

from config import settings
from gemini_client import sync_models_catalog
from handlers import close_http_session, gemini_client, router, storage
from middlewares import AccessMiddleware

BOT_COMMANDS = [
    BotCommand(command="menu", description="Главное меню настроек и моделей"),
    BotCommand(command="start", description="Запустить бота и открыть меню"),
    BotCommand(command="help", description="ℹ️ Справка и краткое руководство по боту"),
    BotCommand(command="avatar", description="🎭 Ответить в стиле Аватара (реплай или текст)"),
    BotCommand(command="avatars", description="🎭 Управление личностями и промптом Аватара"),
    BotCommand(command="q", description="🎭 Режим Аватара: ответ от 1-го лица (/q или /avatar)"),
    BotCommand(command="prompt", description="🥊 Настроить/изменить промпт Stand-режима"),
    BotCommand(command="prompts", description="🥊 Каталог промптов и ролей Stand-режима"),
    BotCommand(command="voice", description="🎙 Stand-режим: вопрос и ответ голосом"),
    BotCommand(command="text", description="💬 Stand-режим: вопрос и ответ текстом"),
    BotCommand(command="model", description="🤖 Выбрать основную модель Gemini"),
    BotCommand(command="settings", description="⚙️ Параметры чата и голосовые ответы"),
    BotCommand(command="tts", description="🎧 Озвучить произвольный текст"),
    BotCommand(command="limits", description="📊 Проверить остаток лимитов запросов"),
    BotCommand(command="clear", description="🗑 Очистить историю диалога (выбор чатов)"),
    BotCommand(command="business", description="💼 Secretary Mode: Telegram Business и авто-ответы"),
    BotCommand(command="admin", description="👑 Панель администратора (для админов)"),
]


async def start_models_sync_worker() -> None:
    """Периодически синхронизирует список моделей с Google GenAI API."""
    if settings.models_sync_interval_hours <= 0:
        logging.info("Периодическая синхронизация моделей отключена (MODELS_SYNC_INTERVAL_HOURS <= 0)")
        return
    interval_seconds = max(60, settings.models_sync_interval_hours * 3600)
    while True:
        try:
            stats = await sync_models_catalog(gemini_client, storage)
            logging.info("Синхронизация каталога моделей завершена: %s", stats)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logging.warning("Ошибка периодической синхронизации моделей: %s", exc)
        await asyncio.sleep(interval_seconds)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    bot: Bot | None = None
    sync_task: asyncio.Task | None = None
    try:
        # Инициализируем базу данных SQLite
        await storage.init_db()
        await storage.seed_baseline_models()

        # Фоновая синхронизация моделей с Google GenAI API
        sync_task = asyncio.create_task(start_models_sync_worker())

        # parse_mode на уровне бота не ставим: rich/plain режим выбирается
        # per-message в handlers.py в зависимости от настройки пользователя
        bot = Bot(token=settings.telegram_token, default=DefaultBotProperties(parse_mode=None))
        dispatcher = Dispatcher()
        dispatcher.update.outer_middleware(AccessMiddleware(storage=storage))
        dispatcher.include_router(router)

        await bot.set_my_commands(BOT_COMMANDS)
        logging.info("Меню команд успешно зарегистрировано в Telegram")

        await bot.delete_webhook(drop_pending_updates=True)
        logging.info("Бот запущен, доступные модели: %s", settings.available_models)

        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        if sync_task is not None:
            sync_task.cancel()
            try:
                await sync_task
            except (asyncio.CancelledError, Exception):
                pass
        if bot is not None:
            await bot.session.close()
        await storage.close()
        await close_http_session()


if __name__ == "__main__":
    asyncio.run(main())

