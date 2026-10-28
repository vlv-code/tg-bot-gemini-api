import asyncio
import os
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:FAKE_TOKEN_FOR_TESTS")
os.environ.setdefault("GEMINI_API_KEY", "FAKE_KEY_FOR_TESTS")

from gemini_client import GeminiClient, sync_models_catalog
from storage import UserStorage


class FakeModel:
    def __init__(self, name: str, display_name: str = "", description: str = "", methods: list = None):
        self.name = name
        self.display_name = display_name or name
        self.description = description
        self.supported_generation_methods = methods or ["generateContent"]


class FakeAsyncPager:
    def __init__(self, items):
        self.items = items

    def __aiter__(self):
        self._iter = iter(self.items)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class TestModelCatalogSync(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_sync.db")
        self.storage = UserStorage(
            db_path=self.db_path,
            default_model="gemini-3.5-flash-lite",
            max_history=5,
        )
        await self.storage.init_db()

        self.client = GeminiClient(api_key="fake_key")

    async def asyncTearDown(self):
        await self.storage.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    async def test_fetch_and_classify_models(self):
        fake_models = [
            FakeModel("models/gemini-3.5-flash-lite", "Gemini 3.5 Flash Lite", "Fast and lightweight"),
            FakeModel("models/gemini-3.8-flash", "Gemini 3.8 Flash", "Balanced flash"),
            FakeModel("models/gemini-4-argon", "Gemini 4 Argon", "Next gen reasoning"),
            FakeModel("models/gemini-3.8-flash-tts", "Gemini 3.8 Flash TTS", "Speech synthesis"),
            FakeModel("models/imagen-3.0-generate-002", "Imagen 3.0", "Image generation"),
            FakeModel("models/text-embedding-004", "Embedding 004", "Embedding", methods=["embedContent"]),
            # Чекпоинты, которые должны фильтроваться
            FakeModel("models/gemini-1.5-flash-001", "Gemini 1.5 Flash 001", "Old checkpoint"),
            FakeModel("models/gemini-exp-1206-20241206", "Gemini Exp", "Dated exp checkpoint"),
        ]

        self.client._client.aio.models.list = AsyncMock(return_value=FakeAsyncPager(fake_models))

        classified = await self.client.fetch_and_classify_models()
        self.assertEqual(len(classified), 6)

        m_map = {m["model_id"]: m for m in classified}
        self.assertIn("gemini-3.5-flash-lite", m_map)
        self.assertIn("gemini-3.8-flash", m_map)
        self.assertIn("gemini-4-argon", m_map)
        self.assertIn("gemini-3.8-flash-tts", m_map)
        self.assertIn("imagen-3.0-generate-002", m_map)
        self.assertIn("text-embedding-004", m_map)

        # Категории
        self.assertEqual(m_map["gemini-3.5-flash-lite"]["category"], "text")
        self.assertEqual(m_map["gemini-3.8-flash-tts"]["category"], "tts")
        self.assertEqual(m_map["imagen-3.0-generate-002"]["category"], "image_gen")
        self.assertEqual(m_map["text-embedding-004"]["category"], "embed")

        # Иконки в отображаемых именах
        self.assertTrue(m_map["gemini-3.5-flash-lite"]["display_name"].startswith("⚡️"))
        self.assertTrue(m_map["gemini-3.8-flash"]["display_name"].startswith("⚖️"))
        self.assertTrue(m_map["gemini-4-argon"]["display_name"].startswith("🧠"))
        self.assertTrue(m_map["gemini-3.8-flash-tts"]["display_name"].startswith("🎧"))
        self.assertTrue(m_map["imagen-3.0-generate-002"]["display_name"].startswith("🎨"))

    async def test_sync_models_catalog_updates_storage(self):
        fake_models = [
            FakeModel("models/gemini-3.8-flash", "Gemini 3.8 Flash"),
            FakeModel("models/gemini-3.8-flash-tts", "Gemini 3.8 Flash TTS"),
        ]
        self.client._client.aio.models.list = AsyncMock(return_value=FakeAsyncPager(fake_models))

        stats = await sync_models_catalog(self.client, self.storage)
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["text"], 1)
        self.assertEqual(stats["tts"], 1)

        db_models = await self.storage.get_available_models(category="text")
        model_ids = [m["model_id"] for m in db_models]
        self.assertIn("gemini-3.8-flash", model_ids)
        self.assertIn("gemini-3.8-flash-lite", model_ids)

    async def test_sync_models_catalog_handles_error(self):
        self.client._client.aio.models.list = AsyncMock(side_effect=RuntimeError("API error"))
        stats = await sync_models_catalog(self.client, self.storage)
        self.assertEqual(stats["total"], 0)
        self.assertIn("error", stats)
