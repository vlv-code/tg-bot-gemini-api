import asyncio
import time
import unittest
from unittest.mock import patch

from rate_limiter import RateLimiter


class TestRateLimiter(unittest.IsolatedAsyncioTestCase):
    async def test_initial_state_allowed(self):
        limiter = RateLimiter(per_minute=5, per_day=20)
        status = await limiter.check(user_id=123)
        self.assertTrue(status.allowed)
        self.assertEqual(status.used_minute, 0)
        self.assertEqual(status.used_day, 0)
        self.assertEqual(status.limit_minute, 5)
        self.assertEqual(status.limit_day, 20)

    async def test_hit_increments_counters(self):
        limiter = RateLimiter(per_minute=5, per_day=20)
        await limiter.hit(user_id=123)
        await limiter.hit(user_id=123)

        status = await limiter.check(user_id=123)
        self.assertTrue(status.allowed)
        self.assertEqual(status.used_minute, 2)
        self.assertEqual(status.used_day, 2)

    async def test_minute_limit_exhausted(self):
        limiter = RateLimiter(per_minute=3, per_day=20)
        for _ in range(3):
            await limiter.hit(user_id=100)

        status = await limiter.check(user_id=100)
        self.assertFalse(status.allowed)
        self.assertEqual(status.used_minute, 3)
        self.assertGreater(status.retry_after, 0.0)

    async def test_day_limit_exhausted(self):
        limiter = RateLimiter(per_minute=10, per_day=3)
        for _ in range(3):
            await limiter.hit(user_id=200)

        status = await limiter.check(user_id=200)
        self.assertFalse(status.allowed)
        self.assertEqual(status.used_day, 3)
        self.assertGreater(status.retry_after, 0.0)

    async def test_sliding_window_expiration(self):
        limiter = RateLimiter(per_minute=2, per_day=10)
        now = time.time()

        with patch("time.time", return_value=now):
            await limiter.hit(user_id=300)
            await limiter.hit(user_id=300)
            status = await limiter.check(user_id=300)
            self.assertFalse(status.allowed)

        # Прошло 65 секунд: минутное окно должно очиститься
        with patch("time.time", return_value=now + 65):
            status = await limiter.check(user_id=300)
            self.assertTrue(status.allowed)
            self.assertEqual(status.used_minute, 0)
            self.assertEqual(status.used_day, 2)


if __name__ == "__main__":
    unittest.main()
