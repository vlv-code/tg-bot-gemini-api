import asyncio
import os
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:FAKE_TOKEN_FOR_TESTS")
os.environ.setdefault("GEMINI_API_KEY", "FAKE_KEY_FOR_TESTS")

from aiogram import Bot
from aiogram.types import Chat, Message, User, CallbackQuery
from gemini_client import GeminiResponse
import handlers.chat as chat_handler
import handlers.common as common_handler
from storage import UserStorage


class TestChatSecurityAudit(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_chat_sec.db")
        chat_handler.storage = UserStorage(db_path=self.db_path, default_model="gemini-3.8-flash", max_history=10)
        common_handler.storage = chat_handler.storage
        await chat_handler.storage.init_db()

        self.bot = MagicMock(spec=Bot)
        self.bot.id = 123456
        self.bot.download = AsyncMock(return_value=None)
        self.bot.send_message = AsyncMock()
        me = MagicMock(spec=User)
        me.id = 123456
        me.username = "gembotik_bot"
        self.bot.get_me = AsyncMock(return_value=me)
        chat_handler._bot_username_cache.clear()

        self.user = User(id=777, is_bot=False, first_name="Tester", username="tester")
        await chat_handler.storage.add_allowed_user(777, "tester", is_admin=False)

    async def asyncTearDown(self):
        await chat_handler.storage.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # 1. AUD-02: Group messages silently ignored unless bot is replied to or mentioned
    async def test_aud02_group_text_ignored_without_mention_or_reply(self):
        msg = MagicMock(spec=Message)
        msg.chat = Chat(id=-100123456, type="supergroup", title="Test Group")
        msg.from_user = self.user
        msg.text = "Just a general message in the group"
        msg.reply_to_message = None
        msg.bot = self.bot
        msg.answer = AsyncMock()

        with patch.object(common_handler, "_process_user_turn", new_callable=AsyncMock) as mock_turn:
            await chat_handler.handle_text(msg)
            mock_turn.assert_not_called()
            msg.answer.assert_not_called()

    async def test_aud02_group_text_processed_on_reply_to_bot(self):
        msg = MagicMock(spec=Message)
        msg.chat = Chat(id=-100123456, type="supergroup", title="Test Group")
        msg.from_user = self.user
        msg.text = "Replying to you, bot!"
        bot_reply = MagicMock(spec=Message)
        bot_reply.text = "Previous bot message"
        bot_reply.caption = None
        bot_reply.photo = None
        bot_reply.voice = None
        bot_reply.audio = None
        bot_reply.document = None
        bot_reply.from_user = MagicMock(spec=User, id=123456, is_bot=True, username="gembotik_bot")
        msg.reply_to_message = bot_reply
        msg.quote = None
        msg.external_reply = None
        msg.bot = self.bot
        msg.answer = AsyncMock()

        with patch.object(chat_handler, "_process_user_turn", new_callable=AsyncMock) as mock_turn:
            await chat_handler.handle_text(msg)
            mock_turn.assert_called_once()
            call_kwargs = mock_turn.call_args.kwargs
            self.assertIn("Replying to you, bot!", call_kwargs.get("history_text"))

    async def test_aud02_group_text_processed_on_mention(self):
        msg = MagicMock(spec=Message)
        msg.chat = Chat(id=-100123456, type="supergroup", title="Test Group")
        msg.from_user = self.user
        msg.text = "@gembotik_bot Tell me a joke"
        msg.reply_to_message = None
        msg.quote = None
        msg.external_reply = None
        msg.bot = self.bot
        msg.answer = AsyncMock()

        with patch.object(chat_handler, "_process_user_turn", new_callable=AsyncMock) as mock_turn:
            await chat_handler.handle_text(msg)
            mock_turn.assert_called_once()
            call_kwargs = mock_turn.call_args.kwargs
            # Mention must be stripped from the prompt
            self.assertEqual(call_kwargs.get("history_text"), "Tell me a joke")

    # 2. AUD-05: Context Bridge speak_response author_id
    async def test_aud05_send_response_preserves_author_id(self):
        msg = MagicMock(spec=Message)
        msg.from_user = MagicMock(id=123456)  # simulate bot message as in callback
        msg.chat = Chat(id=777, type="private")
        msg.answer = AsyncMock()

        mock_state = MagicMock()
        mock_state.rich_mode = False
        mock_response = GeminiResponse(text="Hello world", total_tokens=10)

        # Call with explicit author_id=777
        await common_handler._send_response(
            message=msg,
            state=mock_state,
            response=mock_response,
            prompt_text="Hello",
            want_audio=False,
            author_id=777,
        )

        msg.answer.assert_called_once()
        kb = msg.answer.call_args.kwargs.get("reply_markup")
        self.assertIsNotNone(kb)
        btn = kb.inline_keyboard[0][0]
        self.assertEqual(btn.callback_data, "speak_response:777")

    # 3. AUD-09: Token usage uses response.used_model
    async def test_aud09_process_user_turn_records_used_model(self):
        msg = MagicMock(spec=Message)
        msg.from_user = self.user
        msg.chat = Chat(id=777, type="private")
        msg.bot = self.bot
        msg.answer = AsyncMock()

        resp = GeminiResponse(text="Test answer", total_tokens=15, prompt_tokens=5, candidates_tokens=10)
        resp.used_model = "gemini-3.8-flash-lite-fallback"

        common_handler.gemini_client.ask = AsyncMock(return_value=resp)

        with patch.object(common_handler.storage, "record_token_usage", new_callable=AsyncMock) as mock_record:
            await common_handler._process_user_turn(
                message=msg,
                content_input="Test",
                history_text="Test",
            )
            mock_record.assert_awaited_once()
            self.assertEqual(mock_record.call_args.kwargs.get("model"), "gemini-3.8-flash-lite-fallback")

    # 4. AUD-02: Inline generation fallback intent re-parsing
    async def test_aud02_inline_generation_fallback_intent(self):
        bot = self.bot
        bot.edit_message_text = AsyncMock()

        resp = GeminiResponse(text="Polished avatar reply", total_tokens=10)
        common_handler.gemini_client.ask = AsyncMock(return_value=resp)

        # Session does not exist in _inline_sessions (expired)
        # Raw query has persona prefix "Бро: Привет как дела"
        await chat_handler.storage.save_user_prompt(777, "Бро", "Будь как бро", mode="quick")

        with patch.object(common_handler.storage, "record_token_usage", new_callable=AsyncMock):
            await common_handler._execute_inline_generation(
                bot=bot,
                inline_message_id="inl_msg_123",
                session_id="avatar_expired_session",
                user_id=777,
                raw_query_override="Бро: Привет как дела",
            )

            # Gemini client must be asked with prompt containing "Будь как бро"
            call_kwargs = common_handler.gemini_client.ask.call_args.kwargs
            self.assertIn("Будь как бро", call_kwargs.get("system_prompt", ""))
            # And message input must have prefix stripped
            self.assertIn("Привет как дела", str(call_kwargs.get("message", "")))
            self.assertNotIn("Бро:", str(call_kwargs.get("message", "")))


    def test_chat_module_type_hints_evaluable(self):
        import typing
        hints = typing.get_type_hints(chat_handler._get_bot_username)
        self.assertIn("bot", hints)
        self.assertEqual(hints["bot"], Bot)


if __name__ == "__main__":
    unittest.main()
