import os
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:FAKE_TOKEN_FOR_TESTS")
os.environ.setdefault("GEMINI_API_KEY", "FAKE_KEY_FOR_TESTS")

from keyboards import main_menu_keyboard, admin_panel_keyboard, business_menu_keyboard
from storage import UserStorage


class TestMenuBusinessAndAdminSync(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_biz_admin.db")
        self.storage = UserStorage(
            db_path=self.db_path,
            default_model="gemini-3.5-flash-lite",
            max_history=5,
        )
        await self.storage.init_db()

    async def asyncTearDown(self):
        await self.storage.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_keyboards_buttons(self):
        # 1. Главное меню должно содержать кнопку Secretary/Business
        main_kb = main_menu_keyboard(is_admin=True)
        callbacks = [btn.callback_data for row in main_kb.inline_keyboard for btn in row]
        self.assertIn("menu:business", callbacks)

        # 2. Меню администратора должно содержать кнопку синхронизации моделей
        admin_kb = admin_panel_keyboard(whitelist_enabled=True)
        admin_callbacks = [btn.callback_data for row in admin_kb.inline_keyboard for btn in row]
        self.assertIn("admin:sync_models", admin_callbacks)

        # 3. Бизнес меню должно иметь кнопку возврата в главное меню
        biz_kb = business_menu_keyboard(0, 0, 0)
        biz_callbacks = [btn.callback_data for row in biz_kb.inline_keyboard for btn in row]
        self.assertIn("menu:main", biz_callbacks)

    @patch("handlers.admin.sync_models_catalog")
    @patch("handlers.admin.storage")
    async def test_admin_sync_models_handler(self, mock_storage, mock_sync):
        from handlers.admin import cb_admin_sync_models

        mock_storage.is_user_admin = AsyncMock(return_value=True)
        mock_sync.return_value = {
            "total": 5,
            "text": 3,
            "tts": 1,
            "image_gen": 1,
            "embed": 0,
        }

        mock_cb = MagicMock()
        mock_cb.from_user.id = 777
        mock_cb.answer = AsyncMock()
        mock_cb.message.answer = AsyncMock()

        await cb_admin_sync_models(mock_cb)
        mock_sync.assert_called_once()
        mock_cb.message.answer.assert_called_once()
        args = mock_cb.message.answer.call_args[0][0]
        self.assertIn("5", args)
        self.assertIn("Каталог моделей", args)
