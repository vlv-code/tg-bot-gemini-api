import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456789:TEST_MOCK_TOKEN_FOR_UNITTESTS")
os.environ.setdefault("GEMINI_API_KEY", "AIzaSy_TEST_MOCK_KEY_FOR_UNITTESTS")

from handlers.chat import handle_voice


class TestChatVoiceTurn(unittest.IsolatedAsyncioTestCase):
    async def test_handle_voice_turn(self):
        fake_message = MagicMock()
        fake_message.from_user.id = 12345678
        fake_message.voice = MagicMock()
        fake_message.voice.file_id = "voice_file_123"
        fake_message.voice.mime_type = "audio/ogg"
        fake_message.voice.file_size = 1024
        fake_message.audio = None
        fake_message.caption = None
        fake_message.bot = MagicMock()

        async def fake_download(file_id, destination):
            destination.write(b"fake_voice_bytes")

        fake_message.bot.download = AsyncMock(side_effect=fake_download)

        with patch("handlers.chat._process_user_turn", new_callable=AsyncMock) as mock_process:
            await handle_voice(message=fake_message)
            mock_process.assert_awaited_once()
            call_kwargs = mock_process.call_args.kwargs
            self.assertEqual(call_kwargs["message"], fake_message)
            self.assertEqual(call_kwargs["history_text"], "[Голосовое сообщение] Ответь на аудиосообщение.")


if __name__ == "__main__":
    unittest.main()
