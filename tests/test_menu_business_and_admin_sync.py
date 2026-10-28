import os
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:FAKE_TOKEN_FOR_TESTS")
os.environ.setdefault("GEMINI_API_KEY", "FAKE_KEY_FOR_TESTS")

from keyboards import main_menu_keyboard, admin_panel_keyboard, business_menu_keyboard
from storage import UserStorage
from aiogram.types import CallbackQuery, Chat, Message, User


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

    @patch.object(Message, "edit_text", new_callable=AsyncMock)
    @patch("handlers.business.storage")
    async def test_menu_business_callback_with_real_frozen_callback_query(self, mock_storage, mock_edit_text):
        from datetime import datetime
        from aiogram.types import CallbackQuery, Chat, Message, User
        from handlers.menu import cb_menu_business

        mock_storage.get_business_facts = AsyncMock(return_value=[])
        mock_storage.get_business_keyword_rules = AsyncMock(return_value=[])
        mock_storage.get_business_connections_for_owner = AsyncMock(return_value=[])
        mock_storage.get_auto_reply_chats = AsyncMock(return_value=[])

        user = User(id=999, is_bot=False, first_name="RealOwner")
        chat = Chat(id=999, type="private")
        message = Message(
            message_id=100,
            date=datetime.now(),
            chat=chat,
            text="Menu message",
        )

        real_cb = CallbackQuery(
            id="real_cb_id_1",
            from_user=user,
            chat_instance="chat_inst_1",
            data="menu:business",
            message=message,
        )

        # Before fix, this raises ValidationError because CallbackQuery is frozen and cb_menu_business sets callback.data = "bizmenu:main"
        await cb_menu_business(real_cb)
        mock_edit_text.assert_called_once()

    @patch.object(Message, "edit_text", new_callable=AsyncMock)
    @patch("handlers.business.storage")
    async def test_bizmenu_main_callback_with_real_frozen_callback_query(self, mock_storage, mock_edit_text):
        from datetime import datetime
        from handlers.business import cb_business_menu

        mock_storage.get_business_facts = AsyncMock(return_value=[])
        mock_storage.get_business_keyword_rules = AsyncMock(return_value=[])
        mock_storage.get_business_connections_for_owner = AsyncMock(return_value=[])
        mock_storage.get_auto_reply_chats = AsyncMock(return_value=[])

        user = User(id=999, is_bot=False, first_name="RealOwner")
        chat = Chat(id=999, type="private")
        message = Message(
            message_id=101,
            date=datetime.now(),
            chat=chat,
            text="Menu message",
        )

        real_cb = CallbackQuery(
            id="real_cb_id_2",
            from_user=user,
            chat_instance="chat_inst_2",
            data="bizmenu:main",
            message=message,
        )

        await cb_business_menu(real_cb)
        mock_edit_text.assert_called_once()




