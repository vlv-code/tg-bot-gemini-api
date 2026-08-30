import os
import unittest

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:FAKE_TOKEN_FOR_TESTS")
os.environ.setdefault("GEMINI_API_KEY", "FAKE_KEY_FOR_TESTS")

from keyboards import inline_control_keyboard
from handlers.common import InlineSession
from handlers.inline import _extract_callback_author
from gemini_client import GeminiClient


class TestInlineSafetyAndKeyboards(unittest.TestCase):
    def test_inline_control_keyboard_with_and_without_author(self):
        # Without author_id
        kb_no_author = inline_control_keyboard("session123")
        buttons_no_author = [btn.callback_data for row in kb_no_author.inline_keyboard for btn in row]
        self.assertIn("inl_regen:session123", buttons_no_author)
        self.assertIn("inl_style:session123", buttons_no_author)
        self.assertIn("inl_fix:session123", buttons_no_author)
        self.assertIn("inl_del:session123", buttons_no_author)

        # With author_id
        kb_with_author = inline_control_keyboard("session123", author_id=987654321)
        buttons_with_author = [btn.callback_data for row in kb_with_author.inline_keyboard for btn in row]
        self.assertIn("inl_regen:session123:987654321", buttons_with_author)
        self.assertIn("inl_style:session123:987654321", buttons_with_author)
        self.assertIn("inl_fix:session123:987654321", buttons_with_author)
        self.assertIn("inl_del:session123:987654321", buttons_with_author)

    def test_extract_callback_author(self):
        # When session is active in memory
        session = InlineSession(
            session_id="s1",
            user_id=111,
            query="test",
            persona_id=None,
            persona_name="",
            persona_prompt="",
            is_quick=False,
            interactive=True,
        )
        self.assertEqual(_extract_callback_author("inl_del:s1", session), 111)

        # When session is expired (None), but author_id is encoded in callback_data
        self.assertEqual(_extract_callback_author("inl_del:s1:222", None), 222)
        self.assertEqual(_extract_callback_author("inl_regen:s1:333", None), 333)

        # When session is expired and callback_data has no author suffix
        self.assertIsNone(_extract_callback_author("inl_del:s1", None))
        self.assertIsNone(_extract_callback_author("malformed", None))

    def test_gemini_friendly_messages(self):
        # 429 Resource Exhausted with retryDelay
        exc_429 = Exception("ResourceExhausted: 429 quota exceeded, retryDelay '12.5s'")
        msg_429 = GeminiClient._friendly_message(exc_429)
        self.assertIn("12.5s", msg_429)

        # 429 Free Tier
        exc_freetier = Exception("429 RESOURCE_EXHAUSTED: limit: 10 per day FreeTier")
        msg_freetier = GeminiClient._friendly_message(exc_freetier)
        self.assertIn("10", msg_freetier)

        # 400 Invalid Argument sanitized
        exc_400 = Exception("400 INVALID_ARGUMENT: contents[0].parts[0] is empty or raw backend payload")
        msg_400 = GeminiClient._friendly_message(exc_400)
        self.assertIn("Gemini API", msg_400)

        # 503 Overloaded
        exc_503 = Exception("503 UNAVAILABLE: Model is overloaded due to high demand")
        msg_503 = GeminiClient._friendly_message(exc_503)
        self.assertIn("503", msg_503)


if __name__ == "__main__":
    unittest.main()