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
from dataclasses import dataclass, field
import heapq
import itertools
import time
from typing import Any, AsyncIterator, Callable, Optional


class UserLocks:
    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    def get(self, user_id: int) -> asyncio.Lock:
        return self._locks[user_id]


@dataclass(order=True)
class QueueItem:
    priority: int
    timestamp: float
    sequence: int
    user_id: int = field(compare=False)
    future: asyncio.Future = field(compare=False)


class GlobalQueueManager:
    """Глобальный менеджер очереди запросов к Gemini с приоритизацией (суперадмины первыми)."""

    def __init__(self, max_concurrent: int = 5) -> None:
        self.max_concurrent = max_concurrent
        self._running_count: int = 0
        self._waiters: list[QueueItem] = []
        self._counter = itertools.count()
        self._lock = asyncio.Lock()

    @property
    def waiting_count(self) -> int:
        return len(self._waiters)

    @property
    def running_count(self) -> int:
        return self._running_count

    def get_user_position(self, user_id: int) -> Optional[int]:
        """Возвращает текущую позицию пользователя в очереди (1-indexed) или None."""
        sorted_waiters = sorted(self._waiters)
        for idx, item in enumerate(sorted_waiters, start=1):
            if item.user_id == user_id:
                return idx
        return None

    @asynccontextmanager
    async def acquire(
        self,
        user_id: int,
        priority: int = 2,
        on_waiting: Optional[Callable[[int], Any]] = None,
    ) -> AsyncIterator[int]:
        """Захватывает слот в глобальной очереди с учётом приоритета.

        priority: 0 — наивысший (суперадмин), 1 — админ, 2 — обычный пользователь.
        Если все слоты заняты, вызывается on_waiting(position).
        """
        loop = asyncio.get_running_loop()
        future: Optional[asyncio.Future] = None
        item: Optional[QueueItem] = None
        position = 0

        async with self._lock:
            if self._running_count < self.max_concurrent:
                self._running_count += 1
            else:
                future = loop.create_future()
                item = QueueItem(
                    priority=priority,
                    timestamp=time.monotonic(),
                    sequence=next(self._counter),
                    user_id=user_id,
                    future=future,
                )
                heapq.heappush(self._waiters, item)
                sorted_waiters = sorted(self._waiters)
                position = sorted_waiters.index(item) + 1

        if item is not None and future is not None:
            if on_waiting:
                try:
                    res = on_waiting(position)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:
                    pass

            try:
                await future
            except asyncio.CancelledError:
                async with self._lock:
                    if item in self._waiters:
                        self._waiters.remove(item)
                        heapq.heapify(self._waiters)
                    else:
                        # Слот уже был передан перед отменой, передаём следующему
                        self._release_slot_locked()
                raise

        try:
            yield position
        finally:
            async with self._lock:
                self._release_slot_locked()

    def _release_slot_locked(self) -> None:
        """Освобождает слот или передаёт его следующему самому приоритетному ожидающему."""
        while self._waiters:
            next_item = heapq.heappop(self._waiters)
            if not next_item.future.done():
                next_item.future.set_result(None)
                return
        self._running_count = max(0, self._running_count - 1)

