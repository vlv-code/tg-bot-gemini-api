import os
import unittest
from unittest.mock import AsyncMock, MagicMock

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:FAKE_TOKEN_FOR_TESTS")
os.environ.setdefault("GEMINI_API_KEY", "FAKE_KEY_FOR_TESTS")

from aiogram.types import Update

from middlewares import AccessMiddleware


class FakeStorage:
    """Лёгкая заглушка вместо реального UserStorage — не нужна БД для этих тестов."""

    def __init__(self, allowed_ids: set[int]):
        self.allowed_ids = allowed_ids
        self.calls: list[int] = []

    async def is_user_allowed(self, user_id: int) -> bool:
        self.calls.append(user_id)
        return user_id in self.allowed_ids


def _fake_update(**overrides) -> MagicMock:
    """MagicMock(spec=Update) — isinstance(event, Update) внутри middleware отработает
    корректно благодаря spec, при этом не нужно собирать настоящий pydantic Update."""
    event = MagicMock(spec=Update)
    event.business_message = None
    event.edited_business_message = None
    event.deleted_business_messages = None
    event.business_connection = None
    for key, value in overrides.items():
        setattr(event, key, value)
    return event


class TestAccessMiddleware(unittest.IsolatedAsyncioTestCase):
    async def test_no_storage_always_passes_through(self):
        middleware = AccessMiddleware(storage=None)
        handler = AsyncMock(return_value="ok")
        result = await middleware(handler, _fake_update(), {})
        self.assertEqual(result, "ok")
        handler.assert_awaited_once()

    async def test_regular_message_from_disallowed_user_is_blocked(self):
        storage = FakeStorage(allowed_ids=set())
        middleware = AccessMiddleware(storage=storage)
        handler = AsyncMock(return_value="ok")

        result = await middleware(handler, _fake_update(), {"event_from_user": MagicMock(id=42)})
        self.assertIsNone(result)
        handler.assert_not_awaited()

    async def test_regular_message_from_allowed_user_passes(self):
        storage = FakeStorage(allowed_ids={42})
        middleware = AccessMiddleware(storage=storage)
        handler = AsyncMock(return_value="ok")

        result = await middleware(handler, _fake_update(), {"event_from_user": MagicMock(id=42)})
        self.assertEqual(result, "ok")
        handler.assert_awaited_once()

    async def test_missing_event_from_user_fails_closed(self):
        storage = FakeStorage(allowed_ids={42})
        middleware = AccessMiddleware(storage=storage)
        handler = AsyncMock(return_value="ok")

        result = await middleware(handler, _fake_update(), {})  # event_from_user отсутствует
        self.assertIsNone(result)
        handler.assert_not_awaited()

    async def test_business_message_bypasses_whitelist_even_for_unknown_client(self):
        """Ключевой сценарий: клиент бизнес-чата — произвольный человек, никогда не
        будет в ALLOWED_USER_IDS, и это ожидаемо. business_message не должен блокироваться."""
        storage = FakeStorage(allowed_ids=set())  # никто не разрешён
        middleware = AccessMiddleware(storage=storage)
        handler = AsyncMock(return_value="handled")

        event = _fake_update(business_message=MagicMock())
        result = await middleware(handler, event, {"event_from_user": MagicMock(id=999999999)})

        self.assertEqual(result, "handled")
        handler.assert_awaited_once()
        self.assertEqual(storage.calls, [])  # is_user_allowed для клиента вообще не вызывался

    async def test_edited_business_message_bypasses_whitelist(self):
        storage = FakeStorage(allowed_ids=set())
        middleware = AccessMiddleware(storage=storage)
        handler = AsyncMock(return_value="handled")

        event = _fake_update(edited_business_message=MagicMock())
        result = await middleware(handler, event, {"event_from_user": MagicMock(id=1)})
        self.assertEqual(result, "handled")
        handler.assert_awaited_once()

    async def test_deleted_business_messages_bypasses_whitelist(self):
        storage = FakeStorage(allowed_ids=set())
        middleware = AccessMiddleware(storage=storage)
        handler = AsyncMock(return_value="handled")

        event = _fake_update(deleted_business_messages=MagicMock())
        result = await middleware(handler, event, {})  # даже без event_from_user
        self.assertEqual(result, "handled")
        handler.assert_awaited_once()

    async def test_business_connection_is_still_gated_by_whitelist(self):
        """business_connection — это ПОДКЛЮЧЕНИЕ владельца, не сообщение клиента,
        поэтому здесь whitelist должен продолжать работать как обычно."""
        storage = FakeStorage(allowed_ids=set())  # владелец НЕ в вайтлисте
        middleware = AccessMiddleware(storage=storage)
        handler = AsyncMock(return_value="handled")

        event = _fake_update(business_connection=MagicMock())
        result = await middleware(handler, event, {"event_from_user": MagicMock(id=777)})

        self.assertIsNone(result)
        handler.assert_not_awaited()
        self.assertEqual(storage.calls, [777])

    async def test_business_connection_from_allowed_owner_passes(self):
        storage = FakeStorage(allowed_ids={777})
        middleware = AccessMiddleware(storage=storage)
        handler = AsyncMock(return_value="handled")

        event = _fake_update(business_connection=MagicMock())
        result = await middleware(handler, event, {"event_from_user": MagicMock(id=777)})

        self.assertEqual(result, "handled")
        handler.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
