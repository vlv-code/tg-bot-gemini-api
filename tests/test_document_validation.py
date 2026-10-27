import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:FAKE_TOKEN_FOR_TESTS")
os.environ.setdefault("GEMINI_API_KEY", "FAKE_KEY_FOR_TESTS")

from handlers.chat import (
    MAX_DOCUMENT_SIZE_BYTES,
    DocumentValidationError,
    _validate_document,
    handle_document,
    handle_voice,
)


class DummyDocument:
    def __init__(self, file_id="file_123", file_size=1024, mime_type="text/plain", file_name="notes.txt"):
        self.file_id = file_id
        self.file_size = file_size
        self.mime_type = mime_type
        self.file_name = file_name


class TestDocumentValidation(unittest.IsolatedAsyncioTestCase):
    def test_validate_document_valid_cases(self):
        # 1. Plain text
        doc = DummyDocument(file_size=1024, mime_type="text/plain", file_name="notes.txt")
        self.assertIsNone(_validate_document(doc))

        # 2. PDF
        doc = DummyDocument(file_size=5 * 1024 * 1024, mime_type="application/pdf", file_name="report.pdf")
        self.assertIsNone(_validate_document(doc))

        # 3. Code file with allowed extension and no mime_type
        doc = DummyDocument(file_size=2048, mime_type=None, file_name="script.py")
        self.assertIsNone(_validate_document(doc))

        # 4. JSON file with application/octet-stream
        doc = DummyDocument(file_size=2048, mime_type="application/octet-stream", file_name="config.json")
        self.assertIsNone(_validate_document(doc))

    def test_validate_document_size_limits(self):
        # Missing file_size
        doc = DummyDocument(file_size=None)
        err = _validate_document(doc)
        self.assertIsNotNone(err)
        self.assertIn("Не удалось определить размер", err)

        # Oversized file (> 15 MB)
        doc = DummyDocument(file_size=MAX_DOCUMENT_SIZE_BYTES + 1024, mime_type="application/pdf", file_name="big.pdf")
        err = _validate_document(doc)
        self.assertIsNotNone(err)
        self.assertIn("Файл слишком большой", err)
        self.assertIn("15 МБ", err)

    def test_validate_document_disallowed_types(self):
        # Executable with explicit MIME
        doc = DummyDocument(file_size=1024, mime_type="application/x-msdos-program", file_name="setup.exe")
        err = _validate_document(doc)
        self.assertIsNotNone(err)
        self.assertIn("Неподдерживаемый тип файла", err)

        # ISO image with octet-stream
        doc = DummyDocument(file_size=1024, mime_type="application/octet-stream", file_name="ubuntu.iso")
        err = _validate_document(doc)
        self.assertIsNotNone(err)
        self.assertIn("Неподдерживаемый тип файла", err)

    async def test_handle_document_blocks_oversized_without_downloading(self):
        message = MagicMock()
        message.from_user.id = 12345
        message.document = DummyDocument(file_size=20 * 1024 * 1024, mime_type="application/pdf", file_name="huge.pdf")
        message.bot = MagicMock()
        message.bot.download = AsyncMock()
        message.answer = AsyncMock()

        await handle_document(message)

        # Проверяем, что пользователю отправлен ответ об ошибке размера
        message.answer.assert_called_once()
        sent_text = message.answer.call_args[0][0]
        self.assertIn("Файл слишком большой", sent_text)

        # Скачивание файла в память НЕ должно вызываться!
        message.bot.download.assert_not_called()

    async def test_handle_document_blocks_disallowed_format_without_downloading(self):
        message = MagicMock()
        message.from_user.id = 12345
        message.document = DummyDocument(file_size=1024, mime_type="application/x-executable", file_name="danger.bin")
        message.bot = MagicMock()
        message.bot.download = AsyncMock()
        message.answer = AsyncMock()

        await handle_document(message)

        message.answer.assert_called_once()
        sent_text = message.answer.call_args[0][0]
        self.assertIn("Неподдерживаемый тип файла", sent_text)

        message.bot.download.assert_not_called()


    async def test_handle_voice_blocks_oversized_audio_without_downloading(self):
        message = MagicMock()
        message.from_user.id = 12345
        message.voice = None
        message.audio = DummyDocument(file_size=25 * 1024 * 1024, mime_type="audio/mp3", file_name="podcast.mp3")
        message.caption = None
        message.bot = MagicMock()
        message.bot.download = AsyncMock()
        message.answer = AsyncMock()

        await handle_voice(message)

        message.answer.assert_called_once()
        sent_text = message.answer.call_args[0][0]
        self.assertIn("Аудиофайл слишком большой", sent_text)
        message.bot.download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
