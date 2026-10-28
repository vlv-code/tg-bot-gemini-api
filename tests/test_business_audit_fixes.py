import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456789:TEST_MOCK_TOKEN_FOR_UNITTESTS")
os.environ.setdefault("GEMINI_API_KEY", "AIzaSy_TEST_MOCK_KEY_FOR_UNITTESTS")

import handlers.business as biz
from gemini_client import GeminiResponse


class TestBusinessAuditFixes(unittest.IsolatedAsyncioTestCase):
    async def test_bug_13_on_draft_reply_ignores_commands(self):
        """BUG-13: on_draft_reply ignores incoming messages starting with '/' before querying draft_registry."""
        commands = ["/cancel", "/help", "/menu", "/start", "/settings"]
        for cmd in commands:
            with patch.object(biz.draft_registry, "get_draft_id_by_card", return_value="draft_123") as mock_get_draft:
                msg = MagicMock()
                msg.chat.type = "private"
                msg.reply_to_message = MagicMock(message_id=999)
                msg.text = cmd
                msg.answer = AsyncMock()
                bot = MagicMock()

                await biz.on_draft_reply(msg, bot)

                mock_get_draft.assert_not_called()

    async def test_bug_05_generate_secretary_reply_holds_owner_lock(self):
        """BUG-05: _generate_secretary_reply checks and hits rate limiter within owner lock."""
        owner_user_id = 98765
        chat_id = 43210
        owner_lock = biz.user_locks.get(owner_user_id)

        check_locked = None
        hit_locked = None

        async def fake_check(uid):
            nonlocal check_locked
            check_locked = owner_lock.locked()
            status = MagicMock()
            status.allowed = True
            return status

        async def fake_hit(uid):
            nonlocal hit_locked
            hit_locked = owner_lock.locked()

        mock_state = MagicMock()
        mock_state.system_prompt = None
        mock_state.history = []
        mock_state.business_model = "gemini-2.5-flash"
        mock_state.model = "gemini-2.5-flash"

        fake_response = GeminiResponse(
            text="Hello from assistant",
            prompt_tokens=10,
            candidates_tokens=15,
            total_tokens=25,
        )

        with patch.object(biz.limiter, "check", side_effect=fake_check) as mock_check, \
             patch.object(biz.limiter, "hit", side_effect=fake_hit) as mock_hit, \
             patch.object(biz.storage, "get", AsyncMock(return_value=mock_state)), \
             patch.object(biz.storage, "get_business_facts", AsyncMock(return_value=[])), \
             patch.object(biz.storage, "record_token_usage", AsyncMock()), \
             patch.object(biz.gemini_client, "ask", AsyncMock(return_value=fake_response)):

            resp, state = await biz._generate_secretary_reply(
                owner_user_id=owner_user_id,
                chat_id=chat_id,
                incoming_text="Can you assist me?",
                hint_texts=[],
            )

            self.assertIsNotNone(resp)
            mock_check.assert_awaited_once_with(owner_user_id)
            mock_hit.assert_awaited_once_with(owner_user_id)
            self.assertTrue(check_locked, "limiter.check should be executed inside user_locks.get(owner_user_id)")
            self.assertTrue(hit_locked, "limiter.hit should be executed inside user_locks.get(owner_user_id)")
            self.assertFalse(owner_lock.locked(), "owner lock should be released after execution")


if __name__ == "__main__":
    unittest.main()
