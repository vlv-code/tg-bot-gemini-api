import os
import unittest
from unittest.mock import AsyncMock

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:FAKE_TOKEN_FOR_TESTS")
os.environ.setdefault("GEMINI_API_KEY", "FAKE_KEY_FOR_TESTS")

from config import settings
from gemini_client import GeminiClient, sync_models_catalog
from storage import UserStorage


class FakeModel:
    def __init__(self, name: str, display_name: str = "", supported_methods: list[str] | None = None):
        self.name = name
        self.display_name = display_name
        self.description = "Test model description"
        self.supported_generation_methods = supported_methods or ["generateContent"]


class FakeAsyncPager:
    def __init__(self, items):
        self._items = items

    def __aiter__(self):
        self._iter = iter(self._items)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class TestBaselineModelPreservation(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.test_db = f"tests/test_baseline_preservation_{os.getpid()}.db"
        self.storage = UserStorage(
            db_path=self.test_db,
            default_model="gemini-3.5-flash-lite",
            max_history=10,
        )
        await self.storage.init_db()
        self.client = GeminiClient(api_key="fake-test-key")

    async def asyncTearDown(self):
        await self.storage.close()
        if os.path.exists(self.test_db):
            os.remove(self.test_db)

    async def test_baseline_models_including_gemini_3_8_flash_lite_present_after_sync(self):
        """Проверяем, что sync_models_catalog не удаляет и не затирает gemini-3.8-flash-lite,

        даже если Google API вернул только другие модели (например, только gemini-2.5-flash).
        """
        # Google API возвращает стороннюю модель, в которой нет gemini-3.8-flash-lite
        fake_api_models = [
            FakeModel("models/gemini-2.5-flash", "Gemini 2.5 Flash"),
        ]
        self.client._client.aio.models.list = AsyncMock(return_value=FakeAsyncPager(fake_api_models))

        # Выполняем синхронизацию
        await sync_models_catalog(self.client, self.storage)

        # Проверяем список текстовых моделей
        text_models = await self.storage.get_available_models(category="text")
        model_ids = [m["model_id"] for m in text_models]

        # gemini-3.8-flash-lite ОБЯЗАТЕЛЬНО должен присутствовать!
        self.assertIn(
            "gemini-3.8-flash-lite",
            model_ids,
            f"gemini-3.8-flash-lite пропал из доступных моделей после синхронизации! Доступны: {model_ids}",
        )

        # Все базовые модели из settings.available_models должны быть доступны
        for base_model in settings.available_models:
            self.assertIn(
                base_model,
                model_ids,
                f"Базовая модель {base_model} отсутствует в списке после синхронизации!",
            )

        # И вновь обнаруженная модель из Google API тоже должна присутствовать
        self.assertIn("gemini-2.5-flash", model_ids)

        # Проверяем, что у gemini-3.8-flash-lite правильная категория и иконка
        lite_model = next(m for m in text_models if m["model_id"] == "gemini-3.8-flash-lite")
        self.assertEqual(lite_model["category"], "text")
        self.assertTrue(lite_model["display_name"].startswith("⚡️"))

    async def test_init_db_seeds_baseline_models(self):
        """При первоначальной инициализации базы данных таблица discovered_models

        должна автоматически содержать базовые модели из config.py.
        """
        text_models = await self.storage.get_available_models(category="text")
        model_ids = [m["model_id"] for m in text_models]
        self.assertIn("gemini-3.8-flash-lite", model_ids)
        self.assertIn("gemini-3.5-flash-lite", model_ids)
