import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456789:TEST_MOCK_TOKEN_FOR_UNITTESTS")
os.environ.setdefault("GEMINI_API_KEY", "AIzaSy_TEST_MOCK_KEY_FOR_UNITTESTS")

from aiogram.types import ChosenInlineResult, User
from handlers.inline import handle_chosen_inline_result


class TestInlineTTSRouting(unittest.IsolatedAsyncioTestCase):
    def _create_chosen_result(self, query: str) -> ChosenInlineResult:
        chosen = MagicMock(spec=ChosenInlineResult)
        chosen.inline_message_id = "test_inline_msg_id_123"
        chosen.query = query
        chosen.result_id = "test_res_id_456"
        chosen.from_user = User(id=12345678, is_bot=False, first_name="TestUser")
        chosen.bot = MagicMock()
        return chosen

    async def test_tts_queries_ignored_by_chosen_inline_result(self):
        tts_queries = [
            "~Привет",
            "/tts Привет",
            "tts Привет",
            "/гс Озвучь",
            "гс Озвучь",
        ]
        for query_text in tts_queries:
            with self.subTest(query=query_text):
                chosen = self._create_chosen_result(query_text)
                with patch("handlers.inline._run_background_task", side_effect=lambda coro: [coro.close(), MagicMock()][1]) as mock_run_bg:
                    await handle_chosen_inline_result(chosen)
                    mock_run_bg.assert_not_called()

    async def test_non_tts_queries_trigger_background_task(self):
        chosen = self._create_chosen_result("Обычный запрос")
        with patch("handlers.inline._run_background_task", side_effect=lambda coro: [coro.close(), MagicMock()][1]) as mock_run_bg:
            await handle_chosen_inline_result(chosen)
            mock_run_bg.assert_called_once()


if __name__ == "__main__":
    unittest.main()
