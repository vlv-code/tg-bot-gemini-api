import os
import unittest

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:FAKE_TOKEN_FOR_TESTS")
os.environ.setdefault("GEMINI_API_KEY", "FAKE_KEY_FOR_TESTS")

from handlers.common import close_http_session, get_http_session


class TestHttpSessionSingleton(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await close_http_session()

    async def test_session_singleton_and_reuse(self):
        session1 = await get_http_session()
        self.assertFalse(session1.closed)

        session2 = await get_http_session()
        self.assertIs(session1, session2)

    async def test_close_and_recreate_session(self):
        session = await get_http_session()
        self.assertFalse(session.closed)

        await close_http_session()
        self.assertTrue(session.closed)

        # After closing, next call should lazily recreate a new open session
        new_session = await get_http_session()
        self.assertIsNot(session, new_session)
        self.assertFalse(new_session.closed)

        await close_http_session()
        self.assertTrue(new_session.closed)


if __name__ == "__main__":
    unittest.main()
