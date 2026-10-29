import ast
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:FAKE_TOKEN_FOR_TESTS")
os.environ.setdefault("GEMINI_API_KEY", "FAKE_KEY_FOR_TESTS")

from aiogram.types import CallbackQuery, Chat, Message, User
from config import settings
import keyboards


class TestPromptsAndMenuAudit(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = User(id=12345, is_bot=False, first_name="Tester")
        self.chat = Chat(id=12345, type="private")

    # --- 1. AUD-11: UTF-8 BOM Removal ---
    def test_aud_11_bom_removed_from_prompts(self):
        prompts_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "handlers", "prompts.py"
        )
        with open(prompts_path, "rb") as f:
            raw_header = f.read(4)
        self.assertFalse(
            raw_header.startswith(b"\xef\xbb\xbf"),
            "handlers/prompts.py still starts with UTF-8 BOM (\\xef\\xbb\\xbf)",
        )

    # --- 2. AUD-03: Protect against '=' in questions ---
    def test_aud_03_type_hints_evaluable_without_name_error(self):
        import typing
        from handlers.prompts import _parse_prompt_preset_assignment
        hints = typing.get_type_hints(_parse_prompt_preset_assignment)
        self.assertIn("return", hints)

    def test_aud_03_parse_prompt_preset_assignment_helper(self):
        from handlers.prompts import _parse_prompt_preset_assignment

        # No '=' present
        self.assertIsNone(_parse_prompt_preset_assignment("just some regular prompt"))

        # Explicit prefixes
        prefixes = [
            ("add test = my prompt", ("test", "my prompt")),
            ("save Code = write python", ("Code", "write python")),
            ("edit Math = solve x=y?", ("Math", "solve x=y?")),
            ("изменить Пират = йо-хо-хо", ("Пират", "йо-хо-хо")),
            ("сохранить Бот = будь краток", ("Бот", "будь краток")),
            ("добавить Поэт = пиши стихи", ("Поэт", "пиши стихи")),
            ("ред Эксперт = будь профи", ("Эксперт", "будь профи")),
        ]
        for raw, expected in prefixes:
            self.assertEqual(
                _parse_prompt_preset_assignment(raw),
                expected,
                f"Failed for prefix case: {raw}",
            )

        # Explicit prefix with empty name or prompt
        self.assertIsNone(_parse_prompt_preset_assignment("save = prompt"))
        self.assertIsNone(_parse_prompt_preset_assignment("edit name = "))

        # Strict heuristic without prefix
        self.assertEqual(
            _parse_prompt_preset_assignment("Pirate = speak like a pirate"),
            ("Pirate", "speak like a pirate"),
        )
        self.assertEqual(
            _parse_prompt_preset_assignment("short_name = value with = inside"),
            ("short_name", "value with = inside"),
        )

        # Negative heuristic cases: questions, multi-line, long left side, empty prompt
        self.assertIsNone(
            _parse_prompt_preset_assignment("Как решить x=y?"),
            "Question ending with '?' must not be parsed as preset assignment",
        )
        self.assertIsNone(
            _parse_prompt_preset_assignment("Почему a = b?"),
            "Question ending with '?' must not be parsed as preset assignment",
        )
        self.assertIsNone(
            _parse_prompt_preset_assignment("left?\nname = prompt"),
            "Question mark in left side must not be parsed as preset assignment",
        )
        self.assertIsNone(
            _parse_prompt_preset_assignment("first line\nsecond line = prompt"),
            "Newline in left side must not be parsed as preset assignment",
        )
        self.assertIsNone(
            _parse_prompt_preset_assignment("a" * 33 + " = prompt"),
            "Left side > 32 characters must not be parsed as preset assignment",
        )
        self.assertIsNone(
            _parse_prompt_preset_assignment("valid_name = "),
            "Empty prompt text must not be parsed as preset assignment",
        )

    @patch("handlers.prompts.storage")
    async def test_aud_03_cmd_prompt_question_with_equals(self, mock_storage):
        from handlers.prompts import cmd_prompt

        mock_state = MagicMock()
        mock_state.system_prompt = ""
        mock_storage.get_settings = AsyncMock(return_value=mock_state)
        mock_storage.find_stand_preset_by_name_or_id = AsyncMock(return_value=None)
        mock_storage.save_user_prompt = AsyncMock()
        mock_storage.set_system_prompt = AsyncMock()

        message = MagicMock(spec=Message)
        message.from_user = self.user
        message.chat = self.chat
        message.text = "/prompt Как решить x=y?"
        message.answer = AsyncMock()

        await cmd_prompt(message)

        # Must NOT save as user preset
        mock_storage.save_user_prompt.assert_not_called()
        # Must save as individual custom prompt
        mock_storage.set_system_prompt.assert_called_once_with(self.user.id, "Как решить x=y?")

    @patch("handlers.prompts.storage")
    @patch("handlers.chat.cmd_q", new_callable=AsyncMock)
    async def test_aud_03_cmd_avatar_question_with_equals_delegates_to_q(
        self, mock_cmd_q, mock_storage
    ):
        from handlers.prompts import cmd_avatar

        mock_state = MagicMock()
        mock_state.quick_prompt = ""
        mock_storage.get_settings = AsyncMock(return_value=mock_state)
        mock_storage.find_persona_by_name_or_id = AsyncMock(return_value=None)
        mock_storage.save_user_prompt = AsyncMock()
        mock_storage.set_quick_prompt = AsyncMock()

        message = MagicMock(spec=Message)
        message.from_user = self.user
        message.chat = self.chat
        message.text = "/avatar Как решить уравнение x=y?"
        message.reply_to_message = None
        message.external_reply = None
        message.quote = None
        message.answer = AsyncMock()

        await cmd_avatar(message)

        # Must NOT save as persona preset
        mock_storage.save_user_prompt.assert_not_called()
        # Must delegate question to cmd_q
        mock_cmd_q.assert_called_once_with(message)

    @patch("handlers.prompts.storage")
    async def test_aud_03_cmd_qprompt_question_with_equals(self, mock_storage):
        from handlers.prompts import cmd_qprompt

        mock_state = MagicMock()
        mock_state.quick_prompt = ""
        mock_storage.get_settings = AsyncMock(return_value=mock_state)
        mock_storage.find_persona_by_name_or_id = AsyncMock(return_value=None)
        mock_storage.save_user_prompt = AsyncMock()
        mock_storage.set_quick_prompt = AsyncMock()

        message = MagicMock(spec=Message)
        message.from_user = self.user
        message.chat = self.chat
        message.text = "/qprompt Почему a=b?"
        message.answer = AsyncMock()

        await cmd_qprompt(message)

        # Must NOT save as persona preset
        mock_storage.save_user_prompt.assert_not_called()
        # Must save as individual prompt
        mock_storage.set_quick_prompt.assert_called_once_with(self.user.id, "Почему a=b?")

    # --- 3. AUD-04 & AUD-23: Menu validation & dead code removal ---
    def test_aud_04_models_keyboard_removed_from_keyboards(self):
        self.assertFalse(
            hasattr(keyboards, "models_keyboard"),
            "keyboards.models_keyboard should be removed as dead code",
        )

    def test_aud_04_cb_set_model_removed_from_menu(self):
        import handlers.menu as menu_mod

        self.assertFalse(
            hasattr(menu_mod, "cb_set_model"),
            "handlers.menu.cb_set_model should be removed as dead code",
        )

    @patch("handlers.menu.storage")
    async def test_aud_04_cb_set_tts_model_validation(self, mock_storage):
        import handlers.menu as menu_mod

        # Invalid model
        cb_invalid = MagicMock(spec=CallbackQuery)
        cb_invalid.data = "set_tts_model:completely-invalid-tts-model"
        cb_invalid.from_user = self.user
        cb_invalid.message = MagicMock()
        cb_invalid.message.edit_text = AsyncMock()
        cb_invalid.answer = AsyncMock()

        mock_storage.is_model_enabled = AsyncMock(return_value=False)
        mock_storage.get_available_models = AsyncMock(return_value=[])
        mock_storage.set_tts_model = AsyncMock()

        await menu_mod.cb_set_tts_model(cb_invalid)

        cb_invalid.answer.assert_called_once_with(
            "Модель недоступна или отключена", show_alert=True
        )
        mock_storage.set_tts_model.assert_not_called()

        # Valid model from settings
        valid_model = settings.available_tts_models[0]
        cb_valid = MagicMock(spec=CallbackQuery)
        cb_valid.data = f"set_tts_model:{valid_model}"
        cb_valid.from_user = self.user
        cb_valid.answer = AsyncMock()
        cb_valid.message = MagicMock()
        cb_valid.message.edit_text = AsyncMock()

        await menu_mod.cb_set_tts_model(cb_valid)

        mock_storage.set_tts_model.assert_called_once_with(self.user.id, valid_model)
        cb_valid.answer.assert_called_once_with(f"TTS модель: {valid_model}")

    @patch("handlers.menu.storage")
    async def test_aud_04_cb_set_tts_voice_validation(self, mock_storage):
        import handlers.menu as menu_mod

        # Invalid voice
        cb_invalid = MagicMock(spec=CallbackQuery)
        cb_invalid.data = "set_tts_voice:UnknownVoice123"
        cb_invalid.from_user = self.user
        cb_invalid.message = MagicMock()
        cb_invalid.message.edit_text = AsyncMock()
        cb_invalid.answer = AsyncMock()
        mock_storage.set_tts_voice = AsyncMock()

        await menu_mod.cb_set_tts_voice(cb_invalid)

        cb_invalid.answer.assert_called_once_with("Голос недоступен", show_alert=True)
        mock_storage.set_tts_voice.assert_not_called()

        # Valid voice from settings
        valid_voice = settings.available_voices[0]
        cb_valid = MagicMock(spec=CallbackQuery)
        cb_valid.data = f"set_tts_voice:{valid_voice}"
        cb_valid.from_user = self.user
        cb_valid.answer = AsyncMock()
        cb_valid.message = MagicMock()
        cb_valid.message.edit_text = AsyncMock()

        await menu_mod.cb_set_tts_voice(cb_valid)

        mock_storage.set_tts_voice.assert_called_once_with(self.user.id, valid_voice)
        cb_valid.answer.assert_called_once_with(f"Голос озвучки: {valid_voice}")

    # --- 4. AUD-12: Dead chat_id variable clean up ---
    def test_aud_12_no_unused_chat_id_in_prompts(self):
        prompts_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "handlers", "prompts.py"
        )
        with open(prompts_path, "r", encoding="utf-8-sig") as f:
            code = f.read()

        tree = ast.parse(code)
        unused = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assigned = False
                loaded = False
                for n in ast.walk(node):
                    if isinstance(n, ast.Name) and n.id == "chat_id":
                        if isinstance(n.ctx, ast.Store):
                            assigned = True
                        elif isinstance(n.ctx, ast.Load):
                            loaded = True
                if assigned and not loaded:
                    unused.append(node.name)

        self.assertEqual(
            unused,
            [],
            f"Found dead 'chat_id' assignments in handlers/prompts.py functions: {unused}",
        )

    def test_aud_12_no_unused_chat_id_in_menu(self):
        menu_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "handlers", "menu.py"
        )
        with open(menu_path, "r", encoding="utf-8") as f:
            code = f.read()

        tree = ast.parse(code)
        unused = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assigned = False
                loaded = False
                for n in ast.walk(node):
                    if isinstance(n, ast.Name) and n.id == "chat_id":
                        if isinstance(n.ctx, ast.Store):
                            assigned = True
                        elif isinstance(n.ctx, ast.Load):
                            loaded = True
                if assigned and not loaded:
                    unused.append(node.name)

        self.assertEqual(
            unused,
            [],
            f"Found dead 'chat_id' assignments in handlers/menu.py functions: {unused}",
        )

    # --- 5. AUD-19: Safe long model formatting and unambiguous hash mapping ---
    def test_aud_19_mode_model_selector_keyboard_long_model(self):
        long_model_id = "gemini-2.0-flash-thinking-exp-preview-2025-special-ultra-long-id-extended-variant"
        models = [
            {"model_id": long_model_id, "display_name": "Ultra Long Model"}
        ]
        kb = keyboards.mode_model_selector_keyboard("stand", long_model_id, models, page=1)
        btn = kb.inline_keyboard[0][0]

        # Callback length must strictly obey Telegram limit <= 64 bytes
        self.assertLessEqual(len(btn.callback_data.encode("utf-8")), 64)
        self.assertTrue(btn.callback_data.startswith("smm:s:"))
        self.assertTrue(btn.callback_data.endswith(":p1"))

    @patch("handlers.menu.storage")
    async def test_aud_19_cb_set_mode_model_unambiguous_hash_mapping(self, mock_storage):
        import handlers.menu as menu_mod

        # Two models sharing the same long prefix
        model_alpha = "gemini-2.5-flash-preview-very-long-id-variant-extended-alpha-test"
        model_beta = "gemini-2.5-flash-preview-very-long-id-variant-extended-beta-test"

        models = [
            {"model_id": model_alpha, "display_name": "Alpha Model"},
            {"model_id": model_beta, "display_name": "Beta Model"},
        ]
        mock_storage.get_available_models = AsyncMock(return_value=models)
        mock_storage.set_user_mode_model = AsyncMock()
        mock_storage.clear_history = AsyncMock()

        # Generate selector to get exact callback data for model_beta (which gets trimmed)
        kb = keyboards.mode_model_selector_keyboard("stand", model_alpha, models, page=1)
        btn_beta = kb.inline_keyboard[1][0]
        self.assertEqual(btn_beta.text, "Beta Model")

        # Verify that it was actually trimmed and contains a hash
        self.assertIn("_", btn_beta.callback_data)
        self.assertNotEqual(model_beta, btn_beta.callback_data.split(":")[2])

        cb = MagicMock(spec=CallbackQuery)
        cb.data = btn_beta.callback_data
        cb.from_user = self.user
        cb.message = MagicMock()
        cb.message.chat = self.chat
        cb.message.edit_text = AsyncMock()
        cb.answer = AsyncMock()

        await menu_mod.cb_set_mode_model(cb)

        # Must correctly resolve to model_beta (not model_alpha) despite common prefix!
        mock_storage.set_user_mode_model.assert_called_once_with(
            self.user.id, "stand", model_beta
        )


if __name__ == "__main__":
    unittest.main()
