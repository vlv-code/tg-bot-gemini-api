"""Асинхронное хранилище на базе aiosqlite (SQLite).

Использует переиспользуемое постоянное соединение, WAL-режим для
конкурентной работы без блокировок и быстрый INSERT OR IGNORE вместо
лишних SELECT-запросов.
"""

import asyncio
import os
from dataclasses import dataclass, field
from typing import Optional

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
        self._db: Optional[aiosqlite.Connection] = None
        self._init_lock = asyncio.Lock()

    async def init_db(self) -> None:
        """Инициализирует постоянное соединение с базой данных, WAL-режим и таблицы."""
        if self._db is not None:
            return

        async with self._init_lock:
            if self._db is not None:
                return

            dirname = os.path.dirname(self._db_path)
            if dirname:
                os.makedirs(dirname, exist_ok=True)

            db = await aiosqlite.connect(self._db_path)
            db.row_factory = aiosqlite.Row

            # WAL режим и таймаут ожидания лока для параллельной работы хендлеров
            await db.execute("PRAGMA journal_mode = WAL;")
            await db.execute("PRAGMA synchronous = NORMAL;")
            await db.execute("PRAGMA busy_timeout = 5000;")

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

            self._db = db

    async def close(self) -> None:
        """Закрывает постоянное соединение с базой данных."""
        async with self._init_lock:
            if self._db is not None:
                await self._db.close()
                self._db = None

    async def _ensure_db(self) -> aiosqlite.Connection:
        if self._db is None:
            await self.init_db()
        return self._db

    async def _ensure_user(self, db: aiosqlite.Connection, user_id: int) -> None:
        """Гарантирует существование строки пользователя без лишней выборки."""
        await db.execute(
            """
            INSERT OR IGNORE INTO users (user_id, model, rich_mode, voice_mode, system_prompt)
            VALUES (?, ?, 1, 0, '')
            """,
            (user_id, self._default_model),
        )

    async def get(self, user_id: int) -> UserState:
        """Получает состояние пользователя и последние сообщения истории."""
        db = await self._ensure_db()
        await self._ensure_user(db, user_id)

        cursor = await db.execute(
            "SELECT model, rich_mode, voice_mode, system_prompt FROM users WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()

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
        db = await self._ensure_db()
        await self._ensure_user(db, user_id)
        await db.execute(
            "UPDATE users SET model = ? WHERE user_id = ?",
            (model, user_id),
        )
        await db.commit()

    async def toggle_rich(self, user_id: int) -> bool:
        db = await self._ensure_db()
        await self._ensure_user(db, user_id)
        await db.execute(
            "UPDATE users SET rich_mode = CASE WHEN rich_mode = 1 THEN 0 ELSE 1 END WHERE user_id = ?",
            (user_id,),
        )
        await db.commit()
        cursor = await db.execute("SELECT rich_mode FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        return bool(row["rich_mode"])

    async def toggle_voice(self, user_id: int) -> bool:
        db = await self._ensure_db()
        await self._ensure_user(db, user_id)
        await db.execute(
            "UPDATE users SET voice_mode = CASE WHEN voice_mode = 1 THEN 0 ELSE 1 END WHERE user_id = ?",
            (user_id,),
        )
        await db.commit()
        cursor = await db.execute("SELECT voice_mode FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        return bool(row["voice_mode"])

    async def set_system_prompt(self, user_id: int, prompt: str) -> None:
        db = await self._ensure_db()
        await self._ensure_user(db, user_id)
        await db.execute(
            "UPDATE users SET system_prompt = ? WHERE user_id = ?",
            (prompt, user_id),
        )
        await db.commit()

    async def add_turn(self, user_id: int, role: str, text: str) -> None:
        db = await self._ensure_db()
        await self._ensure_user(db, user_id)
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
        db = await self._ensure_db()
        await db.execute(
            "DELETE FROM history WHERE user_id = ?",
            (user_id,),
        )
        await db.commit()


