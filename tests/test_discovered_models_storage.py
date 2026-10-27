import os
import shutil
import tempfile
import unittest
import aiosqlite

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:FAKE_TOKEN_FOR_TESTS")
os.environ.setdefault("GEMINI_API_KEY", "FAKE_KEY_FOR_TESTS")

from storage import UserStorage, UserState


class TestDiscoveredModelsStorage(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_models.db")
        self.storage = UserStorage(
            db_path=self.db_path,
            default_model="gemini-3.5-flash-lite",
            max_history=5,
            default_tts_model="gemini-3.8-flash-tts",
            default_tts_voice="Aoede",
        )
        await self.storage.init_db()

    async def asyncTearDown(self):
        await self.storage.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    async def test_user_state_mode_models_defaults_and_stand_property(self):
        user_id = 11111
        state = await self.storage.get(user_id)
        self.assertEqual(state.model, "gemini-3.5-flash-lite")
        self.assertEqual(state.stand_model, "gemini-3.5-flash-lite")
        self.assertIsNone(state.avatar_model)
        self.assertIsNone(state.business_model)
        self.assertIsNone(state.stt_model)
        self.assertEqual(state.tts_model, "gemini-3.8-flash-tts")

    async def test_set_user_mode_models(self):
        user_id = 22222
        # Установка моделей по режимам
        await self.storage.set_user_mode_model(user_id, "stand", "gemini-3.8-flash")
        await self.storage.set_user_mode_model(user_id, "avatar", "gemini-4-argon")
        await self.storage.set_user_mode_model(user_id, "business", "gemini-3.6-flash")
        await self.storage.set_user_mode_model(user_id, "stt", "gemini-3.8-flash-lite")
        await self.storage.set_user_mode_model(user_id, "tts", "gemini-3.8-flash-lite-tts")

        state = await self.storage.get(user_id)
        self.assertEqual(state.model, "gemini-3.8-flash")
        self.assertEqual(state.stand_model, "gemini-3.8-flash")
        self.assertEqual(state.avatar_model, "gemini-4-argon")
        self.assertEqual(state.business_model, "gemini-3.6-flash")
        self.assertEqual(state.stt_model, "gemini-3.8-flash-lite")
        self.assertEqual(state.tts_model, "gemini-3.8-flash-lite-tts")

        # get_settings также должен возвращать эти поля
        settings_state = await self.storage.get_settings(user_id)
        self.assertEqual(settings_state.avatar_model, "gemini-4-argon")
        self.assertEqual(settings_state.business_model, "gemini-3.6-flash")
        self.assertEqual(settings_state.stt_model, "gemini-3.8-flash-lite")

        # Неизвестный режим вызывает ValueError
        with self.assertRaises(ValueError):
            await self.storage.set_user_mode_model(user_id, "unknown_mode", "gemini-test")

    async def test_upsert_and_get_discovered_models(self):
        models = [
            {
                "model_id": "gemini-3.8-flash",
                "display_name": "Gemini 3.8 Flash",
                "category": "text",
                "input_modalities": "text,image,audio",
                "output_modalities": "text",
                "is_enabled": 1,
                "sort_order": 10,
            },
            {
                "model_id": "gemini-4-argon",
                "display_name": "Gemini 4 Argon",
                "category": "text",
                "input_modalities": "text,image,audio",
                "output_modalities": "text",
                "is_enabled": 1,
                "sort_order": 20,
            },
            {
                "model_id": "gemini-3.8-flash-tts",
                "display_name": "Gemini 3.8 Flash TTS",
                "category": "tts",
                "input_modalities": "text",
                "output_modalities": "audio",
                "is_enabled": 1,
                "sort_order": 10,
            },
            {
                "model_id": "imagen-3.0-generate-002",
                "display_name": "Imagen 3.0",
                "category": "image_gen",
                "input_modalities": "text",
                "output_modalities": "image",
                "is_enabled": 1,
                "sort_order": 50,
            },
        ]
        await self.storage.upsert_discovered_models(models)

        # Выборка всех доступных
        all_models = await self.storage.get_available_models()
        self.assertEqual(len(all_models), 4)

        # Выборка по категории 'text'
        text_models = await self.storage.get_available_models(category="text")
        self.assertEqual(len(text_models), 2)
        self.assertEqual([m["model_id"] for m in text_models], ["gemini-3.8-flash", "gemini-4-argon"])

        # get_available_model_ids
        ids = await self.storage.get_available_model_ids(category="text")
        self.assertEqual(ids, ["gemini-3.8-flash", "gemini-4-argon"])

        # Выборка по категории 'tts'
        tts_models = await self.storage.get_available_models(category="tts")
        self.assertEqual(len(tts_models), 1)
        self.assertEqual(tts_models[0]["model_id"], "gemini-3.8-flash-tts")

        # Отключение модели
        await self.storage.toggle_discovered_model("gemini-4-argon", is_enabled=False)
        enabled_text = await self.storage.get_available_models(category="text", enabled_only=True)
        self.assertEqual(len(enabled_text), 1)
        self.assertEqual(enabled_text[0]["model_id"], "gemini-3.8-flash")

        # С enabled_only=False возвращаются обе
        all_text = await self.storage.get_available_models(category="text", enabled_only=False)
        self.assertEqual(len(all_text), 2)

    async def test_fallback_when_discovered_models_empty(self):
        # Когда таблица discovered_models пустая, должен возвращаться fallback
        text_models = await self.storage.get_available_models(category="text")
        self.assertTrue(len(text_models) > 0)
        text_ids = await self.storage.get_available_model_ids(category="text")
        self.assertIn("gemini-3.5-flash-lite", text_ids)

        tts_models = await self.storage.get_available_models(category="tts")
        self.assertTrue(len(tts_models) > 0)
        tts_ids = [m["model_id"] for m in tts_models]
        self.assertIn("gemini-3.8-flash-tts", tts_ids)

    async def test_migration_adds_new_columns_to_existing_db(self):
        # Закрываем текущее хранилище
        await self.storage.close()

        # Создаём вручную старую БД без avatar_model, business_model, stt_model
        old_db_path = os.path.join(self.test_dir, "old_bot.db")
        async with aiosqlite.connect(old_db_path) as db:
            await db.execute(
                """
                CREATE TABLE users (
                    user_id INTEGER PRIMARY KEY,
                    model TEXT NOT NULL,
                    rich_mode INTEGER NOT NULL DEFAULT 1,
                    voice_mode INTEGER NOT NULL DEFAULT 0,
                    system_prompt TEXT NOT NULL DEFAULT '',
                    quick_prompt TEXT NOT NULL DEFAULT '',
                    tts_model TEXT NOT NULL DEFAULT '',
                    tts_voice TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await db.execute(
                "INSERT INTO users (user_id, model) VALUES (123, 'gemini-3.5-flash-lite')"
            )
            await db.commit()

        # Запускаем UserStorage на старой БД - должен выполнить миграцию
        new_storage = UserStorage(
            db_path=old_db_path,
            default_model="gemini-3.5-flash-lite",
            max_history=5,
        )
        await new_storage.init_db()

        # Проверяем, что существующий пользователь прочитан и поля None
        state = await new_storage.get(123)
        self.assertEqual(state.model, "gemini-3.5-flash-lite")
        self.assertIsNone(state.avatar_model)
        self.assertIsNone(state.business_model)
        self.assertIsNone(state.stt_model)

        # Проверяем запись новых полей
        await new_storage.set_user_mode_model(123, "avatar", "gemini-4-argon")
        updated_state = await new_storage.get(123)
        self.assertEqual(updated_state.avatar_model, "gemini-4-argon")

        await new_storage.close()
