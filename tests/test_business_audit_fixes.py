import io
import os
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:FAKE_TOKEN_FOR_TESTS")
os.environ.setdefault("GEMINI_API_KEY", "FAKE_KEY_FOR_TESTS")

from aiogram import Bot
from aiogram.types import Document, Message
from google.genai import types

import handlers.business as biz
from gemini_client import GeminiResponse
import media_batch


OWNER_ID = 3000
CONN_ID = "conn-audit-fixes"
CLIENT_CHAT_ID = 888888


def _fake_bot():
    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock(side_effect=lambda **kw: MagicMock(message_id=999, **kw))
    bot.edit_message_text = AsyncMock()
    bot.download = AsyncMock(return_value=io.BytesIO(b"fake_document_content"))
    return bot


def _fake_connection_event(conn_id=CONN_ID, owner_id=OWNER_ID, can_reply=True, is_enabled=True):
    event = MagicMock()
    event.id = conn_id
    event.user = MagicMock(id=owner_id)
    event.user_chat_id = owner_id
    event.is_enabled = is_enabled
    event.rights = MagicMock(can_reply=can_reply)
    return event


def _fake_callback(user_id, data, message=None):
    cb = MagicMock()
    cb.from_user = MagicMock(id=user_id)
    cb.data = data
    cb.message = message or MagicMock(edit_text=AsyncMock())
    cb.answer = AsyncMock()
    return cb


class TestBusinessAuditFixes(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp()
        biz.storage._db_path = os.path.join(self.test_dir, "test_audit.db")
        biz.storage._db = None
        await biz.storage.init_db()
        biz._pending_drafts.clear()
        biz.draft_registry = biz.DraftRegistry()
        biz.debounce_manager.delay_seconds = 0
        biz.limiter._minute_hits.pop(OWNER_ID, None)
        biz.limiter._day_hits.pop(OWNER_ID, None)
        self.bot = _fake_bot()
        await biz.on_business_connection(_fake_connection_event(), self.bot)

    async def asyncTearDown(self):
        await biz.storage.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # 1. AUD-15: Unified media limit
    def test_aud15_media_limit_constant(self):
        self.assertEqual(media_batch.MAX_MEDIA_BYTES, 15 * 1024 * 1024)

    # 2. AUD-07: Document support in media_batch and business message handler
    async def test_aud07_document_batching_within_limit(self):
        msg = MagicMock(spec=Message)
        msg.text = None
        msg.caption = "Подпись к документу"
        msg.voice = None
        msg.photo = None
        msg.audio = None
        doc = MagicMock(spec=Document)
        doc.file_id = "doc_file_123"
        doc.file_size = 5 * 1024 * 1024
        doc.mime_type = "application/pdf"
        msg.document = doc

        payload = await media_batch.build_media_batch(self.bot, [msg])
        self.assertTrue(payload.has_media)
        self.assertIn("📄 Документ", payload.media_badges)
        self.assertEqual(len(payload.gemini_parts), 1)
        part = payload.gemini_parts[0]
        self.assertEqual(part.inline_data.mime_type, "application/pdf")
        self.assertIn("Подпись к документу", payload.combined_text)

    async def test_aud07_document_batching_exceeding_limit(self):
        msg = MagicMock(spec=Message)
        msg.text = None
        msg.caption = None
        msg.voice = None
        msg.photo = None
        msg.audio = None
        doc = MagicMock(spec=Document)
        doc.file_id = "doc_file_large"
        doc.file_size = 16 * 1024 * 1024
        doc.mime_type = "application/pdf"
        msg.document = doc

        payload = await media_batch.build_media_batch(self.bot, [msg])
        self.assertFalse(payload.has_media)
        self.assertIn("⚠️ Документ > 15 МБ пропущен", payload.media_badges)
        self.assertEqual(len(payload.gemini_parts), 0)

    async def test_aud07_business_message_document_accepted(self):
        msg = MagicMock(spec=Message)
        msg.business_connection_id = CONN_ID
        msg.chat = MagicMock(id=CLIENT_CHAT_ID, first_name="Клиент", title=None, full_name="Клиент")
        msg.from_user = MagicMock(id=555)
        msg.message_id = 201
        msg.text = None
        msg.caption = None
        msg.voice = None
        msg.photo = None
        msg.audio = None
        doc = MagicMock(spec=Document)
        doc.file_id = "doc_file_999"
        doc.file_size = 1024
        doc.mime_type = "text/plain"
        msg.document = doc

        with patch.object(biz.debounce_manager, "push", new_callable=AsyncMock) as mock_push:
            await biz.on_business_message(msg, self.bot)
            mock_push.assert_awaited_once()

    # 3. AUD-10: Atomic user/model turn recording
    async def test_aud10_pending_draft_has_turn_text_and_no_orphan_user_turn(self):
        biz.gemini_client.ask = AsyncMock(
            return_value=GeminiResponse(text="Ответ ассистента", total_tokens=10)
        )
        msg = MagicMock(spec=Message)
        msg.business_connection_id = CONN_ID
        msg.chat = MagicMock(id=CLIENT_CHAT_ID, first_name="Клиент", title=None, full_name="Клиент")
        msg.from_user = MagicMock(id=555)
        msg.message_id = 301
        msg.text = "Вопрос клиента"
        msg.caption = None
        msg.photo = None
        msg.voice = None
        msg.audio = None
        msg.document = None
        msg.answer = AsyncMock()

        await biz._process_batched_business_messages(self.bot, CONN_ID, CLIENT_CHAT_ID, [msg])

        # 1. Draft created
        self.assertEqual(len(biz._pending_drafts), 1)
        draft_id = next(iter(biz._pending_drafts))
        draft = biz._pending_drafts[draft_id]
        self.assertEqual(draft.turn_text, "Вопрос клиента")

        # 2. History must NOT have user turn yet!
        state = await biz.storage.get(OWNER_ID, chat_id=CLIENT_CHAT_ID, mode="business")
        self.assertEqual(len(state.history), 0, "User turn must NOT be stored before draft approval")

        # 3. Discard draft -> history remains empty
        cb_discard = _fake_callback(OWNER_ID, f"biz_draft:discard:{draft_id}")
        await biz.cb_business_draft(cb_discard, self.bot)
        state_after_discard = await biz.storage.get(OWNER_ID, chat_id=CLIENT_CHAT_ID, mode="business")
        self.assertEqual(len(state_after_discard.history), 0, "Discarded draft must leave history empty")

    async def test_aud10_approved_draft_records_both_turns_atomically(self):
        biz.gemini_client.ask = AsyncMock(
            return_value=GeminiResponse(text="Ответ ассистента", total_tokens=10)
        )
        msg = MagicMock(spec=Message)
        msg.business_connection_id = CONN_ID
        msg.chat = MagicMock(id=CLIENT_CHAT_ID, first_name="Клиент", title=None, full_name="Клиент")
        msg.from_user = MagicMock(id=555)
        msg.message_id = 302
        msg.text = "Сколько стоит услуга?"
        msg.caption = None
        msg.photo = None
        msg.voice = None
        msg.audio = None
        msg.document = None
        msg.answer = AsyncMock()

        await biz._process_batched_business_messages(self.bot, CONN_ID, CLIENT_CHAT_ID, [msg])
        draft_id = next(iter(biz._pending_drafts))

        # Approve and send draft
        cb_send = _fake_callback(OWNER_ID, f"biz_draft:send:{draft_id}")
        await biz.cb_business_draft(cb_send, self.bot)

        # Both user and model turns must be present in history
        state = await biz.storage.get(OWNER_ID, chat_id=CLIENT_CHAT_ID, mode="business")
        turns = [(t.role, t.text) for t in state.history]
        self.assertEqual(turns, [
            ("user", "Сколько стоит услуга?"),
            ("model", "Ответ ассистента"),
        ])

    async def test_aud10_autoreply_records_both_turns(self):
        await biz.storage.set_auto_reply(CONN_ID, CLIENT_CHAT_ID, True)
        biz.gemini_client.ask = AsyncMock(
            return_value=GeminiResponse(text="Авто-ответ", total_tokens=10)
        )
        msg = MagicMock(spec=Message)
        msg.business_connection_id = CONN_ID
        msg.chat = MagicMock(id=CLIENT_CHAT_ID, first_name="Клиент", title=None, full_name="Клиент")
        msg.from_user = MagicMock(id=555)
        msg.message_id = 303
        msg.text = "Здравствуйте"
        msg.caption = None
        msg.photo = None
        msg.voice = None
        msg.audio = None
        msg.document = None
        msg.answer = AsyncMock()

        await biz._process_batched_business_messages(self.bot, CONN_ID, CLIENT_CHAT_ID, [msg])

        state = await biz.storage.get(OWNER_ID, chat_id=CLIENT_CHAT_ID, mode="business")
        turns = [(t.role, t.text) for t in state.history]
        self.assertEqual(turns, [
            ("user", "Здравствуйте"),
            ("model", "Авто-ответ"),
        ])

    # 4. AUD-09: Token usage recorded with response.used_model
    async def test_aud09_token_usage_records_used_model(self):
        mock_response = GeminiResponse(text="Ответ", total_tokens=42, prompt_tokens=20, candidates_tokens=22)
        mock_response.used_model = "gemini-3.8-flash-fallback-tested"
        biz.gemini_client.ask = AsyncMock(return_value=mock_response)

        with patch.object(biz.storage, "record_token_usage", new_callable=AsyncMock) as mock_record:
            await biz._generate_secretary_reply(
                owner_user_id=OWNER_ID,
                chat_id=CLIENT_CHAT_ID,
                incoming_text="Привет",
                hint_texts=[],
            )
            mock_record.assert_awaited_once()
            call_kwargs = mock_record.call_args.kwargs
            self.assertEqual(call_kwargs.get("model"), "gemini-3.8-flash-fallback-tested")

    # 5. AUD-06: Onboarding text update
    async def test_aud06_onboarding_text_explains_keywords_and_gemini_drafts(self):
        bot = _fake_bot()
        event = _fake_connection_event(conn_id="conn-new", owner_id=OWNER_ID, can_reply=True, is_enabled=True)
        await biz.on_business_connection(event, bot)

        bot.send_message.assert_awaited_once()
        sent_text = bot.send_message.call_args.kwargs.get("text", "")
        self.assertIn(
            "Шаблоны ключевых слов отправляются клиенту мгновенно (без участия Gemini); ответы ИИ формируются как черновики на подтверждение",
            sent_text,
        )


if __name__ == "__main__":
    unittest.main()
