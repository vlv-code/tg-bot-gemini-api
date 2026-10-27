import io
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:FAKE_TOKEN_FOR_TESTS")
os.environ.setdefault("GEMINI_API_KEY", "FAKE_KEY_FOR_TESTS")

from aiogram import Bot
from aiogram.types import Message, PhotoSize, Voice
from google.genai import types

import handlers.business as biz
from draft_editor import DraftActionType, parse_reply_action
from gemini_client import GeminiResponse
from media_batch import build_media_batch, format_incoming_summary

OWNER_ID = 2000
CONN_ID = "conn-pipeline"
CLIENT_CHAT_ID = 777777


def _fake_bot():
    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock(side_effect=lambda **kw: MagicMock(message_id=999, **kw))
    bot.edit_message_text = AsyncMock()
    bot.download = AsyncMock(return_value=io.BytesIO(b"fake_audio_or_photo_data"))
    return bot


def _fake_connection_event(conn_id=CONN_ID, owner_id=OWNER_ID, can_reply=True, is_enabled=True):
    event = MagicMock()
    event.id = conn_id
    event.user = MagicMock(id=owner_id)
    event.user_chat_id = owner_id
    event.is_enabled = is_enabled
    event.rights = MagicMock(can_reply=can_reply)
    return event


class TestBusinessPipeline(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp()
        biz.storage._db_path = os.path.join(self.test_dir, "test_pipeline.db")
        biz.storage._db = None
        await biz.storage.init_db()
        biz._pending_drafts.clear()
        biz.draft_registry = biz.DraftRegistry()
        biz.debounce_manager.delay_seconds = 0  # Synchronous flush in base tests
        biz.limiter._minute_hits.pop(OWNER_ID, None)
        biz.limiter._day_hits.pop(OWNER_ID, None)

        # Set up active business connection
        self.bot = _fake_bot()
        await biz.on_business_connection(_fake_connection_event(), self.bot)

    async def asyncTearDown(self):
        await biz.storage.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    async def test_debounce_batching_multiple_messages(self):
        """Проверяем, что несколько сообщений склеиваются в единый черновик."""
        biz.gemini_client.ask = AsyncMock(
            return_value=GeminiResponse(text="Здравствуйте! Да, доставим в четверг.", total_tokens=15)
        )

        msg1 = MagicMock(spec=Message)
        msg1.business_connection_id = CONN_ID
        msg1.chat = MagicMock(id=CLIENT_CHAT_ID, first_name="Иван", title=None, full_name="Иван")
        msg1.from_user = MagicMock(id=555)
        msg1.message_id = 101
        msg1.text = "Здравствуйте!"
        msg1.caption = None
        msg1.photo = None
        msg1.voice = None
        msg1.audio = None
        msg1.bot = self.bot

        msg2 = MagicMock(spec=Message)
        msg2.business_connection_id = CONN_ID
        msg2.chat = MagicMock(id=CLIENT_CHAT_ID, first_name="Иван", title=None, full_name="Иван")
        msg2.from_user = MagicMock(id=555)
        msg2.message_id = 102
        msg2.text = "А в Северный район есть доставка?"
        msg2.caption = None
        msg2.photo = None
        msg2.voice = None
        msg2.audio = None
        msg2.bot = self.bot

        # Запускаем обработку батча сообщений
        await biz._process_batched_business_messages(self.bot, CONN_ID, CLIENT_CHAT_ID, [msg1, msg2])

        # Проверяем, что владельцу отправлен ровно один черновик
        self.assertEqual(len(biz._pending_drafts), 1)
        draft_id = next(iter(biz._pending_drafts))
        draft = biz._pending_drafts[draft_id]

        self.assertIn("Здравствуйте!\nА в Северный район есть доставка?", draft.incoming_text)
        self.assertIn("Да, доставим в четверг", draft.draft_text)

        # Проверяем, что карточка зарегистрирована в draft_registry
        self.assertEqual(biz.draft_registry.get_draft_id_by_card(999), draft_id)

    async def test_multimodal_voice_and_photo_batch(self):
        """Проверяем мультимодальный сбор голосового и фото с бейджами в саммари."""
        biz.gemini_client.ask = AsyncMock(
            return_value=GeminiResponse(text="Вижу фото поломки и прослушал аудио. Мастер будет завтра.", total_tokens=25)
        )

        # 1. Голосовое сообщение
        voice_mock = MagicMock(spec=Voice)
        voice_mock.file_id = "voice_file_123"
        voice_mock.duration = 18

        msg_voice = MagicMock(spec=Message)
        msg_voice.business_connection_id = CONN_ID
        msg_voice.chat = MagicMock(id=CLIENT_CHAT_ID, first_name="Мария", title=None, full_name="Мария")
        msg_voice.from_user = MagicMock(id=666)
        msg_voice.message_id = 201
        msg_voice.text = None
        msg_voice.caption = None
        msg_voice.voice = voice_mock
        msg_voice.photo = None
        msg_voice.audio = None
        msg_voice.bot = self.bot

        # 2. Фото с подписью
        photo_mock = MagicMock(spec=PhotoSize)
        photo_mock.file_id = "photo_file_456"

        msg_photo = MagicMock(spec=Message)
        msg_photo.business_connection_id = CONN_ID
        msg_photo.chat = MagicMock(id=CLIENT_CHAT_ID, first_name="Мария", title=None, full_name="Мария")
        msg_photo.from_user = MagicMock(id=666)
        msg_photo.message_id = 202
        msg_photo.text = None
        msg_photo.caption = "Вот фото поломки"
        msg_photo.voice = None
        msg_photo.photo = [photo_mock]
        msg_photo.audio = None
        msg_photo.bot = self.bot

        await biz._process_batched_business_messages(self.bot, CONN_ID, CLIENT_CHAT_ID, [msg_voice, msg_photo])

        self.assertEqual(len(biz._pending_drafts), 1)
        draft_id = next(iter(biz._pending_drafts))
        draft = biz._pending_drafts[draft_id]

        # В саммари должны быть бейджи [🎙 Голосовое (18с)] и [📷 Фото]
        self.assertIn("🎙 Голосовое (18с)", draft.incoming_summary)
        self.assertIn("📷 Фото", draft.incoming_summary)
        self.assertIn("Вот фото поломки", draft.incoming_summary)

        # В gemini_client.ask должны быть переданы медиа-части
        call_kwargs = biz.gemini_client.ask.await_args.kwargs
        media_message = call_kwargs["message"]
        self.assertIsInstance(media_message, list)
        self.assertGreaterEqual(len(media_message), 3)  # text + voice part + photo part

    async def test_reply_to_edit_instant_send(self):
        """Проверяем моментальную отправку клиенту по реплаю с точкой ('.Текст')."""
        # Создаем активный черновик
        draft_id = "test-draft-1"
        card_id = 8888
        draft = biz.PendingDraft(
            draft_id=draft_id,
            business_connection_id=CONN_ID,
            chat_id=CLIENT_CHAT_ID,
            owner_user_id=OWNER_ID,
            draft_text="Исходный черновик",
            incoming_text="Когда приедете?",
            counterparty_name="Клиент",
        )
        biz._pending_drafts[draft_id] = draft
        biz.draft_registry.register_card(card_id, draft_id)

        # Реплай владельца в личке бота: ".Будем ровно в 15:00"
        reply_msg = MagicMock(spec=Message)
        reply_msg.chat = MagicMock(type="private", id=OWNER_ID)
        reply_msg.from_user = MagicMock(id=OWNER_ID)
        reply_msg.text = ".Будем ровно в 15:00"
        reply_msg.caption = None
        reply_msg.reply_to_message = MagicMock(message_id=card_id)
        reply_msg.answer = AsyncMock()

        await biz.on_draft_reply(reply_msg, self.bot)

        # Проверяем:
        # 1. Сообщение отправлено клиенту напрямую с business_connection_id
        self.bot.send_message.assert_awaited_with(
            chat_id=CLIENT_CHAT_ID,
            text="Будем ровно в 15:00",
            business_connection_id=CONN_ID,
        )
        # 2. Черновик удален из pending и из registry
        self.assertNotIn(draft_id, biz._pending_drafts)
        self.assertIsNone(biz.draft_registry.get_draft_id_by_card(card_id))
        # 3. Карточка отредактирована как закрытая
        self.bot.edit_message_text.assert_awaited()
        # 4. Владельцу отправлено подтверждение
        reply_msg.answer.assert_awaited_with("✅ Ответ отправлен собеседнику напрямую!")

    async def test_reply_to_edit_gemini_revision(self):
        """Проверяем докрутку черновика через Gemini по обычному реплаю владельца."""
        draft_id = "test-draft-2"
        card_id = 7777
        draft = biz.PendingDraft(
            draft_id=draft_id,
            business_connection_id=CONN_ID,
            chat_id=CLIENT_CHAT_ID,
            owner_user_id=OWNER_ID,
            draft_text="Доставка 500 рублей.",
            incoming_text="Сколько стоит доставка?",
            counterparty_name="Клиент",
        )
        biz._pending_drafts[draft_id] = draft
        biz.draft_registry.register_card(card_id, draft_id)

        biz.gemini_client.ask = AsyncMock(
            return_value=GeminiResponse(
                text="Доставка 500 рублей, но при заказе от 3000 рублей — бесплатно!",
                total_tokens=20,
            )
        )

        reply_msg = MagicMock(spec=Message)
        reply_msg.chat = MagicMock(type="private", id=OWNER_ID)
        reply_msg.from_user = MagicMock(id=OWNER_ID)
        reply_msg.text = "добавь что от 3000 рублей бесплатно"
        reply_msg.caption = None
        reply_msg.reply_to_message = MagicMock(message_id=card_id)
        reply_msg.answer = AsyncMock(return_value=MagicMock(delete=AsyncMock()))

        await biz.on_draft_reply(reply_msg, self.bot)

        # Черновик остался активным, но текст обновился!
        self.assertIn(draft_id, biz._pending_drafts)
        self.assertIn("бесплатно", draft.draft_text)

        # Карточка в Telegram перерисована
        self.bot.edit_message_text.assert_awaited()
