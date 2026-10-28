import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:FAKE_TOKEN_FOR_TESTS")
os.environ.setdefault("GEMINI_API_KEY", "FAKE_KEY_FOR_TESTS")

from aiogram.types import CallbackQuery, Chat, Message, User

from config import settings
from keyboards import (
    admin_panel_keyboard,
    business_draft_keyboard,
    business_menu_keyboard,
    clear_history_chats_keyboard,
    info_menu_keyboard,
    limits_keyboard,
    main_menu_keyboard,
    mode_model_selector_keyboard,
    models_hub_keyboard,
    prompt_keyboard,
    qprompt_keyboard,
    settings_keyboard,
    tts_menu_keyboard,
    tts_models_keyboard,
    tts_voices_keyboard,
)
from storage import UserState, UserStorage


class TestAllButtonsResponsive(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.test_db = f"tests/test_buttons_{os.getpid()}.db"
        self.storage = UserStorage(
            db_path=self.test_db,
            default_model="gemini-3.5-flash-lite",
            max_history=10,
        )
        await self.storage.init_db()

    async def asyncTearDown(self):
        await self.storage.close()
        if os.path.exists(self.test_db):
            os.remove(self.test_db)

    def _create_mock_callback(self, user_id: int, callback_data: str) -> CallbackQuery:
        cb = MagicMock(spec=CallbackQuery)
        cb.data = callback_data
        cb.from_user = MagicMock(spec=User)
        cb.from_user.id = user_id
        cb.from_user.username = "test_user"
        cb.from_user.full_name = "Test User"
        cb.message = MagicMock(spec=Message)
        cb.message.chat = MagicMock(spec=Chat)
        cb.message.chat.id = user_id
        cb.message.chat.type = "private"
        cb.message.text = "Menu header"
        cb.message.edit_text = AsyncMock()
        cb.message.delete = AsyncMock()
        cb.message.reply = AsyncMock()
        cb.message.reply_voice = AsyncMock()
        cb.answer = AsyncMock()
        return cb

    def _extract_all_callbacks(self, markup) -> list[str]:
        results = []
        for row in markup.inline_keyboard:
            for btn in row:
                if btn.callback_data:
                    results.append(btn.callback_data)
        return results

    async def test_all_keyboards_produce_valid_callbacks(self):
        """Проверяем, что все генераторы клавиатур возвращают непустые callback_data."""
        state = UserState(model="gemini-3.5-flash-lite")
        markups = [
            main_menu_keyboard(is_admin=True),
            info_menu_keyboard(),
            models_hub_keyboard(state),
            mode_model_selector_keyboard(
                "stand",
                "gemini-3.5-flash-lite",
                [{"model_id": "gemini-3.8-flash-lite", "display_name": "⚡️ gemini-3.8-flash-lite"}],
            ),
            settings_keyboard(rich_mode=True, voice_mode=False),
            limits_keyboard(),
            admin_panel_keyboard(whitelist_enabled=True),
            tts_menu_keyboard("gemini-3.8-flash-tts", "Aoede", False),
            tts_models_keyboard("gemini-3.8-flash-tts"),
            tts_voices_keyboard("Aoede"),
            business_menu_keyboard(facts_count=2, rules_count=3, autoreply_count=1),
            business_draft_keyboard("draft_abc"),
            prompt_keyboard(),
            qprompt_keyboard(),
            clear_history_chats_keyboard([{"chat_id": 12345, "chat_title": "Test", "message_count": 5}]),
        ]

        all_cb_data = []
        for kb in markups:
            cbs = self._extract_all_callbacks(kb)
            self.assertGreater(len(cbs), 0, f"Клавиатура {kb} не содержит кнопок!")
            all_cb_data.extend(cbs)

        # Ни один callback_data не должен превышать лимит Telegram (64 байта)
        for cb in all_cb_data:
            self.assertLessEqual(
                len(cb.encode("utf-8")),
                64,
                f"callback_data '{cb}' превышает лимит Telegram в 64 байта!",
            )

    async def test_model_selection_callbacks_respond_promptly(self):
        """Проверяем отклик хэндлеров навигации по моделям (menu:model, pick_mode_model, set_mode_model)."""
        import handlers.menu as menu_mod

        user_id = 999111
        with patch.object(menu_mod, "storage", self.storage):
            # 1. Открытие хаба моделей
            cb1 = self._create_mock_callback(user_id, "menu:model")
            await menu_mod.cb_menu_model(cb1)
            cb1.answer.assert_called_once()
            cb1.message.edit_text.assert_called_once()

            # 2. Выбор режима (например, stand)
            cb2 = self._create_mock_callback(user_id, "pick_mode_model:stand")
            await menu_mod.cb_pick_mode_model(cb2)
            cb2.answer.assert_called_once()
            cb2.message.edit_text.assert_called_once()

            # 3. Установка модели
            cb3 = self._create_mock_callback(user_id, "set_mode_model:stand:gemini-3.8-flash-lite")
            await menu_mod.cb_set_mode_model(cb3)
            cb3.answer.assert_called_once()
            user_state = await self.storage.get_settings(user_id)
            self.assertEqual(user_state.stand_model, "gemini-3.8-flash-lite")

    async def test_main_menu_and_settings_callbacks_respond_promptly(self):
        """Проверяем отклик хэндлеров главного меню, настроек и переключателей."""
        import handlers.menu as menu_mod

        user_id = 999222
        with patch.object(menu_mod, "storage", self.storage):
            # Переход в главное меню
            cb_main = self._create_mock_callback(user_id, "menu:main")
            await menu_mod.cb_menu_main(cb_main)
            cb_main.answer.assert_called_once()

            # Переключение rich-режима
            cb_rich = self._create_mock_callback(user_id, "toggle_rich")
            await menu_mod.cb_toggle_rich(cb_rich)
            cb_rich.answer.assert_called_once()

            # Переключение голосовых ответов
            cb_voice = self._create_mock_callback(user_id, "toggle_voice")
            await menu_mod.cb_toggle_voice(cb_voice)
            cb_voice.answer.assert_called_once()

            # Меню TTS
            cb_tts = self._create_mock_callback(user_id, "menu:tts")
            await menu_mod.cb_menu_tts(cb_tts)
            cb_tts.answer.assert_called_once()

            # Выбор TTS модели
            cb_set_tts = self._create_mock_callback(user_id, "set_tts_model:gemini-3.8-flash-tts")
            await menu_mod.cb_set_tts_model(cb_set_tts)
            cb_set_tts.answer.assert_called_once()
            state = await self.storage.get_settings(user_id)
            self.assertEqual(state.tts_model, "gemini-3.8-flash-tts")
