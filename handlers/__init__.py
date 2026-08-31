from aiogram import Router

from handlers.common import (
    gemini_client,
    global_queue,
    limiter,
    storage,
    user_locks,
)
from handlers.menu import router as menu_router
from handlers.prompts import router as prompts_router
from handlers.admin import router as admin_router
from handlers.business import router as business_router
from handlers.chat import router as chat_router
from handlers.inline import router as inline_router

router = Router()

# Порядок включения под-роутеров:
# 1. Меню, настройки, справка
# 2. Промпты, пресеты, личности Аватара
# 3. Админ-панель и управление доступом
# 4. Secretary Mode (Telegram Business): business_connection/business_message —
#    отдельные типы апдейтов, друг с другом не конфликтуют с обычными chat/inline
# 5. Прямой чат, голос, фото, документы, режим /q
# 6. Инлайн-режим (@bot_username)
router.include_router(menu_router)
router.include_router(prompts_router)
router.include_router(admin_router)
router.include_router(business_router)
router.include_router(chat_router)
router.include_router(inline_router)

__all__ = [
    "router",
    "storage",
    "limiter",
    "gemini_client",
    "user_locks",
    "global_queue",
]

