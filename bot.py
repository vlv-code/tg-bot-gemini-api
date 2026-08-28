import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand

from config import settings
from handlers import router, storage
from middlewares import AccessMiddleware

BOT_COMMANDS = [
    BotCommand(command="menu", description="Главное меню настроек и моделей"),
    BotCommand(command="start", description="Запустить бота и открыть меню"),
    BotCommand(command="voice", description="Задать вопрос и получить ответ голосом"),
    BotCommand(command="text", description="Задать вопрос и получить ответ текстом"),
    BotCommand(command="model", description="Выбрать основную модель Gemini"),
    BotCommand(command="settings", description="Параметры чата и голосовые ответы"),
    BotCommand(command="prompt", description="Настроить системный промпт"),
    BotCommand(command="tts", description="Озвучить произвольный текст"),
    BotCommand(command="limits", description="Проверить остаток лимитов запросов"),
    BotCommand(command="clear", description="Очистить историю диалога (выбор чатов)"),
    BotCommand(command="admin", description="Панель администратора (для админов)"),
]


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    # Инициализируем базу данных SQLite
    await storage.init_db()

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

    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        await storage.close()


if __name__ == "__main__":
    asyncio.run(main())

