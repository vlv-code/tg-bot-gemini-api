import html
import os
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

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

        self.assertIn("smm:a:gemini-3.5-flash-lite:p1", callbacks)
        self.assertIn("smm:a:gemini-3.8-flash:p1", callbacks)
        self.assertIn("smm:a:gemini-4-argon:p1", callbacks)
        self.assertIn("menu:model", callbacks)

        # Выбранная модель должна быть помечена ✅
        for btn in buttons:
            if btn.callback_data == "smm:a:gemini-4-argon:p1":
                self.assertTrue(btn.text.startswith("✅"))
            elif btn.callback_data == "smm:a:gemini-3.8-flash:p1":
                self.assertFalse(btn.text.startswith("✅"))

    async def test_set_mode_model_via_storage(self):
        user_id = 99999
        await self.storage.set_user_mode_model(user_id, "business", "gemini-4-argon")
        state = await self.storage.get_settings(user_id)
        self.assertEqual(state.business_model, "gemini-4-argon")

    def test_mode_model_selector_keyboard_38_models_strict_telegram_limits(self):
        """Проверяем, что при 38 моделях с длинными названиями ни одна кнопка не превышает 64 байта,
        а размер всей клавиатуры не превышает лимит Telegram в 10 КБ."""
        models = [
            {
                "model_id": f"gemini-2.0-flash-thinking-exp-preview-{i:02d}",
                "display_name": f"🧠 Gemini 2.0 Flash Thinking Exp Preview {i:02d}",
            }
            for i in range(38)
        ]
        # Проверяем все режимы: stand, avatar, business, stt, tts
        for mode in ("stand", "avatar", "business", "stt", "tts"):
            kb = mode_model_selector_keyboard(mode, models[0]["model_id"], models, page=1)
            self.assertIsNotNone(kb)

            # Проверяем лимит 64 байта на каждую кнопку
            for row in kb.inline_keyboard:
                for btn in row:
                    cb_len = len(btn.callback_data.encode("utf-8"))
                    self.assertLessEqual(
                        cb_len,
                        64,
                        f"Режим {mode}: callback_data '{btn.callback_data}' превышает лимит Telegram (64 байта): {cb_len} байт",
                    )

            # Проверяем размер JSON разметки клавиатуры (должен быть значительно меньше 10 КБ)
            kb_json_len = len(kb.model_dump_json().encode("utf-8"))
            self.assertLess(
                kb_json_len,
                4096,
                f"Режим {mode}: размер клавиатуры {kb_json_len} байт превышает 4 КБ",
            )

    async def test_cb_pick_and_set_mode_model_flow(self):
        """Проверяем открытие селектора и переключение модели для режимов stand/avatar/business."""
        import handlers.menu as menu_mod
        from aiogram.exceptions import TelegramBadRequest

        user_id = 777888
        # Добавляем 38 моделей в БД
        test_models = [
            {
                "model_id": f"gemini-test-model-{i:02d}-variant",
                "display_name": f"🧠 Model {i:02d}",
                "category": "text",
                "input_modalities": "text",
                "output_modalities": "text",
                "is_enabled": 1,
                "sort_order": 10,
            }
            for i in range(38)
        ]
        await self.storage.upsert_discovered_models(test_models)

        with patch.object(menu_mod, "storage", self.storage):
            for mode in ("stand", "avatar", "business", "stt"):
                # 1. Открытие селектора моделей
                cb = MagicMock()
                cb.data = f"pick_mode_model:{mode}"
                cb.from_user.id = user_id
                cb.message.chat.id = user_id
                cb.message.edit_text = AsyncMock()
                cb.answer = AsyncMock()

                await menu_mod.cb_pick_mode_model(cb)
                cb.answer.assert_called_once()
                cb.message.edit_text.assert_called_once()

                # Проверяем переданный reply_markup
                call_args = cb.message.edit_text.call_args
                reply_markup = call_args.kwargs.get("reply_markup")
                self.assertIsNotNone(reply_markup)
                for row in reply_markup.inline_keyboard:
                    for btn in row:
                        self.assertLessEqual(len(btn.callback_data.encode("utf-8")), 64)

                # 2. Выбор модели через компактный callback
                chosen_mid = f"gemini-test-model-03-variant"
                mode_code = {"stand": "s", "avatar": "a", "business": "b", "stt": "v"}[mode]
                cb_set = MagicMock()
                cb_set.data = f"smm:{mode_code}:{chosen_mid}:p1"
                cb_set.from_user.id = user_id
                cb_set.message.chat.id = user_id
                cb_set.message.edit_text = AsyncMock()
                cb_set.answer = AsyncMock()

                await menu_mod.cb_set_mode_model(cb_set)
                cb_set.answer.assert_called_once()
                cb_set.message.edit_text.assert_called_once()

                # Проверяем, что модель сохранилась в БД
                settings_state = await self.storage.get_settings(user_id)
                actual_model = getattr(settings_state, f"{mode}_model" if mode != "stand" else "model")
                self.assertEqual(actual_model, chosen_mid)

            # 3. Проверка обработки ошибок: TelegramBadRequest не должен проглатываться втихую
            cb_err = MagicMock()
            cb_err.data = "pick_mode_model:stand"
            cb_err.from_user.id = user_id
            cb_err.message.chat.id = user_id
            cb_err.message.edit_text = AsyncMock(side_effect=TelegramBadRequest(method=MagicMock(), message="Bad Request: BUTTON_DATA_INVALID"))
            cb_err.answer = AsyncMock()

            await menu_mod.cb_pick_mode_model(cb_err)
            # При реальной ошибке (не message is not modified) должен быть вызван alert для пользователя
            err_answer_calls = [c for c in cb_err.answer.call_args_list if c.kwargs.get("show_alert")]
            self.assertGreater(len(err_answer_calls), 0, "Ошибка TelegramBadRequest не должна проглатываться без оповещения пользователя!")

