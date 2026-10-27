from dataclasses import dataclass, field
import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class AccumulatedMessage:
    text: str = ""
    message_id: int = 0
    counterparty_name: str = "Собеседник"
    raw_message: Optional[Any] = None
    created_at: float = field(default_factory=time.time)


class DebounceManager:
    """
    In-memory debounce buffer manager for Secretary Mode.
    Groups rapid incoming messages for a specific (business_connection_id, chat_id)
    key and flushes them together after an inactivity delay.
    """

    def __init__(
        self,
        delay_seconds: float = 3.5,
        on_flush: Optional[Callable[[tuple, list[AccumulatedMessage]], Awaitable[None]]] = None,
    ):
        self.delay_seconds = delay_seconds
        self.on_flush = on_flush

        self._queues: dict[tuple, list[AccumulatedMessage]] = {}
        self._timers: dict[tuple, asyncio.TimerHandle] = {}
        self._generations: dict[tuple, int] = {}
        self._lock = asyncio.Lock()
        self._active_tasks: set[asyncio.Task] = set()

    def _pop_items_locked(self, key: tuple) -> Optional[list[AccumulatedMessage]]:
        timer = self._timers.pop(key, None)
        if timer is not None:
            timer.cancel()
        self._generations.pop(key, None)
        return self._queues.pop(key, None)

    def _fire_timeout(self, key: tuple, gen: int) -> None:
        task = asyncio.create_task(self._on_timeout(key, gen))
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)

    async def _on_timeout(self, key: tuple, gen: int) -> None:
        try:
            async with self._lock:
                if self._generations.get(key) != gen:
                    return
                items = self._pop_items_locked(key)

            if not items:
                return

            if self.on_flush is not None:
                await self.on_flush(key, items)
        except Exception as e:
            logger.exception("Error in DebounceManager on_flush for key %s: %s", key, e)

    async def push(self, key: tuple, item: AccumulatedMessage) -> None:
        """
        Appends an item to the queue for `key` and resets/schedules the debounce timer.
        """
        async with self._lock:
            old_timer = self._timers.pop(key, None)
            if old_timer is not None:
                old_timer.cancel()

            gen = self._generations.get(key, 0) + 1
            self._generations[key] = gen

            if key not in self._queues:
                self._queues[key] = []
            self._queues[key].append(item)

            if self.delay_seconds <= 0:
                items = self._pop_items_locked(key)
                if items and self.on_flush is not None:
                    await self.on_flush(key, items)
                return

            loop = asyncio.get_running_loop()
            timer = loop.call_later(self.delay_seconds, self._fire_timeout, key, gen)
            self._timers[key] = timer

    async def flush(self, key: tuple) -> Optional[list[AccumulatedMessage]]:
        """
        Cancels active timer for `key`, clears and returns accumulated messages.
        If `on_flush` was registered, awaits `on_flush(key, items)`.
        """
        async with self._lock:
            items = self._pop_items_locked(key)

        if not items:
            return None

        if self.on_flush is not None:
            await self.on_flush(key, items)

        return items

    async def cancel(self, key: tuple) -> None:
        """
        Cancels timer and discards accumulated messages for `key` without invoking `on_flush`.
        """
        async with self._lock:
            self._pop_items_locked(key)

    def active_keys_count(self) -> int:
        """
        Returns the number of keys currently buffering messages.
        """
        return len(self._queues)

    def get_pending(self, key: tuple) -> list[AccumulatedMessage]:
        """
        Returns a shallow copy of buffered messages for `key` without flushing.
        """
        items = self._queues.get(key)
        return list(items) if items is not None else []

    async def close(self) -> None:
        """
        Cancels all pending timers and background tasks.
        """
        async with self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
            self._queues.clear()
            self._generations.clear()

        for task in list(self._active_tasks):
            task.cancel()
