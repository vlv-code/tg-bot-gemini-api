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


class UserLocks:
    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    def get(self, user_id: int) -> asyncio.Lock:
        return self._locks[user_id]
