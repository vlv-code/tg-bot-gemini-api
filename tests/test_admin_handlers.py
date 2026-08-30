import os
import unittest
import tempfile
import shutil

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:FAKE_TOKEN_FOR_TESTS")
os.environ.setdefault("GEMINI_API_KEY", "FAKE_KEY_FOR_TESTS")

from storage import UserStorage
from handlers.admin import _render_admin_panel_text


class TestAdminHandlersAndStorage(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_admin.db")
        self.storage = UserStorage(
            db_path=self.db_path,
            default_model="gemini-3.5-flash-lite",
            max_history=5,
            default_tts_model="gemini-3.1-flash-tts-preview",
            default_tts_voice="Aoede",
        )
        await self.storage.init_db()

    async def asyncTearDown(self):
        await self.storage.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    async def test_whitelist_toggle_and_aliases(self):
        # Default whitelist mode is False
        mode = await self.storage.get_whitelist_mode()
        self.assertFalse(mode)
        self.assertFalse(await self.storage.is_whitelist_enabled())

        # Toggle to True
        new_mode = await self.storage.toggle_whitelist_mode()
        self.assertTrue(new_mode)
        self.assertTrue(await self.storage.get_whitelist_mode())
        self.assertTrue(await self.storage.is_whitelist_enabled())

        # Toggle back to False via alias
        new_mode_2 = await self.storage.toggle_whitelist()
        self.assertFalse(new_mode_2)
        self.assertFalse(await self.storage.get_whitelist_mode())

    async def test_global_and_user_token_stats(self):
        # Record token usage for user 101 and 102
        await self.storage.record_token_usage(
            user_id=101, chat_id=101, model="gemini-3.5-flash-lite",
            prompt_tokens=10, candidates_tokens=20, total_tokens=30,
        )
        await self.storage.record_token_usage(
            user_id=102, chat_id=102, model="gemini-3.5-flash-lite",
            prompt_tokens=15, candidates_tokens=25, total_tokens=40,
        )

        # Per user stats for user 101
        u101_stats = await self.storage.get_token_stats(101)
        self.assertEqual(u101_stats["all_total"], 30)
        self.assertEqual(u101_stats["all_requests"], 1)

        # Per user stats for user 102
        u102_stats = await self.storage.get_token_stats(102)
        self.assertEqual(u102_stats["all_total"], 40)
        self.assertEqual(u102_stats["all_requests"], 1)

        # Global aggregate stats across all users (user_id is None)
        global_stats = await self.storage.get_token_stats(None)
        self.assertEqual(global_stats["all_total"], 70)
        self.assertEqual(global_stats["all_prompt"], 25)
        self.assertEqual(global_stats["all_candidates"], 45)
        self.assertEqual(global_stats["all_requests"], 2)

    async def test_admin_panel_render_text(self):
        stats = await self.storage.get_token_stats()
        users = await self.storage.list_allowed_users()
        wl_enabled = await self.storage.get_whitelist_mode()
        text = _render_admin_panel_text(wl_enabled, users, stats)
        self.assertIn("Панель управления администратора", text)
        self.assertIn("Белый список", text)
        self.assertIn("Расход токенов", text)


if __name__ == "__main__":
    unittest.main()