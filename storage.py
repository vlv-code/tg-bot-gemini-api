"""Простое in-memory хранилище per-user состояния.

Для одного процесса этого достаточно. Если бот будет жить на нескольких
воркерах или должен переживать перезапуски без потери истории — замени
UserStorage на обёртку над Redis/SQLite/Postgres, интерфейс
(get/set_model/...) можно оставить тем же — методы уже async def именно
для этого: сама реализация сейчас синхронная (dict), но вызовы в
handlers.py уже написаны как await storage.get(...), так что при
переходе на настоящий асинхронный I/O (aiosqlite/asyncpg/redis.asyncio)
менять нужно будет только тело этого класса, а не handlers.py.
"""

from dataclasses import dataclass, field


@dataclass
class Turn:
    role: str  # "user" или "model"
    text: str


@dataclass
class UserState:
    model: str
    rich_mode: bool = True
    history: list[Turn] = field(default_factory=list)


class UserStorage:
    def __init__(self, default_model: str, max_history: int) -> None:
        self._data: dict[int, UserState] = {}
        self._default_model = default_model
        self._max_history = max_history

    async def get(self, user_id: int) -> UserState:
        if user_id not in self._data:
            self._data[user_id] = UserState(model=self._default_model)
        return self._data[user_id]

    async def set_model(self, user_id: int, model: str) -> None:
        state = await self.get(user_id)
        state.model = model

    async def toggle_rich(self, user_id: int) -> bool:
        state = await self.get(user_id)
        state.rich_mode = not state.rich_mode
        return state.rich_mode

    async def add_turn(self, user_id: int, role: str, text: str) -> None:
        state = await self.get(user_id)
        state.history.append(Turn(role, text))
        if len(state.history) > self._max_history:
            state.history = state.history[-self._max_history :]

    async def clear_history(self, user_id: int) -> None:
        state = await self.get(user_id)
        state.history.clear()
