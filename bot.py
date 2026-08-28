import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from config import settings
from handlers import router, storage


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    # Инициализируем базу данных SQLite
    await storage.init_db()

    # parse_mode на уровне бота не ставим: rich/plain режим выбирается
    # per-message в handlers.py в зависимости от настройки пользователя
    bot = Bot(token=settings.telegram_token, default=DefaultBotProperties(parse_mode=None))
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Бот запущен, доступные модели: %s", settings.available_models)
    await dispatcher.start_polling(bot)



if __name__ == "__main__":
    asyncio.run(main())
