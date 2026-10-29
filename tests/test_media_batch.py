"""Unit tests for multimodal media batch builder and summary formatting (media_batch.py)."""

import io
import unittest
from unittest.mock import AsyncMock, MagicMock

from aiogram import Bot
from aiogram.types import Audio, Message, PhotoSize, Voice
from google.genai import types

from media_batch import (
    MediaBatchPayload,
    build_media_batch,
    format_incoming_summary,
)


class TestMediaBatch(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = AsyncMock(spec=Bot)

    def test_dataclass_defaults(self):
        payload = MediaBatchPayload()
        self.assertEqual(payload.combined_text, "")
        self.assertEqual(payload.gemini_parts, [])
        self.assertEqual(payload.media_badges, [])
        self.assertFalse(payload.has_media)

    async def test_empty_batch(self):
        payload = await build_media_batch(self.bot, [])
        self.assertEqual(payload.combined_text, "")
        self.assertEqual(payload.gemini_parts, [])
        self.assertEqual(payload.media_badges, [])
        self.assertFalse(payload.has_media)
        self.bot.download.assert_not_called()

    async def test_text_only_batch(self):
        m1 = MagicMock(spec=Message)
        m1.text = "Здравствуйте!"
        m1.caption = None
        m1.voice = None
        m1.photo = None
        m1.audio = None

        m2 = MagicMock(spec=Message)
        m2.text = "Подскажите цену и наличие."
        m2.caption = None
        m2.voice = None
        m2.photo = None
        m2.audio = None

        payload = await build_media_batch(self.bot, [m1, m2])
        self.assertEqual(payload.combined_text, "Здравствуйте!\nПодскажите цену и наличие.")
        self.assertEqual(payload.gemini_parts, [])
        self.assertEqual(payload.media_badges, [])
        self.assertFalse(payload.has_media)
        self.bot.download.assert_not_called()

    async def test_voice_message_batch(self):
        fake_bytes = b"fake_ogg_voice_content"
        self.bot.download = AsyncMock(return_value=io.BytesIO(fake_bytes))

        m = MagicMock(spec=Message)
        m.text = None
        m.caption = None
        m.voice = MagicMock(spec=Voice)
        m.voice.file_id = "voice_file_123"
        m.voice.duration = 15
        m.photo = None
        m.audio = None

        payload = await build_media_batch(self.bot, [m])
        self.assertTrue(payload.has_media)
        self.assertEqual(len(payload.gemini_parts), 1)

        part = payload.gemini_parts[0]
        self.assertIsInstance(part, types.Part)
        self.assertIsNotNone(part.inline_data)
        self.assertEqual(part.inline_data.mime_type, "audio/ogg")
        self.assertEqual(part.inline_data.data, fake_bytes)

        self.assertEqual(len(payload.media_badges), 1)
        self.assertTrue(payload.media_badges[0].startswith("🎙 Голосовое"))
        self.assertIn("15", payload.media_badges[0])

        self.bot.download.assert_awaited_once_with("voice_file_123")

    async def test_photo_message_batch(self):
        fake_bytes = b"fake_jpeg_photo_content"
        self.bot.download = AsyncMock(return_value=io.BytesIO(fake_bytes))

        small_photo = MagicMock(spec=PhotoSize)
        small_photo.file_id = "small_1"
        large_photo = MagicMock(spec=PhotoSize)
        large_photo.file_id = "large_2"

        m = MagicMock(spec=Message)
        m.text = None
        m.caption = "Вот фото детали"
        m.voice = None
        m.photo = [small_photo, large_photo]
        m.audio = None

        payload = await build_media_batch(self.bot, [m])
        self.assertTrue(payload.has_media)
        self.assertEqual(payload.combined_text, "Вот фото детали")
        self.assertEqual(len(payload.gemini_parts), 1)

        part = payload.gemini_parts[0]
        self.assertIsInstance(part, types.Part)
        self.assertIsNotNone(part.inline_data)
        self.assertEqual(part.inline_data.mime_type, "image/jpeg")
        self.assertEqual(part.inline_data.data, fake_bytes)

        self.assertEqual(payload.media_badges, ["📷 Фото"])
        self.bot.download.assert_awaited_once_with("large_2")

    async def test_audio_message_batch(self):
        fake_bytes = b"fake_mp3_content"
        self.bot.download = AsyncMock(return_value=io.BytesIO(fake_bytes))

        m = MagicMock(spec=Message)
        m.text = None
        m.caption = "Музыкальный трек"
        m.voice = None
        m.photo = None
        m.audio = MagicMock(spec=Audio)
        m.audio.file_id = "audio_789"
        m.audio.mime_type = "audio/mpeg"

        payload = await build_media_batch(self.bot, [m])
        self.assertTrue(payload.has_media)
        self.assertEqual(payload.combined_text, "Музыкальный трек")
        self.assertEqual(len(payload.gemini_parts), 1)

        part = payload.gemini_parts[0]
        self.assertEqual(part.inline_data.mime_type, "audio/mpeg")
        self.assertEqual(part.inline_data.data, fake_bytes)
        self.assertIn("🎵 Аудио", payload.media_badges)

        self.bot.download.assert_awaited_once_with("audio_789")

    async def test_mixed_batch(self):
        self.bot.download = AsyncMock(
            side_effect=[
                io.BytesIO(b"photo_data"),
                io.BytesIO(b"voice_data"),
            ]
        )

        m1 = MagicMock(spec=Message)
        m1.text = "Первая часть вопроса"
        m1.caption = None
        m1.voice = None
        m1.photo = None
        m1.audio = None

        m2 = MagicMock(spec=Message)
        m2.text = None
        m2.caption = "Посмотрите это фото"
        m2.voice = None
        photo_obj = MagicMock(spec=PhotoSize)
        photo_obj.file_id = "photo_xyz"
        m2.photo = [photo_obj]
        m2.audio = None

        m3 = MagicMock(spec=Message)
        m3.text = None
        m3.caption = None
        m3.voice = MagicMock(spec=Voice)
        m3.voice.file_id = "voice_xyz"
        m3.voice.duration = 24
        m3.photo = None
        m3.audio = None

        payload = await build_media_batch(self.bot, [m1, m2, m3])
        self.assertTrue(payload.has_media)
        self.assertEqual(payload.combined_text, "Первая часть вопроса\nПосмотрите это фото")
        self.assertEqual(len(payload.gemini_parts), 2)
        self.assertEqual(payload.gemini_parts[0].inline_data.mime_type, "image/jpeg")
        self.assertEqual(payload.gemini_parts[1].inline_data.mime_type, "audio/ogg")

        self.assertEqual(len(payload.media_badges), 2)
        self.assertIn("📷 Фото", payload.media_badges)
        self.assertTrue(any("🎙 Голосовое" in b for b in payload.media_badges))

    async def test_safe_limit_oversized_media(self):
        # 21 MB payload
        oversized_data = b"x" * (21 * 1024 * 1024)
        self.bot.download = AsyncMock(return_value=io.BytesIO(oversized_data))

        m = MagicMock(spec=Message)
        m.text = None
        m.caption = None
        m.voice = MagicMock(spec=Voice)
        m.voice.file_id = "huge_voice"
        m.voice.duration = 600
        m.photo = None
        m.audio = None

        payload = await build_media_batch(self.bot, [m])
        self.assertEqual(len(payload.gemini_parts), 0)
        self.assertIn("⚠️ Медиа > 15 МБ пропущено", payload.media_badges)


class TestFormatIncomingSummary(unittest.TestCase):
    def test_text_only(self):
        payload = MediaBatchPayload(combined_text="Простой текст сообщения")
        summary = format_incoming_summary(payload)
        self.assertEqual(summary, "Простой текст сообщения")

    def test_text_truncation(self):
        long_text = "слово " * 100
        payload = MediaBatchPayload(combined_text=long_text)
        summary = format_incoming_summary(payload, max_len=50)
        self.assertLessEqual(len(summary), 50)
        self.assertTrue(summary.endswith("…"))

    def test_voice_badge_formatting(self):
        payload = MediaBatchPayload(
            combined_text="Текст реплики клиента",
            media_badges=["🎙 Голосовое (0:15)"],
        )
        summary = format_incoming_summary(payload)
        self.assertEqual(summary, "[🎙 Голосовое (0:15)]\nТекст реплики клиента")

    def test_photo_badge_formatting(self):
        payload = MediaBatchPayload(
            combined_text="Подпись к фото",
            media_badges=["📷 Фото"],
        )
        summary = format_incoming_summary(payload)
        self.assertEqual(summary, "[📷 Фото] Подпись к фото")

    def test_media_only_no_text(self):
        payload = MediaBatchPayload(
            combined_text="",
            media_badges=["📷 Фото"],
        )
        summary = format_incoming_summary(payload)
        self.assertEqual(summary, "[📷 Фото]")

    def test_multiple_badges_formatting(self):
        payload = MediaBatchPayload(
            combined_text="Пояснение к материалам",
            media_badges=["📷 Фото", "🎙 Голосовое (0:24)"],
        )
        summary = format_incoming_summary(payload)
        self.assertEqual(
            summary,
            "[📷 Фото] [🎙 Голосовое (0:24)]\nПояснение к материалам",
        )


if __name__ == "__main__":
    unittest.main()
