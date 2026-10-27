import os
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:FAKE_TOKEN_FOR_TESTS")
os.environ.setdefault("GEMINI_API_KEY", "FAKE_KEY_FOR_TESTS")

from storage import UserStorage, UserState
from gemini_client import GeminiResponse


class TestModeModelRuntime(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_runtime.db")
        self.storage = UserStorage(
            db_path=self.db_path,
            default_model="gemini-3.5-flash-lite",
            max_history=5,
        )
        await self.storage.init_db()

    async def asyncTearDown(self):
        await self.storage.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("handlers.common.gemini_client.ask")
    @patch("handlers.common.storage")
    @patch("handlers.common.limiter.check")
    async def test_process_user_turn_model_routing(self, mock_limiter, mock_storage, mock_ask):
        from handlers.common import _process_user_turn

        mock_limiter.return_value = MagicMock(allowed=True)
        mock_response = GeminiResponse(text="Hello", total_tokens=10)
        mock_ask.return_value = mock_response

        # Пользователь с разными моделями для режимов
        custom_state = UserState(
            model="gemini-3.5-flash-lite",
            avatar_model="gemini-4-argon",
            business_model="gemini-3.8-flash",
            stt_model="gemini-3.8-flash-lite",
        )
        mock_storage.get = AsyncMock(return_value=custom_state)
        mock_storage.get_settings = AsyncMock(return_value=custom_state)
        mock_storage.track_user_chat = AsyncMock()
        mock_storage.add_turn = AsyncMock()
        mock_storage.record_token_usage = AsyncMock()
        mock_storage.is_user_admin = AsyncMock(return_value=False)

        mock_msg = MagicMock()
        mock_msg.from_user.id = 123
        mock_msg.chat.id = 123
        mock_msg.chat.title = "Chat"
        mock_msg.chat.type = "private"
        mock_msg.bot.send_message = AsyncMock()
        mock_msg.answer = AsyncMock()

        # 1. Stand mode (обычный текст, use_quick_prompt=False)
        await _process_user_turn(mock_msg, "hi", "hi", use_quick_prompt=False)
        self.assertEqual(mock_ask.call_args.kwargs["model"], "gemini-3.5-flash-lite")

        # 2. Avatar mode (use_quick_prompt=True)
        await _process_user_turn(mock_msg, "hi", "hi", use_quick_prompt=True)
        self.assertEqual(mock_ask.call_args.kwargs["model"], "gemini-4-argon")

        # 3. STT / Voice override
        await _process_user_turn(mock_msg, "hi", "hi", model_override="gemini-3.8-flash-lite")
        self.assertEqual(mock_ask.call_args.kwargs["model"], "gemini-3.8-flash-lite")

    @patch("handlers.business.gemini_client.ask")
    @patch("handlers.business.storage")
    @patch("handlers.business.limiter.check")
    async def test_business_reply_uses_business_model(self, mock_limiter, mock_storage, mock_ask):
        from handlers.business import _generate_secretary_reply

        mock_limiter.return_value = MagicMock(allowed=True)
        mock_ask.return_value = GeminiResponse(text="Business reply", total_tokens=15)

        custom_state = UserState(
            model="gemini-3.5-flash-lite",
            business_model="gemini-3.8-flash",
        )
        mock_storage.get = AsyncMock(return_value=custom_state)
        mock_storage.get_settings = AsyncMock(return_value=custom_state)
        mock_storage.get_business_connection = AsyncMock(return_value={"is_enabled": 1})
        mock_storage.get_business_settings = AsyncMock(return_value={"system_prompt": "", "business_name": "ООО Тест"})
        mock_storage.get_business_facts = AsyncMock(return_value=[])
        mock_storage.record_token_usage = AsyncMock()

        response, _ = await _generate_secretary_reply(
            owner_user_id=456,
            chat_id=789,
            incoming_text="Сколько стоят ваши услуги?",
            hint_texts=[],
        )
        self.assertIsNotNone(response)
        self.assertEqual(mock_ask.call_args.kwargs["model"], "gemini-3.8-flash")
