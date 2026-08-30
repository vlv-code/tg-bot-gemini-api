import os
import shutil
import tempfile
import unittest

# Устанавливаем фейковые токены для тестов до импорта модулей
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:FAKE_TOKEN_FOR_TESTS")
os.environ.setdefault("GEMINI_API_KEY", "FAKE_KEY_FOR_TESTS")

from storage import UserStorage


class TestUserStorage(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_bot.db")
        self.storage = UserStorage(
            db_path=self.db_path,
            default_model="gemini-3.5-flash-lite",
            max_history=5,
            default_tts_model="gemini-2.5-flash-preview-tts",
            default_tts_voice="Aoede",
        )
        await self.storage.init_db()

    async def asyncTearDown(self):
        await self.storage.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    async def test_user_initial_state_and_settings(self):
        user_id = 12345
        state = await self.storage.get(user_id)
        self.assertEqual(state.model, "gemini-3.5-flash-lite")
        self.assertTrue(state.rich_mode)
        self.assertFalse(state.voice_mode)
        self.assertEqual(state.tts_voice, "Aoede")
        self.assertEqual(state.history, [])

        # Изменение модели и настроек
        await self.storage.set_model(user_id, "gemini-3.7-flash")
        await self.storage.set_tts_voice(user_id, "Kore")
        await self.storage.toggle_voice(user_id)

        updated = await self.storage.get_settings(user_id)
        self.assertEqual(updated.model, "gemini-3.7-flash")
        self.assertEqual(updated.tts_voice, "Kore")
        self.assertTrue(updated.voice_mode)

    async def test_history_management_and_trimming(self):
        user_id = 999
        # Добавляем 7 реплик при max_history=5
        for i in range(1, 8):
            await self.storage.add_turn(
                user_id=user_id,
                role="user" if i % 2 != 0 else "model",
                text=f"Turn {i}",
                mode="main",
            )

        state = await self.storage.get(user_id, mode="main")
        self.assertEqual(len(state.history), 5)
        # Должны остаться последние 5 реплик: Turn 3 .. Turn 7
        self.assertEqual(state.history[0].text, "Turn 3")
        self.assertEqual(state.history[-1].text, "Turn 7")

        # Режим 'quick' должен оставаться пустым
        quick_state = await self.storage.get(user_id, mode="quick")
        self.assertEqual(len(quick_state.history), 0)

    async def test_prompts_and_personas(self):
        user_id = 555
        # Сохранение Stand промпта
        prompt_id = await self.storage.save_user_prompt(
            user_id=user_id, name="ТестПромпт", prompt="Тестовый текст", mode="main"
        )
        self.assertGreater(prompt_id, 0)

        saved = await self.storage.get_saved_prompts(user_id, mode="main")
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["name"], "ТестПромпт")

        # Поиск по имени
        found = await self.storage.find_stand_preset_by_name_or_id(user_id, "ТестПромпт")
        self.assertIsNotNone(found)
        self.assertEqual(found["prompt"], "Тестовый текст")

        # Удаление промпта
        deleted = await self.storage.delete_user_prompt_by_name(user_id, "ТестПромпт", mode="main")
        self.assertTrue(deleted)
        saved_after = await self.storage.get_saved_prompts(user_id, mode="main")
        self.assertEqual(len(saved_after), 0)

    async def test_admin_and_whitelist(self):
        user_id = 777
        self.assertFalse(await self.storage.is_user_admin(user_id))

        await self.storage.add_allowed_user(user_id, username="testadmin", is_admin=True)
        self.assertTrue(await self.storage.is_user_admin(user_id))
        self.assertTrue(await self.storage.is_user_allowed(user_id))

        # Удаление
        await self.storage.remove_allowed_user(user_id)
        self.assertFalse(await self.storage.is_user_admin(user_id))

    async def test_cleanup_and_backup(self):
        user_id = 888
        # Записываем токен и TTS кэш
        await self.storage.record_token_usage(
            user_id=user_id, chat_id=user_id, model="gemini-3.5-flash-lite",
            prompt_tokens=100, candidates_tokens=50, total_tokens=150
        )
        await self.storage.save_cached_tts_voice("Привет", "Aoede", "model-tts", "file_id_123")

        # Имитируем старые записи (старше 60 и 30 дней)
        db = await self.storage._ensure_db()
        await db.execute("UPDATE token_usage SET created_at = datetime('now', '-70 days') WHERE user_id = ?", (user_id,))
        await db.execute("UPDATE tts_cache SET created_at = datetime('now', '-40 days')")
        await db.commit()

        # Тест очистки (удалит записи старше 60 и 30 дней)
        cleanup_res = await self.storage.cleanup_old_data(days_token_usage=60, days_tts_cache=30)
        self.assertEqual(cleanup_res["token_usage_deleted"], 1)
        self.assertEqual(cleanup_res["tts_cache_deleted"], 1)

        # Тест hot-бэкапа VACUUM INTO
        backup_file = os.path.join(self.test_dir, "backup.db")
        await self.storage.backup_to_file(backup_file)
        self.assertTrue(os.path.exists(backup_file))
        self.assertGreater(os.path.getsize(backup_file), 0)


if __name__ == "__main__":
    unittest.main()
