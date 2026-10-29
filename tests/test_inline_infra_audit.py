import asyncio
import os
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:FAKE_TOKEN_FOR_TESTS")
os.environ.setdefault("GEMINI_API_KEY", "FAKE_KEY_FOR_TESTS")

from aiogram import Bot
from aiogram.types import InlineQuery, User
import handlers.inline as inline_handler
from middlewares import AccessMiddleware
from storage import UserStorage


class TestInlineInfraAudit(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_inline.db")
        inline_handler.storage = UserStorage(db_path=self.db_path, default_model="gemini-3.8-flash", max_history=10)
        await inline_handler.storage.init_db()

        self.bot = MagicMock(spec=Bot)
        self.bot.id = 123456
        self.bot.send_voice = AsyncMock()
        self.bot.delete_message = AsyncMock()

        self.user = User(id=999, is_bot=False, first_name="InlineUser", username="inline_user")
        await inline_handler.storage.add_allowed_user(999, "inline_user", is_admin=False)

    async def asyncTearDown(self):
        await inline_handler.storage.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # 1. AUD-14: AccessMiddleware answers empty results on inline_query when denied
    async def test_aud14_access_middleware_inline_query_empty_answer(self):
        mock_storage = MagicMock()
        mock_storage.is_user_allowed = AsyncMock(return_value=False)

        middleware = AccessMiddleware(storage=mock_storage)
        event = MagicMock()
        event.business_message = None
        event.edited_business_message = None
        event.deleted_business_messages = None
        event.callback_query = None

        mock_inline_query = MagicMock()
        mock_inline_query.answer = AsyncMock()
        event.inline_query = mock_inline_query

        data = {"event_from_user": self.user}
        handler = AsyncMock()

        res = await middleware(handler, event, data)
        self.assertIsNone(res)
        handler.assert_not_called()
        mock_inline_query.answer.assert_awaited_once_with([], cache_time=0)

    # 2. AUD-17: .dockerignore checks
    def test_aud17_dockerignore_includes_dev_paths(self):
        dockerignore_path = os.path.join(os.path.dirname(__file__), "..", ".dockerignore")
        with open(dockerignore_path, "r", encoding="utf-8") as f:
            content = f.read()

        required_patterns = ["tests/", "docs/", "data/", ".pytest_cache/", "conftest.py"]
        for pat in required_patterns:
            self.assertIn(pat, content, f"Pattern {pat} missing from .dockerignore")

    # 3. AUD-08: Inline TTS timeout returns placeholder and warms cache in bg
    async def test_aud08_inline_tts_timeout_returns_placeholder_and_bg_caching(self):
        query = MagicMock(spec=InlineQuery)
        query.id = "iq_test_timeout"
        query.from_user = self.user
        query.query = "/tts Long test text for timeout"
        query.bot = self.bot
        query.answer = AsyncMock()

        # Simulate generate_speech taking 0.2s, while we set a tiny timeout or test helper
        async def slow_speech(*args, **kwargs):
            await asyncio.sleep(0.15)
            return b"fake_audio_pcm_bytes"

        inline_handler.gemini_client.generate_speech = AsyncMock(side_effect=slow_speech)

        sent_msg = MagicMock()
        sent_msg.message_id = 456
        sent_msg.voice = MagicMock(file_id="voice_file_bg_cached")
        self.bot.send_voice = AsyncMock(return_value=sent_msg)

        # Mock convert_gemini_audio to avoid ffmpeg
        with patch("handlers.inline.convert_gemini_audio", new_callable=AsyncMock) as mock_conv, \
             patch("handlers.inline.INLINE_TTS_TIMEOUT_SECONDS", 0.05):
            mock_conv.return_value = (b"ogg_data", "voice.ogg", 0)

            await inline_handler.handle_inline(query)

            # 1. query.answer must have received placeholder with cache_time=1
            query.answer.assert_awaited_once()
            call_kw = query.answer.call_args.kwargs
            self.assertEqual(call_kw.get("cache_time"), 1)
            results = call_kw.get("results", [])
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].title, "⏳ Аудио готовится...")

            # 2. Wait a little for background task to complete and warm cache
            await asyncio.sleep(0.2)

            state = await inline_handler.storage.get_settings(self.user.id)
            cached_id = await inline_handler.storage.get_cached_tts_voice(
                text="Long test text for timeout",
                voice=state.tts_voice,
                model=state.tts_model,
            )
            self.assertEqual(cached_id, "voice_file_bg_cached")


    def test_inline_module_type_hints_evaluable(self):
        import typing
        hints = typing.get_type_hints(inline_handler._generate_and_cache_tts)
        self.assertIn("bot", hints)
        self.assertEqual(hints["bot"], Bot)

    def test_all_handlers_type_hints_evaluable_on_python312(self):
        import importlib
        import inspect
        import typing
        for mod_name in (
            "handlers.chat",
            "handlers.inline",
            "handlers.prompts",
            "handlers.business",
            "handlers.admin",
            "handlers.menu",
            "handlers.common",
        ):
            mod = importlib.import_module(mod_name)
            for name, obj in inspect.getmembers(mod):
                if (inspect.isfunction(obj) or inspect.isclass(obj)) and getattr(obj, "__module__", None) == mod_name:
                    hints = typing.get_type_hints(obj)
                    self.assertIsInstance(hints, dict)


if __name__ == "__main__":
    unittest.main()
