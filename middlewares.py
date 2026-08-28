"""Middleware для приватного режима бота.

Отсекает апдейты от юзеров вне ALLOWED_USER_IDS ещё до того, как они
дойдут до хендлеров — регистрируется как outer middleware, так что
срабатывает раньше любых фильтров (Command, F.data и т.д.).

Если ALLOWED_USER_IDS пуст — не фильтрует вообще (публичный режим).
"""

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from config import settings

logger = logging.getLogger(__name__)


class AccessMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not settings.allowed_user_ids:
            return await handler(event, data)

        # event_from_user кладёт встроенный UserContextMiddleware aiogram
        # (висит на update ещё до разбора на message/callback_query),
        # так что здесь он уже гарантированно есть в data.
        user = data.get("event_from_user")
        if user is None or user.id not in settings.allowed_user_ids:
            if user is not None:
                logger.info("Отклонён доступ для user_id=%s (нет в ALLOWED_USER_IDS)", user.id)
            return None  # молча игнорируем — не подтверждаем чужим, что бот вообще существует

        return await handler(event, data)
