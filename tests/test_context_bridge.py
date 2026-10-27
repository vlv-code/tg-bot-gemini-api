import asyncio
import os
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:FAKE_TOKEN_FOR_TESTS")
os.environ.setdefault("GEMINI_API_KEY", "FAKE_KEY_FOR_TESTS")

from aiogram.types import CallbackQuery, Chat, Message, User
from handlers.common import (
    FORWARD_CONTEXT_TTL,
    ForwardedContext,
    _forwarded_contexts,
    get_forwarded_context,
    save_forwarded_context,
)
from handlers.chat import cb_fwd_stand_now, cmd_q, handle_forwarded_message, handle_text


class TestContextBridge(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _forwarded_contexts.clear()
        self.user = User(id=123, is_bot=False, first_name="TestUser")
        self.chat = Chat(id=123, type="private")

    def tearDown(self):
        _forwarded_contexts.clear()

    def test_save_and_get_forwarded_context(self):
        ctx = ForwardedContext(text="Привет от шефа", sender_name="Шеф")
        save_forwarded_context(123, ctx)

        retrieved = get_forwarded_context(123, consume=False)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.text, "Привет от шефа")
        self.assertEqual(retrieved.sender_name, "Шеф")

        # consume=True should delete the context
        consumed = get_forwarded_context(123, consume=True)
        self.assertEqual(consumed.text, "Привет от шефа")
        self.assertIsNone(get_forwarded_context(123))

    def test_forwarded_context_ttl_expiration(self):
        now = time.time()
        ctx = ForwardedContext(text="Срочно", sender_name="Коллега", created_at=now - 200)
        save_forwarded_context(123, ctx)

        # Context is older than FORWARD_CONTEXT_TTL (180s)
        self.assertIsNone(get_forwarded_context(123))

    async def test_handle_forwarded_message_captures_context_and_replies(self):
        message = MagicMock(spec=Message)
        message.from_user = self.user
        message.chat = self.chat
        message.text = "Нужно согласовать смету к вечеру"
        message.caption = None
        message.photo = None
        message.voice = None
        message.audio = None
        message.document = None

        origin = MagicMock()
        origin.sender_user = MagicMock(full_name="Иван Босс")
        message.forward_origin = origin

        message.react = AsyncMock()
        sent_mock = MagicMock(spec=Message, message_id=999)
        message.answer = AsyncMock(return_value=sent_mock)

        await handle_forwarded_message(message)

        ctx = get_forwarded_context(123, consume=False)
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx.text, "Нужно согласовать смету к вечеру")
        self.assertEqual(ctx.sender_name, "Иван Босс")
        self.assertEqual(ctx.notification_message_id, 999)

        message.react.assert_awaited_once()
        message.answer.assert_awaited_once()
        args, kwargs = message.answer.call_args
        self.assertIn("Иван Босс", args[0])
        self.assertIn("🥊 Разобрать сразу в Stand", str(kwargs.get("reply_markup")))

    @patch("handlers.chat._process_user_turn", new_callable=AsyncMock)
    async def test_cb_fwd_stand_now_consumes_context_with_user_id_override(self, mock_process_turn):
        ctx = ForwardedContext(
            text="Текст для анализа",
            sender_name="Партнёр",
            notification_message_id=999,
        )
        save_forwarded_context(123, ctx)

        callback = MagicMock(spec=CallbackQuery)
        callback.from_user = self.user
        callback.message = MagicMock(spec=Message)
        callback.message.delete = AsyncMock()
        callback.answer = AsyncMock()

        await cb_fwd_stand_now(callback)

        self.assertIsNone(get_forwarded_context(123))
        callback.message.delete.assert_awaited_once()
        callback.answer.assert_awaited_once()
        mock_process_turn.assert_awaited_once()
        _, kwargs = mock_process_turn.call_args
        self.assertEqual(kwargs.get("user_id_override"), 123)

    @patch("handlers.chat._process_user_turn", new_callable=AsyncMock)
    async def test_cmd_q_consumes_forwarded_context_when_no_reply(self, mock_process_turn):
        ctx = ForwardedContext(
            text="Вы сможете подойти на встречу?",
            sender_name="Клиент",
            notification_message_id=888,
        )
        save_forwarded_context(123, ctx)

        message = MagicMock(spec=Message)
        message.from_user = self.user
        message.chat = self.chat
        message.text = "/q Да, буду к 15:00"
        message.reply_to_message = None
        message.external_reply = None
        message.quote = None
        message.bot = MagicMock()
        message.bot.delete_message = AsyncMock()

        await cmd_q(message)

        # Context was consumed
        self.assertIsNone(get_forwarded_context(123))
        # Notification was deleted from PM
        message.bot.delete_message.assert_awaited_once_with(chat_id=123, message_id=888)
        # Processed with ghostwriter instruction
        mock_process_turn.assert_awaited_once()
        _, kwargs = mock_process_turn.call_args
        self.assertTrue(kwargs.get("use_quick_prompt"))


if __name__ == "__main__":
    unittest.main()
