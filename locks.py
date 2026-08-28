"""Per-user asyncio-лок для сериализации обработки апдейтов одного юзера.

aiogram по умолчанию обрабатывает апдейты как параллельные таски
(Dispatcher.start_polling(handle_as_tasks=True) — дефолт). Без лока
это значит, что несколько сообщений от ОДНОГО юзера, отправленных
быстрее, чем успевает прийти ответ Gemini, могут все пройти
limiter.check() как allowed=True — limiter.hit() пишется только после
успешного ответа, а до этого момента ни одно параллельное сообщение
ещё не видно другим. Лок гарантирует, что для одного user_id весь
блок check → Gemini → hit выполняется только одной корутиной за раз.

Живёт, пока жив процесс — для нескольких воркеров такой лок не
сработает (память не общая), там нужен либо распределённый лок
(Redis SET NX PX), либо шардирование апдейтов по user_id между
воркерами.
"""

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Optional


class UserLocks:
    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    def get(self, user_id: int) -> asyncio.Lock:
        return self._locks[user_id]


class GlobalQueueManager:
    """Глобальный семафор + трекер очереди ожидания для запросов к Gemini API."""

    def __init__(self, max_concurrent: int = 5) -> None:
        self.max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._waiting_count: int = 0
        self._lock = asyncio.Lock()

    @property
    def waiting_count(self) -> int:
        return self._waiting_count

    @asynccontextmanager
    async def acquire(
        self,
        user_id: int,
        on_waiting: Optional[Callable[[int], Any]] = None,
    ) -> AsyncIterator[int]:
        """Захватывает слот в глобальной очереди.

        Если все слоты заняты, вызывает on_waiting(position) с номером позиции в очереди.
        """
        async with self._lock:
            if self._semaphore.locked():
                self._waiting_count += 1
                position = self._waiting_count
                if on_waiting:
                    try:
                        res = on_waiting(position)
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception:
                        pass
                should_decrement = True
            else:
                position = 0
                should_decrement = False

        try:
            await self._semaphore.acquire()
            yield position
        finally:
            if should_decrement:
                async with self._lock:
                    self._waiting_count = max(0, self._waiting_count - 1)
            self._semaphore.release()

