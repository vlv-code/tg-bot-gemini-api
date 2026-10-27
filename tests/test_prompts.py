import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:FAKE_TOKEN_FOR_TESTS")
os.environ.setdefault("GEMINI_API_KEY", "FAKE_KEY_FOR_TESTS")

from aiogram.types import Chat, Message, User
from handlers.prompts import cmd_avatar


class TestAvatarCommandRouting(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = User(id=123, is_bot=False, first_name="Tester")
        self.chat = Chat(id=456, type="private")

    @patch("handlers.prompts.storage")
    async def test_avatar_no_args_opens_menu(self, mock_storage):
        message = MagicMock(spec=Message)
        message.from_user = self.user
        message.chat = self.chat
        message.text = "/avatar"
        message.reply_to_message = None
        message.external_reply = None
        message.quote = None
        message.answer = AsyncMock()

        mock_state = MagicMock()
        mock_state.quick_prompt = ""
        mock_storage.get_settings = AsyncMock(return_value=mock_state)
        mock_storage.get_all_personas = AsyncMock(return_value=[])
        mock_storage.get_pinned_persona_ids = AsyncMock(return_value=[])

        await cmd_avatar(message)

        message.answer.assert_called_once()
        args, kwargs = message.answer.call_args
        self.assertIn("Личности Аватара", args[0])

    @patch("handlers.prompts.storage")
    @patch("handlers.chat.cmd_q", new_callable=AsyncMock)
    async def test_avatar_with_reply_delegates_to_cmd_q(self, mock_cmd_q, mock_storage):
        message = MagicMock(spec=Message)
        message.from_user = self.user
        message.chat = self.chat
        message.text = "/avatar скажи что занят"
        message.reply_to_message = MagicMock(spec=Message)
        message.external_reply = None
        message.quote = None
        message.answer = AsyncMock()

        await cmd_avatar(message)

        mock_cmd_q.assert_awaited_once_with(message)
        message.answer.assert_not_called()

    @patch("handlers.prompts.storage")
    @patch("handlers.chat.cmd_q", new_callable=AsyncMock)
    async def test_avatar_with_draft_text_delegates_to_cmd_q(self, mock_cmd_q, mock_storage):
        message = MagicMock(spec=Message)
        message.from_user = self.user
        message.chat = self.chat
        message.text = "/avatar Напиши вежливый отказ"
        message.reply_to_message = None
        message.external_reply = None
        message.quote = None
        message.answer = AsyncMock()

        mock_state = MagicMock()
        mock_storage.get_settings = AsyncMock(return_value=mock_state)
        mock_storage.find_persona_by_name_or_id = AsyncMock(return_value=None)

        await cmd_avatar(message)

        mock_cmd_q.assert_awaited_once_with(message)
        message.answer.assert_not_called()

    @patch("handlers.prompts.storage")
    @patch("handlers.chat.cmd_q", new_callable=AsyncMock)
    async def test_avatar_matching_persona_activates_it(self, mock_cmd_q, mock_storage):
        message = MagicMock(spec=Message)
        message.from_user = self.user
        message.chat = self.chat
        message.text = "/avatar Бро"
        message.reply_to_message = None
        message.external_reply = None
        message.quote = None
        message.answer = AsyncMock()

        mock_state = MagicMock()
        mock_storage.get_settings = AsyncMock(return_value=mock_state)
        mock_storage.find_persona_by_name_or_id = AsyncMock(
            return_value={"id": 1, "name": "bro", "title": "Бро", "prompt": "Будь бро"}
        )
        mock_storage.set_quick_prompt = AsyncMock()

        await cmd_avatar(message)

        mock_cmd_q.assert_not_called()
        mock_storage.set_quick_prompt.assert_awaited_once_with(123, "Будь бро")
        message.answer.assert_called_once()
        self.assertIn("Активирована личность", message.answer.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
