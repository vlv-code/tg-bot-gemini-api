import asyncio
import os
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:FAKE_TOKEN_FOR_TESTS")
os.environ.setdefault("GEMINI_API_KEY", "FAKE_KEY_FOR_TESTS")

import aiosqlite
from config import settings
from gemini_client import GeminiClient, GeminiResponse
from storage import UserStorage


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


class FakeModelWithActions:
    def __init__(self, name: str, display_name: str = "", supported_actions: list = None, supported_generation_methods: list = None):
        self.name = name
        self.display_name = display_name or name
        self.supported_actions = supported_actions
        self.supported_generation_methods = supported_generation_methods


class TestCoreClientAudit(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_audit.db")
        self.storage = UserStorage(
            db_path=self.db_path,
            default_model="gemini-3.5-flash-lite",
            max_history=5,
        )
        await self.storage.init_db()
        self.client = GeminiClient(
            api_key="FAKE_KEY",
            default_model="gemini-3.5-flash-lite",
            timeout_seconds=0.5,
        )
        self.client._client = MagicMock()

    async def asyncTearDown(self):
        await self.storage.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # 1. AUD-01: Requirements and supported_actions in fetch_and_classify_models
    def test_aud01_requirements_google_genai_version(self):
        req_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "requirements.txt")
        with open(req_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("google-genai>=2.0", content)

    async def test_aud01_supported_actions_classification(self):
        # Models with supported_actions=['generateContent'] and NO supported_generation_methods (e.g. gemma-3-27b-it)
        fake_models = [
            FakeModelWithActions("models/gemma-3-27b-it", "Gemma 3 27B IT", supported_actions=["generateContent"]),
        ]
        self.client._client.aio.models.list = AsyncMock(return_value=FakeAsyncPager(fake_models))

        classified = await self.client.fetch_and_classify_models()
        self.assertEqual(len(classified), 1)
        model = classified[0]
        self.assertEqual(model["model_id"], "gemma-3-27b-it")
        self.assertEqual(model["category"], "text")
        self.assertNotEqual(model["category"], "other")

    # 2. AUD-18: response_mime_type in generate_speech
    async def test_aud18_generate_speech_response_mime_type(self):
        mock_part = MagicMock()
        mock_part.inline_data.data = b"PCM_DATA_BYTES"
        mock_candidate = MagicMock()
        mock_candidate.content.parts = [mock_part]
        mock_response = MagicMock()
        mock_response.candidates = [mock_candidate]

        self.client._client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        audio = await self.client.generate_speech(text="Привет мир", voice_name="Aoede")
        self.assertEqual(audio, b"PCM_DATA_BYTES")

        # Verify config passed to generate_content: response_mime_type must be None (Gemini API 400 rejection guard)
        calls = self.client._client.aio.models.generate_content.call_args_list
        self.assertTrue(len(calls) > 0)
        config_arg = calls[0].kwargs.get("config")
        self.assertIsNotNone(config_arg)
        self.assertIsNone(getattr(config_arg, "response_mime_type", None))
        self.assertEqual(config_arg.response_modalities, ["AUDIO"])

    # 3. AUD-09 (Core): used_model in GeminiResponse and ask()
    def test_aud09_gemini_response_has_used_model_field(self):
        resp = GeminiResponse(text="test")
        self.assertTrue(hasattr(resp, "used_model"))
        self.assertEqual(resp.used_model, "")

    async def test_aud09_ask_records_used_model_primary_and_fallback(self):
        mock_response = MagicMock()
        mock_response.text = "OK"
        mock_response.candidates = []

        # Case 1: Primary success
        mock_chat_success = MagicMock()
        mock_chat_success.send_message = AsyncMock(return_value=mock_response)
        self.client._client.aio.chats.create = MagicMock(return_value=mock_chat_success)

        resp = await self.client.ask(
            model="gemini-4-argon",
            history_turns=[],
            message="Тест",
        )
        self.assertEqual(resp.used_model, "gemini-4-argon")

        # Case 2: Fallback on 503
        mock_failing_chat = MagicMock()
        mock_failing_chat.send_message = AsyncMock(side_effect=Exception("503 UNAVAILABLE: Model overloaded"))

        def fake_create(model, config, history):
            if model == "gemini-4-argon":
                return mock_failing_chat
            return mock_chat_success

        self.client._client.aio.chats.create.side_effect = fake_create

        resp_fallback = await self.client.ask(
            model="gemini-4-argon",
            history_turns=[],
            message="Тест",
        )
        self.assertEqual(resp_fallback.used_model, "gemini-3.5-flash-lite")

    # 4. AUD-16: is_pinned column, set_model_enabled, and soft-disable on complete baseline
    async def test_aud16_schema_has_is_pinned(self):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("PRAGMA table_info(discovered_models)")
            columns = {row[1]: row for row in await cursor.fetchall()}
            self.assertIn("is_pinned", columns)

    async def test_aud16_set_model_enabled_pins_model(self):
        # Insert a model
        await self.storage.upsert_discovered_models([
            {"model_id": "test-model-1", "display_name": "Test 1", "category": "text"}
        ])

        # Enable/disable via set_model_enabled
        await self.storage.set_model_enabled("test-model-1", False)

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT is_enabled, is_pinned FROM discovered_models WHERE model_id = 'test-model-1'")
            row = await cursor.fetchone()
            self.assertEqual(row[0], 0)  # is_enabled == 0
            self.assertEqual(row[1], 1)  # is_pinned == 1

        await self.storage.set_model_enabled("test-model-1", True)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT is_enabled, is_pinned FROM discovered_models WHERE model_id = 'test-model-1'")
            row = await cursor.fetchone()
            self.assertEqual(row[0], 1)  # is_enabled == 1
            self.assertEqual(row[1], 1)  # is_pinned == 1

    async def test_aud16_soft_disable_when_baseline_complete(self):
        # Seed an old model and a pinned old model
        await self.storage.upsert_discovered_models([
            {"model_id": "old-unpinned-model", "display_name": "Old", "category": "text"},
            {"model_id": "pinned-model", "display_name": "Pinned", "category": "text"},
        ])
        await self.storage.set_model_enabled("pinned-model", True)

        # 1. When incoming models do NOT contain all settings.available_models, do NOT soft-disable
        partial_models = [{"model_id": "some-random-model", "display_name": "Random", "category": "text"}]
        await self.storage.upsert_discovered_models(partial_models)

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT model_id, is_enabled FROM discovered_models WHERE model_id = 'old-unpinned-model'")
            row = await cursor.fetchone()
            self.assertEqual(row[1], 1)  # Still enabled because baseline was incomplete

        # 2. When incoming models contain ALL settings.available_models
        complete_models = [
            {"model_id": mid, "display_name": mid, "category": "text"}
            for mid in settings.available_models
        ]
        await self.storage.upsert_discovered_models(complete_models)

        async with aiosqlite.connect(self.db_path) as db:
            # old-unpinned-model should now be disabled
            cursor = await db.execute("SELECT model_id, is_enabled FROM discovered_models WHERE model_id = 'old-unpinned-model'")
            row = await cursor.fetchone()
            self.assertEqual(row[1], 0)

            # pinned-model should still be enabled
            cursor = await db.execute("SELECT model_id, is_enabled FROM discovered_models WHERE model_id = 'pinned-model'")
            row = await cursor.fetchone()
            self.assertEqual(row[1], 1)
