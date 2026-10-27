"""Rate limiting по пользователю: лимит в минуту + лимит в сутки.

In-memory реализация со скользящим окном. Живёт, пока жив процесс —
для продакшена с несколькими воркерами замени на Redis (ZADD/ZREMRANGEBYSCORE
делают ровно то же самое, только общее для всех воркеров). Методы уже
async def по той же причине, что и в storage.py: реализация сейчас
синхронная, но вызовы в handlers.py уже написаны через await, так что
переход на Redis не потребует правок вне этого файла.

check()/hit() защищены внутренним asyncio.Lock от гонок при параллельных
вызовах для одного и того же user_id / owner_user_id из разных бизнес-чатов.
"""

import asyncio
import time
from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass
class LimitStatus:
    allowed: bool
    used_minute: int
    limit_minute: int
    used_day: int
    limit_day: int
    retry_after: float = 0.0  # секунд до следующей попытки, если allowed=False


class RateLimiter:
    def __init__(self, per_minute: int, per_day: int) -> None:
        self.per_minute = per_minute
        self.per_day = per_day
        self._minute_hits: dict[int, deque] = defaultdict(deque)
        self._day_hits: dict[int, deque] = defaultdict(deque)
        self._lock = asyncio.Lock()

    @staticmethod
    def _trim(dq: deque, window_seconds: float, now: float) -> None:
        while dq and now - dq[0] > window_seconds:
            dq.popleft()

    async def check(self, user_id: int) -> LimitStatus:
        """Проверяет лимит, ничего не записывает."""
        async with self._lock:
            now = time.time()
            minute_dq = self._minute_hits[user_id]
            day_dq = self._day_hits[user_id]
            self._trim(minute_dq, 60, now)
            self._trim(day_dq, 86400, now)

            if len(minute_dq) >= self.per_minute:
                retry_after = 60 - (now - minute_dq[0])
                return LimitStatus(False, len(minute_dq), self.per_minute, len(day_dq), self.per_day, retry_after)
            if len(day_dq) >= self.per_day:
                retry_after = 86400 - (now - day_dq[0])
                return LimitStatus(False, len(minute_dq), self.per_minute, len(day_dq), self.per_day, retry_after)
            return LimitStatus(True, len(minute_dq), self.per_minute, len(day_dq), self.per_day)

    async def hit(self, user_id: int) -> None:
        """Фиксирует использование одного запроса. Вызывать ПОСЛЕ успешного ответа Gemini."""
        async with self._lock:
            now = time.time()
            self._minute_hits[user_id].append(now)
            self._day_hits[user_id].append(now)

    async def status(self, user_id: int) -> LimitStatus:
        """Текущий статус лимитов, без списания запроса."""
        return await self.check(user_id)
