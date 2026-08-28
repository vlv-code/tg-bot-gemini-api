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
    tts_model: str = "gemini-3.1-flash-tts-preview"
    tts_voice: str = "Aoede"
    history: list[Turn] = field(default_factory=list)


class UserStorage:
    def __init__(
        self,
        db_path: str,
        default_model: str,
        max_history: int,
        default_tts_model: str = "gemini-3.1-flash-tts-preview",
        default_tts_voice: str = "Aoede",
    ) -> None:
        self._db_path = db_path
        self._default_model = default_model
        self._max_history = max_history
        self._default_tts_model = default_tts_model
        self._default_tts_voice = default_tts_voice
        self._db: Optional[aiosqlite.Connection] = None
        self._init_lock = asyncio.Lock()

    async def init_db(self) -> None:
        """Инициализирует постоянное соединение с базой данных, WAL-режим и таблицы."""
        if self._db is not None:
            return

        async with self._init_lock:
            if self._db is not None:
                return

            dirname = os.path.dirname(os.path.abspath(self._db_path))
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
                    tts_model TEXT NOT NULL DEFAULT '',
                    tts_voice TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            # Миграция: добавляем tts_model и tts_voice, если их ещё нет в существующей БД
            cursor = await db.execute("PRAGMA table_info(users)")
            columns = [row["name"] for row in await cursor.fetchall()]
            if "tts_model" not in columns:
                await db.execute(
                    f"ALTER TABLE users ADD COLUMN tts_model TEXT NOT NULL DEFAULT '{self._default_tts_model}'"
                )
            if "tts_voice" not in columns:
                await db.execute(
                    f"ALTER TABLE users ADD COLUMN tts_voice TEXT NOT NULL DEFAULT '{self._default_tts_voice}'"
                )

            # Миграция: добавляем chat_id в history, если его ещё нет
            cursor = await db.execute("PRAGMA table_info(history)")
            hist_columns = [row["name"] for row in await cursor.fetchall()]
            if hist_columns and "chat_id" not in hist_columns:
                await db.execute("ALTER TABLE history ADD COLUMN chat_id INTEGER NOT NULL DEFAULT 0")
                await db.execute("UPDATE history SET chat_id = user_id WHERE chat_id = 0")

            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL DEFAULT 0,
                    user_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_history_chat_user ON history (chat_id, user_id, id)"
            )

            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS token_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL DEFAULT 0,
                    model TEXT NOT NULL,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    candidates_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_token_usage_user_time ON token_usage (user_id, created_at)"
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
            INSERT OR IGNORE INTO users (user_id, model, rich_mode, voice_mode, system_prompt, tts_model, tts_voice)
            VALUES (?, ?, 1, 0, '', ?, ?)
            """,
            (user_id, self._default_model, self._default_tts_model, self._default_tts_voice),
        )

    async def get(self, user_id: int, chat_id: Optional[int] = None) -> UserState:
        """Получает состояние пользователя и последние сообщения истории конкретного чата."""
        db = await self._ensure_db()
        await self._ensure_user(db, user_id)

        target_chat_id = chat_id if chat_id is not None else user_id

        cursor = await db.execute(
            "SELECT model, rich_mode, voice_mode, system_prompt, tts_model, tts_voice FROM users WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()

        model = row["model"]
        rich_mode = bool(row["rich_mode"])
        voice_mode = bool(row["voice_mode"])
        system_prompt = row["system_prompt"] or ""
        tts_model = row["tts_model"] or self._default_tts_model
        tts_voice = row["tts_voice"] or self._default_tts_voice

        cursor = await db.execute(
            """
            SELECT role, text FROM (
                SELECT id, role, text FROM history
                WHERE chat_id = ? AND user_id = ?
                ORDER BY id DESC
                LIMIT ?
            ) ORDER BY id ASC
            """,
            (target_chat_id, user_id, self._max_history),
        )
        rows = await cursor.fetchall()
        history = [Turn(role=r["role"], text=r["text"]) for r in rows]

        return UserState(
            model=model,
            rich_mode=rich_mode,
            voice_mode=voice_mode,
            system_prompt=system_prompt,
            tts_model=tts_model,
            tts_voice=tts_voice,
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

    async def set_tts_model(self, user_id: int, tts_model: str) -> None:
        db = await self._ensure_db()
        await self._ensure_user(db, user_id)
        await db.execute(
            "UPDATE users SET tts_model = ? WHERE user_id = ?",
            (tts_model, user_id),
        )
        await db.commit()

    async def set_tts_voice(self, user_id: int, voice: str) -> None:
        db = await self._ensure_db()
        await self._ensure_user(db, user_id)
        await db.execute(
            "UPDATE users SET tts_voice = ? WHERE user_id = ?",
            (voice, user_id),
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

    async def add_turn(
        self, user_id: int, role: str, text: str, chat_id: Optional[int] = None
    ) -> None:
        target_chat_id = chat_id if chat_id is not None else user_id
        db = await self._ensure_db()
        await self._ensure_user(db, user_id)
        await db.execute(
            "INSERT INTO history (chat_id, user_id, role, text) VALUES (?, ?, ?, ?)",
            (target_chat_id, user_id, role, text),
        )
        await db.execute(
            """
            DELETE FROM history
            WHERE chat_id = ? AND user_id = ? AND id NOT IN (
                SELECT id FROM history
                WHERE chat_id = ? AND user_id = ?
                ORDER BY id DESC
                LIMIT ?
            )
            """,
            (target_chat_id, user_id, target_chat_id, user_id, self._max_history),
        )
        await db.commit()

    async def clear_history(self, user_id: int, chat_id: Optional[int] = None) -> None:
        target_chat_id = chat_id if chat_id is not None else user_id
        db = await self._ensure_db()
        await db.execute(
            "DELETE FROM history WHERE chat_id = ? AND user_id = ?",
            (target_chat_id, user_id),
        )
        await db.commit()

    async def record_token_usage(
        self,
        user_id: int,
        chat_id: int,
        model: str,
        prompt_tokens: int,
        candidates_tokens: int,
        total_tokens: int,
    ) -> None:
        """Записывает точный расход токенов из usage_metadata ответа Gemini."""
        db = await self._ensure_db()
        await db.execute(
            """
            INSERT INTO token_usage (user_id, chat_id, model, prompt_tokens, candidates_tokens, total_tokens)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, chat_id, model, prompt_tokens, candidates_tokens, total_tokens),
        )
        await db.commit()

    async def get_token_stats(self, user_id: int) -> dict[str, int]:
        """Возвращает агрегированную статистику токенов за сегодня и за всё время."""
        db = await self._ensure_db()

        # Статистика за сегодня
        cursor = await db.execute(
            """
            SELECT 
                COALESCE(SUM(prompt_tokens), 0) as today_prompt,
                COALESCE(SUM(candidates_tokens), 0) as today_candidates,
                COALESCE(SUM(total_tokens), 0) as today_total,
                COUNT(*) as today_requests
            FROM token_usage
            WHERE user_id = ? AND date(created_at) = date('now')
            """,
            (user_id,),
        )
        today_row = await cursor.fetchone()

        # Статистика за всё время
        cursor = await db.execute(
            """
            SELECT 
                COALESCE(SUM(prompt_tokens), 0) as all_prompt,
                COALESCE(SUM(candidates_tokens), 0) as all_candidates,
                COALESCE(SUM(total_tokens), 0) as all_total,
                COUNT(*) as all_requests
            FROM token_usage
            WHERE user_id = ?
            """,
            (user_id,),
        )
        all_row = await cursor.fetchone()

        return {
            "today_prompt": int(today_row["today_prompt"]) if today_row else 0,
            "today_candidates": int(today_row["today_candidates"]) if today_row else 0,
            "today_total": int(today_row["today_total"]) if today_row else 0,
            "today_requests": int(today_row["today_requests"]) if today_row else 0,
            "all_prompt": int(all_row["all_prompt"]) if all_row else 0,
            "all_candidates": int(all_row["all_candidates"]) if all_row else 0,
            "all_total": int(all_row["all_total"]) if all_row else 0,
            "all_requests": int(all_row["all_requests"]) if all_row else 0,
        }



