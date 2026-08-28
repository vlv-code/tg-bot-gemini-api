"""Middleware для управления доступом и белым списком пользователей."""

import logging
from typing import Any, Awaitable, Callable, Optional

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from storage import UserStorage

logger = logging.getLogger(__name__)


class AccessMiddleware(BaseMiddleware):
    def __init__(self, storage: Optional[UserStorage] = None) -> None:
        self.storage = storage

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if self.storage is None:
            return await handler(event, data)

        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        allowed = await self.storage.is_user_allowed(user.id)
        if not allowed:
            logger.info("Отклонён доступ для user_id=%s (не разрешён в белом списке)", user.id)
            return None  # молча игнорируем чужих

        return await handler(event, data)
