import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456789:TEST_MOCK_TOKEN_FOR_UNITTESTS")
os.environ.setdefault("GEMINI_API_KEY", "AIzaSy_TEST_MOCK_KEY_FOR_UNITTESTS")

from aiogram import Bot
from gemini_client import GeminiResponse
from handlers.common import (
    InlineSession,
    _execute_inline_generation,
    _inline_sessions,
    _run_background_task,
)
from storage import UserState


class TestInlineAuditFixes(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        _inline_sessions.clear()

    async def test_execute_inline_generation_plain_text_fixes(self):
        user_id = 12345678
        session_id = "prev_audit_test_sess"
        inline_message_id = "inline_msg_test_001"
        plain_query = "What is the meaning of life without urls?"

        session = InlineSession(
            session_id=session_id,
            user_id=user_id,
            query=plain_query,
            is_quick=True,
            interactive=True,
            persona_prompt="",
            persona_name="Avatar",
            forwarded_context=None,
        )
        _inline_sessions[session_id] = session

        mock_bot = MagicMock(spec=Bot)
        mock_bot.edit_message_text = AsyncMock()

        fake_state = UserState(
            model="gemini-base-default",
            avatar_model="gemini-avatar-custom",
            rich_mode=False,
            system_prompt="base system prompt",
            quick_prompt="quick avatar prompt",
        )

        mock_response = GeminiResponse(
            text="42 is the answer.",
            prompt_tokens=12,
            candidates_tokens=18,
            total_tokens=30,
        )

        with patch("handlers.common.storage") as mock_storage, \
             patch("handlers.common.limiter") as mock_limiter, \
             patch("handlers.common.gemini_client") as mock_gemini_client, \
             patch("handlers.common.settings.inline_buttons_ttl_seconds", 0):

            mock_storage.get_settings = AsyncMock(return_value=fake_state)
            mock_storage.get = AsyncMock(return_value=fake_state)
            mock_storage.is_user_admin = AsyncMock(return_value=False)
            mock_storage.record_token_usage = AsyncMock()

            mock_limiter.check = AsyncMock(return_value=MagicMock(allowed=True, retry_after=0))
            mock_limiter.hit = AsyncMock()

            mock_gemini_client.ask = AsyncMock(return_value=mock_response)

            # Execution should succeed without NameError: name 'url_match' is not defined
            await _execute_inline_generation(
                bot=mock_bot,
                user_id=user_id,
                session_id=session_id,
                inline_message_id=inline_message_id,
            )

            # Verify storage.get_settings called instead of storage.get (BUG-03)
            mock_storage.get_settings.assert_awaited_once_with(user_id)
            mock_storage.get.assert_not_called()

            # Verify gemini_client called with avatar_model (target_model)
            mock_gemini_client.ask.assert_awaited_once()
            self.assertEqual(mock_gemini_client.ask.call_args.kwargs["model"], "gemini-avatar-custom")

            # Verify storage.record_token_usage called with target_model (avatar_model) instead of state.model (BUG-07)
            mock_storage.record_token_usage.assert_awaited_once_with(
                user_id=user_id,
                chat_id=user_id,
                model="gemini-avatar-custom",
                prompt_tokens=12,
                candidates_tokens=18,
                total_tokens=30,
            )

    async def test_run_background_task_logs_unhandled_exception(self):
        async def failing_coro():
            raise RuntimeError("Simulated background task failure")

        with patch("handlers.common.logger.exception") as mock_logger_exc:
            task = _run_background_task(failing_coro())
            await asyncio.gather(task, return_exceptions=True)

            mock_logger_exc.assert_called_once()
            call_args, call_kwargs = mock_logger_exc.call_args
            self.assertIn("Фоновая inline-задача завершилась с ошибкой: %s", call_args[0])
            self.assertIsInstance(call_args[1], RuntimeError)
            self.assertIs(call_kwargs.get("exc_info"), call_args[1])


if __name__ == "__main__":
    unittest.main()
