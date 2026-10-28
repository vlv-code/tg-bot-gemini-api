from datetime import datetime
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Устанавливаем фейковые токены для тестов до импорта модулей
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:FAKE_TOKEN_FOR_TESTS")
os.environ.setdefault("GEMINI_API_KEY", "FAKE_KEY_FOR_TESTS")

from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, Update, User, Voice

from handlers import router, storage, limiter, gemini_client, user_locks, global_queue
from handlers.menu import router as menu_router
from handlers.prompts import router as prompts_router
from handlers.admin import router as admin_router
from handlers.business import router as business_router
from handlers.chat import router as chat_router
from handlers.inline import router as inline_router


class TestHandlersModularization(unittest.IsolatedAsyncioTestCase):
    def test_main_router_composition(self):
        """Проверяем, что главный роутер включает все 6 под-роутеров в правильном порядке."""
        self.assertIsNotNone(router)
        sub_routers = router.sub_routers
        self.assertEqual(len(sub_routers), 6)
        self.assertIs(sub_routers[0], menu_router)
        self.assertIs(sub_routers[1], prompts_router)
        self.assertIs(sub_routers[2], admin_router)
        self.assertIs(sub_routers[3], business_router)
        self.assertIs(sub_routers[4], chat_router)
        self.assertIs(sub_routers[5], inline_router)

    def test_shared_services_exports(self):
        """Проверяем, что все синглтоны и сервисы корректно экспортируются из пакета handlers."""
        self.assertIsNotNone(storage)
        self.assertIsNotNone(limiter)
        self.assertIsNotNone(gemini_client)
        self.assertIsNotNone(user_locks)
        self.assertIsNotNone(global_queue)

    async def test_reply_to_voice_message_is_not_swallowed_by_business_draft(self):
        """Проверяем, что обычный ответ на голосовое сообщение в ЛС не поглощается business_router."""
        import handlers.chat as chat_mod

        dp = Dispatcher()
        dp.include_router(router)

        bot = MagicMock(spec=Bot)
        bot.id = 8824955574
        bot.download = AsyncMock()

        voice_msg = Message(
            message_id=100,
            date=datetime.now(),
            chat=Chat(id=55555, type="private"),
            from_user=User(id=8824955574, is_bot=True, first_name="Bot"),
            voice=Voice(file_id="voice_abc", file_unique_id="v_u_abc", duration=3),
        )
        reply_msg = Message(
            message_id=101,
            date=datetime.now(),
            chat=Chat(id=55555, type="private"),
            from_user=User(id=55555, is_bot=False, first_name="User"),
            text="транскрибируй",
            reply_to_message=voice_msg,
        )
        update = Update(update_id=1001, message=reply_msg)

        with patch("handlers.chat._process_user_turn", new_callable=AsyncMock) as mock_process:
            await dp.feed_update(bot, update)
            # _process_user_turn в chat_router ОБЯЗАН быть вызван
            mock_process.assert_called_once()
            call_kwargs = mock_process.call_args.kwargs
            self.assertEqual(call_kwargs["message"].text, "транскрибируй")
            self.assertIsNotNone(call_kwargs["content_input"])


if __name__ == "__main__":
    unittest.main()
