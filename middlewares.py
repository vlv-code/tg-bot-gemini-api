"""Middleware для управления доступом и белым списком пользователей."""

import logging
from typing import Any, Awaitable, Callable, Optional

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

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

        # business_message/edited_business_message/deleted_business_messages приходят
        # от КЛИЕНТОВ бизнес-аккаунта владельца (aiogram резолвит event_from_user как
        # message.from_user, т.е. собеседника, а не владельца) — это произвольные
        # посторонние люди, они не обязаны быть в ALLOWED_USER_IDS, и это нормально:
        # белый список защищает обычный чат с ботом, а не переписку владельца с его
        # собственными клиентами. Авторизация для Secretary Mode идёт по другому пути:
        # business_connection (ниже, владелец) проверяется как обычно, а внутри
        # on_business_message есть отдельная проверка is_user_allowed(owner_user_id) —
        # на случай, если владельца уже после подключения убрали из вайтлиста.
        if isinstance(event, Update) and (
            event.business_message or event.edited_business_message or event.deleted_business_messages
        ):
            return await handler(event, data)

        user = data.get("event_from_user")
        if user is None:
            return None  # fail-closed: без явного пользователя доступ закрыт

        allowed = await self.storage.is_user_allowed(user.id)
        if not allowed:
            logger.info("Отклонён доступ для user_id=%s (не разрешён в белом списке)", user.id)
            return None  # молча игнорируем чужих

        return await handler(event, data)
