import html
import os
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:FAKE_TOKEN_FOR_TESTS")
os.environ.setdefault("GEMINI_API_KEY", "FAKE_KEY_FOR_TESTS")

from keyboards import models_hub_keyboard, mode_model_selector_keyboard
from storage import UserStorage, UserState


class TestModelSelectors(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_selectors.db")
        self.storage = UserStorage(
            db_path=self.db_path,
            default_model="gemini-3.5-flash-lite",
            max_history=5,
            default_tts_model="gemini-3.8-flash-tts",
        )
        await self.storage.init_db()

    async def asyncTearDown(self):
        await self.storage.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_models_hub_keyboard_structure(self):
        state = UserState(
            model="gemini-3.5-flash-lite",
            avatar_model="gemini-4-argon",
            business_model="gemini-3.8-flash",
            stt_model="gemini-3.8-flash-lite",
            tts_model="gemini-3.8-flash-tts",
        )
        kb = models_hub_keyboard(state)
        self.assertIsNotNone(kb)

        callback_data_list = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        self.assertIn("pick_mode_model:stand", callback_data_list)
        self.assertIn("pick_mode_model:avatar", callback_data_list)
        self.assertIn("pick_mode_model:business", callback_data_list)
        self.assertIn("pick_mode_model:stt", callback_data_list)
        self.assertIn("pick_mode_model:tts", callback_data_list)
        self.assertIn("menu:main", callback_data_list)

        # Проверяем, что в названиях кнопок отображаются соответствующие модели
        btn_texts = {btn.callback_data: btn.text for row in kb.inline_keyboard for btn in row}
        self.assertIn("gemini-3.5-flash-lite", btn_texts["pick_mode_model:stand"])
        self.assertIn("gemini-4-argon", btn_texts["pick_mode_model:avatar"])
        self.assertIn("gemini-3.8-flash", btn_texts["pick_mode_model:business"])
        self.assertIn("gemini-3.8-flash-lite", btn_texts["pick_mode_model:stt"])
        self.assertIn("gemini-3.8-flash-tts", btn_texts["pick_mode_model:tts"])

    def test_mode_model_selector_keyboard(self):
        models = [
            {"model_id": "gemini-3.5-flash-lite", "display_name": "⚡️ Gemini 3.5 Flash Lite"},
            {"model_id": "gemini-3.8-flash", "display_name": "⚖️ Gemini 3.8 Flash"},
            {"model_id": "gemini-4-argon", "display_name": "🧠 Gemini 4 Argon"},
        ]
        kb = mode_model_selector_keyboard("avatar", "gemini-4-argon", models)
        self.assertIsNotNone(kb)

        buttons = [btn for row in kb.inline_keyboard for btn in row]
        callbacks = [btn.callback_data for btn in buttons]

        self.assertIn("set_mode_model:avatar:gemini-3.5-flash-lite", callbacks)
        self.assertIn("set_mode_model:avatar:gemini-3.8-flash", callbacks)
        self.assertIn("set_mode_model:avatar:gemini-4-argon", callbacks)
        self.assertIn("menu:model", callbacks)

        # Выбранная модель должна быть помечена ✅
        for btn in buttons:
            if btn.callback_data == "set_mode_model:avatar:gemini-4-argon":
                self.assertTrue(btn.text.startswith("✅"))
            elif btn.callback_data == "set_mode_model:avatar:gemini-3.8-flash":
                self.assertFalse(btn.text.startswith("✅"))

    async def test_set_mode_model_via_storage(self):
        user_id = 99999
        await self.storage.set_user_mode_model(user_id, "business", "gemini-4-argon")
        state = await self.storage.get_settings(user_id)
        self.assertEqual(state.business_model, "gemini-4-argon")
