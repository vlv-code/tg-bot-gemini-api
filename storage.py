"""Асинхронное хранилище на базе aiosqlite (SQLite).

Сохраняет состояние пользователя (выбранную модель, rich-режим,
голосовой режим, индивидуальный system prompt) и историю диалогов
в файле базы данных SQLite.
"""

import os
from dataclasses import dataclass, field

import aiosqlite


@dataclass
class Turn:
    role: str  # "user" или "model"
    text: str


@dataclass
class UserState:
    model: str
    rich_mode: bool = True
    voice_mode: bool = False
    system_prompt: str = ""
    history: list[Turn] = field(default_factory=list)


class UserStorage:
    def __init__(self, db_path: str, default_model: str, max_history: int) -> None:
        self._db_path = db_path
        self._default_model = default_model
        self._max_history = max_history

    async def init_db(self) -> None:
        """Инициализирует базу данных и создаёт таблицы."""
        dirname = os.path.dirname(self._db_path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    model TEXT NOT NULL,
                    rich_mode INTEGER NOT NULL DEFAULT 1,
                    voice_mode INTEGER NOT NULL DEFAULT 0,
                    system_prompt TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_history_user_id ON history (user_id, id)"
            )
            await db.commit()

    async def get(self, user_id: int) -> UserState:
        """Получает состояние пользователя и последние сообщения истории."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT model, rich_mode, voice_mode, system_prompt FROM users WHERE user_id = ?",
                (user_id,),
            )
            row = await cursor.fetchone()

            if row is None:
                await db.execute(
                    """
                    INSERT INTO users (user_id, model, rich_mode, voice_mode, system_prompt)
                    VALUES (?, ?, 1, 0, '')
                    """,
                    (user_id, self._default_model),
                )
                await db.commit()
                model = self._default_model
                rich_mode = True
                voice_mode = False
                system_prompt = ""
            else:
                model = row["model"]
                rich_mode = bool(row["rich_mode"])
                voice_mode = bool(row["voice_mode"])
                system_prompt = row["system_prompt"] or ""

            cursor = await db.execute(
                """
                SELECT role, text FROM (
                    SELECT id, role, text FROM history
                    WHERE user_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                ) ORDER BY id ASC
                """,
                (user_id, self._max_history),
            )
            rows = await cursor.fetchall()
            history = [Turn(role=r["role"], text=r["text"]) for r in rows]

            return UserState(
                model=model,
                rich_mode=rich_mode,
                voice_mode=voice_mode,
                system_prompt=system_prompt,
                history=history,
            )

    async def set_model(self, user_id: int, model: str) -> None:
        await self.get(user_id)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE users SET model = ? WHERE user_id = ?",
                (model, user_id),
            )
            await db.commit()

    async def toggle_rich(self, user_id: int) -> bool:
        state = await self.get(user_id)
        new_val = not state.rich_mode
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE users SET rich_mode = ? WHERE user_id = ?",
                (1 if new_val else 0, user_id),
            )
            await db.commit()
        return new_val

    async def toggle_voice(self, user_id: int) -> bool:
        state = await self.get(user_id)
        new_val = not state.voice_mode
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE users SET voice_mode = ? WHERE user_id = ?",
                (1 if new_val else 0, user_id),
            )
            await db.commit()
        return new_val

    async def set_system_prompt(self, user_id: int, prompt: str) -> None:
        await self.get(user_id)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE users SET system_prompt = ? WHERE user_id = ?",
                (prompt, user_id),
            )
            await db.commit()

    async def add_turn(self, user_id: int, role: str, text: str) -> None:
        await self.get(user_id)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO history (user_id, role, text) VALUES (?, ?, ?)",
                (user_id, role, text),
            )
            await db.execute(
                """
                DELETE FROM history
                WHERE user_id = ? AND id NOT IN (
                    SELECT id FROM history
                    WHERE user_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                )
                """,
                (user_id, user_id, self._max_history),
            )
            await db.commit()

    async def clear_history(self, user_id: int) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "DELETE FROM history WHERE user_id = ?",
                (user_id,),
            )
            await db.commit()

