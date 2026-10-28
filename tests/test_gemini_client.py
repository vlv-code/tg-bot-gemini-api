import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:FAKE_TOKEN_FOR_TESTS")
os.environ.setdefault("GEMINI_API_KEY", "FAKE_KEY_FOR_TESTS")

from gemini_client import GeminiClient, GeminiError, GeminiResponse, Turn


class TestGeminiClientFallbacks(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = GeminiClient(
            api_key="FAKE_KEY",
            default_model="gemini-3.5-flash-lite",
            timeout_seconds=0.5,
        )
        self.client._client = MagicMock()

    def test_client_init_defaults(self):
        self.assertEqual(self.client.default_model, "gemini-3.5-flash-lite")
        self.assertEqual(self.client.timeout_seconds, 0.5)

    async def test_ask_fallback_on_503(self):
        mock_response = MagicMock()
        mock_response.text = "Ответ от быстрой модели"
        mock_response.candidates = []

        mock_failing_chat = MagicMock()
        mock_failing_chat.send_message = AsyncMock(side_effect=Exception("503 UNAVAILABLE: Model overloaded"))

        mock_success_chat = MagicMock()
        mock_success_chat.send_message = AsyncMock(return_value=mock_response)

        calls = []

        def fake_create(model, config, history):
            calls.append(model)
            if model == "gemini-4-argon":
                return mock_failing_chat
            return mock_success_chat

        self.client._client.aio.chats.create.side_effect = fake_create

        res = await self.client.ask(
            model="gemini-4-argon",
            history_turns=[],
            message="Привет",
        )
        self.assertEqual(res.text, "Ответ от быстрой модели")
        self.assertEqual(calls, ["gemini-4-argon", "gemini-3.5-flash-lite"])

    async def test_ask_fallback_on_timeout(self):
        mock_response = MagicMock()
        mock_response.text = "Ответ после таймаута первой модели"
        mock_response.candidates = []

        mock_timeout_chat = MagicMock()
        mock_timeout_chat.send_message = AsyncMock(side_effect=TimeoutError())

        mock_success_chat = MagicMock()
        mock_success_chat.send_message = AsyncMock(return_value=mock_response)

        calls = []

        def fake_create(model, config, history):
            calls.append(model)
            if model == "gemini-3.8-flash":
                return mock_timeout_chat
            return mock_success_chat

        self.client._client.aio.chats.create.side_effect = fake_create

        res = await self.client.ask(
            model="gemini-3.8-flash",
            history_turns=[],
            message="Привет",
        )
        self.assertEqual(res.text, "Ответ после таймаута первой модели")
        self.assertEqual(calls, ["gemini-3.8-flash", "gemini-3.5-flash-lite"])

    async def test_ask_no_fallback_if_already_default_model(self):
        mock_failing_chat = MagicMock()
        mock_failing_chat.send_message = AsyncMock(side_effect=Exception("503 UNAVAILABLE: Model overloaded"))

        calls = []

        def fake_create(model, config, history):
            calls.append(model)
            return mock_failing_chat

        self.client._client.aio.chats.create.side_effect = fake_create

        with self.assertRaises(GeminiError):
            await self.client.ask(
                model="gemini-3.5-flash-lite",
                history_turns=[],
                message="Привет",
            )
        self.assertEqual(calls, ["gemini-3.5-flash-lite"])

    async def test_generate_speech_sends_exact_text_without_prompt_leakage(self):
        """Проверяем, что в TTS передаётся чистый текст без префиксов 'Прочитай следующий текст'."""
        mock_response = MagicMock()
        mock_part = MagicMock()
        mock_part.inline_data.data = b"RIFFfakeaudio"
        mock_candidate = MagicMock()
        mock_candidate.content.parts = [mock_part]
        mock_response.candidates = [mock_candidate]

        self.client._client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        audio_bytes = await self.client.generate_speech(
            text="Привет! Чем могу помочь?",
            voice_name="Puck",
            model="gemini-3.8-flash-lite-tts",
        )
        self.assertEqual(audio_bytes, b"RIFFfakeaudio")

        # Проверяем переданные аргументы в generate_content
        call_kwargs = self.client._client.aio.models.generate_content.call_args.kwargs
        self.assertEqual(call_kwargs["contents"], "Привет! Чем могу помочь?")
        self.assertNotIn("Прочитай следующий текст", call_kwargs["contents"])



if __name__ == "__main__":
    unittest.main()
