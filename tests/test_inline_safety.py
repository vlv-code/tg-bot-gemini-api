import os
import unittest

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:FAKE_TOKEN_FOR_TESTS")
os.environ.setdefault("GEMINI_API_KEY", "FAKE_KEY_FOR_TESTS")

from keyboards import inline_control_keyboard
from handlers.common import InlineSession
from handlers.inline import _extract_callback_author
from gemini_client import GeminiClient


class TestInlineSafetyAndKeyboards(unittest.IsolatedAsyncioTestCase):
    def test_inline_control_keyboard_with_and_without_author(self):
        # Without author_id
        kb_no_author = inline_control_keyboard("session123")
        buttons_no_author = [btn.callback_data for row in kb_no_author.inline_keyboard for btn in row]
        self.assertIn("inl_regen:session123", buttons_no_author)
        self.assertIn("inl_style:session123", buttons_no_author)
        self.assertIn("inl_fix:session123", buttons_no_author)
        self.assertIn("inl_del:session123", buttons_no_author)

        # With author_id
        kb_with_author = inline_control_keyboard("session123", author_id=987654321)
        buttons_with_author = [btn.callback_data for row in kb_with_author.inline_keyboard for btn in row]
        self.assertIn("inl_regen:session123:987654321", buttons_with_author)
        self.assertIn("inl_style:session123:987654321", buttons_with_author)
        self.assertIn("inl_fix:session123:987654321", buttons_with_author)
        self.assertIn("inl_del:session123:987654321", buttons_with_author)

    def test_extract_callback_author(self):
        # When session is active in memory
        session = InlineSession(
            session_id="s1",
            user_id=111,
            query="test",
            persona_id=None,
            persona_name="",
            persona_prompt="",
            is_quick=False,
            interactive=True,
        )
        self.assertEqual(_extract_callback_author("inl_del:s1", session), 111)

        # When session is expired (None), but author_id is encoded in callback_data
        self.assertEqual(_extract_callback_author("inl_del:s1:222", None), 222)
        self.assertEqual(_extract_callback_author("inl_regen:s1:333", None), 333)

        # When session is expired and callback_data has no author suffix
        self.assertIsNone(_extract_callback_author("inl_del:s1", None))
        self.assertIsNone(_extract_callback_author("malformed", None))

    def test_gemini_friendly_messages(self):
        # 429 Resource Exhausted with retryDelay
        exc_429 = Exception("ResourceExhausted: 429 quota exceeded, retryDelay '12.5s'")
        msg_429 = GeminiClient._friendly_message(exc_429)
        self.assertIn("12.5s", msg_429)

        # 429 Free Tier
        exc_freetier = Exception("429 RESOURCE_EXHAUSTED: limit: 10 per day FreeTier")
        msg_freetier = GeminiClient._friendly_message(exc_freetier)
        self.assertIn("10", msg_freetier)

        # 400 Invalid Argument sanitized
        exc_400 = Exception("400 INVALID_ARGUMENT: contents[0].parts[0] is empty or raw backend payload")
        msg_400 = GeminiClient._friendly_message(exc_400)
        self.assertIn("Gemini API", msg_400)

        # 503 Overloaded
        exc_503 = Exception("503 UNAVAILABLE: Model is overloaded due to high demand")
        msg_503 = GeminiClient._friendly_message(exc_503)
        self.assertIn("503", msg_503)

    async def test_active_task_cancellation_on_regen_and_del(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch
        from aiogram.types import CallbackQuery, User
        from handlers.common import _inline_sessions
        from handlers.inline import cb_inl_regen, cb_inl_del

        session_id = "test_cancel_sess"
        mock_task = MagicMock(spec=asyncio.Task)
        mock_task.done.return_value = False

        session = InlineSession(
            session_id=session_id,
            user_id=777,
            query="test query",
            is_quick=True,
            interactive=True,
            active_task=mock_task,
        )
        _inline_sessions[session_id] = session

        cb = MagicMock(spec=CallbackQuery)
        cb.data = f"inl_regen:{session_id}"
        cb.from_user = User(id=777, is_bot=False, first_name="Owner")
        cb.inline_message_id = "msg_123"
        cb.answer = AsyncMock()
        cb.bot = MagicMock()

        with patch("handlers.inline._run_background_task", side_effect=lambda coro: [coro.close(), MagicMock()][1]):
            await cb_inl_regen(cb)

        # Old active task should have been cancelled
        mock_task.cancel.assert_called_once()

        # Now test cb_inl_del
        mock_task2 = MagicMock(spec=asyncio.Task)
        mock_task2.done.return_value = False
        session.active_task = mock_task2

        cb_del = MagicMock(spec=CallbackQuery)
        cb_del.data = f"inl_del:{session_id}"
        cb_del.from_user = User(id=777, is_bot=False, first_name="Owner")
        cb_del.inline_message_id = "msg_123"
        cb_del.answer = AsyncMock()
        cb_del.bot.edit_message_text = AsyncMock()

        await cb_inl_del(cb_del)
        mock_task2.cancel.assert_called_once()
        self.assertNotIn(session_id, _inline_sessions)

    def test_extract_tts_inline_text(self):
        from handlers.inline import _extract_tts_inline_text

        self.assertEqual(_extract_tts_inline_text("~Привет"), "Привет")
        self.assertEqual(_extract_tts_inline_text("~ Привет мир"), "Привет мир")
        self.assertEqual(_extract_tts_inline_text("/tts Озвучь это"), "Озвучь это")
        self.assertEqual(_extract_tts_inline_text("tts Озвучь это"), "Озвучь это")
        self.assertEqual(_extract_tts_inline_text("/гс Привет"), "Привет")
        self.assertEqual(_extract_tts_inline_text("гс Привет"), "Привет")
        self.assertEqual(_extract_tts_inline_text("/tts"), "")
        self.assertIsNone(_extract_tts_inline_text("ttservice test"))
        self.assertIsNone(_extract_tts_inline_text("просто текст"))

    async def test_cb_inl_start_double_click_guard(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from aiogram.types import CallbackQuery, User
        from handlers.common import _inline_sessions
        from handlers.inline import cb_inl_start

        session_id = "test_dc_sess"
        mock_task = MagicMock(spec=asyncio.Task)
        mock_task.done.return_value = False

        session = InlineSession(
            session_id=session_id,
            user_id=777,
            query="test double click",
            is_quick=False,
            interactive=True,
            active_task=mock_task,
        )
        _inline_sessions[session_id] = session

        cb = MagicMock(spec=CallbackQuery)
        cb.data = f"inl_start:{session_id}"
        cb.from_user = User(id=777, is_bot=False, first_name="Owner")
        cb.inline_message_id = "msg_dc_123"
        cb.answer = AsyncMock()

        await cb_inl_start(cb)
        cb.answer.assert_called_once_with("⏳ Уже генерируется, подождите немного...", show_alert=False)
        mock_task.cancel.assert_not_called()

    async def test_cb_inl_noop(self):
        from unittest.mock import AsyncMock, MagicMock
        from aiogram.types import CallbackQuery
        from handlers.inline import cb_inl_noop

        cb = MagicMock(spec=CallbackQuery)
        cb.answer = AsyncMock()
        await cb_inl_noop(cb)
        cb.answer.assert_called_once_with("⏳ Генерация уже идёт...", show_alert=False)

    async def test_cb_inl_fix_cancels_cleanup_task(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from aiogram.types import CallbackQuery, User
        from handlers.common import _inline_sessions
        from handlers.inline import cb_inl_fix

        session_id = "test_fix_sess"
        mock_cleanup = MagicMock(spec=asyncio.Task)
        mock_cleanup.done.return_value = False

        session = InlineSession(
            session_id=session_id,
            user_id=777,
            query="test fix",
            is_quick=False,
            interactive=True,
            cleanup_task=mock_cleanup,
        )
        _inline_sessions[session_id] = session

        cb = MagicMock(spec=CallbackQuery)
        cb.data = f"inl_fix:{session_id}"
        cb.from_user = User(id=777, is_bot=False, first_name="Owner")
        cb.inline_message_id = "msg_fix_123"
        cb.bot = MagicMock()
        cb.bot.edit_message_reply_markup = AsyncMock()
        cb.answer = AsyncMock()

        await cb_inl_fix(cb)
        mock_cleanup.cancel.assert_called_once()
        cb.bot.edit_message_reply_markup.assert_called_once_with(
            inline_message_id="msg_fix_123",
            reply_markup=None,
        )

    async def test_auto_remove_inline_buttons(self):
        from unittest.mock import AsyncMock, MagicMock
        from handlers.common import _auto_remove_inline_buttons, _inline_sessions, InlineSession

        session_id = "test_auto_del_sess"
        session = InlineSession(
            session_id=session_id,
            user_id=777,
            query="test auto del",
            is_quick=False,
            interactive=True,
        )
        _inline_sessions[session_id] = session

        bot = MagicMock()
        bot.edit_message_reply_markup = AsyncMock()

        await _auto_remove_inline_buttons(bot_instance=bot, im_id="im_456", sid=session_id, delay=0)
        bot.edit_message_reply_markup.assert_called_once_with(
            inline_message_id="im_456",
            reply_markup=None,
        )
        self.assertNotIn(session_id, _inline_sessions)


if __name__ == "__main__":
    unittest.main()
