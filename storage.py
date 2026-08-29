"""Асинхронное хранилище на базе aiosqlite (SQLite).

Использует переиспользуемое постоянное соединение, WAL-режим для
конкурентной работы без блокировок и быстрый INSERT OR IGNORE вместо
лишних SELECT-запросов.
"""

import asyncio
import hashlib
import os
from dataclasses import dataclass, field
from typing import Optional

import aiosqlite

from config import settings


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
    quick_prompt: str = ""
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
                    quick_prompt TEXT NOT NULL DEFAULT '',
                    tts_model TEXT NOT NULL DEFAULT '',
                    tts_voice TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            # Миграция: добавляем tts_model, tts_voice и quick_prompt, если их ещё нет в существующей БД
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
            if "quick_prompt" not in columns:
                await db.execute(
                    "ALTER TABLE users ADD COLUMN quick_prompt TEXT NOT NULL DEFAULT ''"
                )

            # Миграция: добавляем chat_id и mode в history, если их ещё нет
            cursor = await db.execute("PRAGMA table_info(history)")
            hist_columns = [row["name"] for row in await cursor.fetchall()]
            if hist_columns and "chat_id" not in hist_columns:
                await db.execute("ALTER TABLE history ADD COLUMN chat_id INTEGER NOT NULL DEFAULT 0")
                await db.execute("UPDATE history SET chat_id = user_id WHERE chat_id = 0")
            if hist_columns and "mode" not in hist_columns:
                await db.execute("ALTER TABLE history ADD COLUMN mode TEXT NOT NULL DEFAULT 'main'")

            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL DEFAULT 0,
                    user_id INTEGER NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'main',
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_history_chat_user ON history (chat_id, user_id, mode, id)"
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

            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS user_chats (
                    user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    chat_title TEXT NOT NULL DEFAULT '',
                    chat_type TEXT NOT NULL DEFAULT 'private',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, chat_id)
                )
                """
            )

            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS allowed_users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL DEFAULT '',
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    added_by INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS tts_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cache_key TEXT UNIQUE NOT NULL,
                    text TEXT NOT NULL,
                    voice TEXT NOT NULL,
                    model TEXT NOT NULL,
                    file_id TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS user_prompts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'main',
                    name TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (user_id, mode, name)
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_prompts_user_mode ON user_prompts (user_id, mode)"
            )

            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS user_pinned_personas (
                    user_id INTEGER NOT NULL,
                    persona_id TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, persona_id)
                )
                """
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
            INSERT OR IGNORE INTO users (user_id, model, rich_mode, voice_mode, system_prompt, quick_prompt, tts_model, tts_voice)
            VALUES (?, ?, 1, 0, '', '', ?, ?)
            """,
            (user_id, self._default_model, self._default_tts_model, self._default_tts_voice),
        )

    async def get(
        self, user_id: int, chat_id: Optional[int] = None, mode: str = "main"
    ) -> UserState:
        """Получает состояние пользователя и последние сообщения истории конкретного чата и режима."""
        db = await self._ensure_db()
        await self._ensure_user(db, user_id)

        target_chat_id = chat_id if chat_id is not None else user_id

        cursor = await db.execute(
            "SELECT model, rich_mode, voice_mode, system_prompt, quick_prompt, tts_model, tts_voice FROM users WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()

        model = row["model"]
        rich_mode = bool(row["rich_mode"])
        voice_mode = bool(row["voice_mode"])
        system_prompt = row["system_prompt"] or ""
        quick_prompt = row["quick_prompt"] or ""
        tts_model = row["tts_model"] or self._default_tts_model
        tts_voice = row["tts_voice"] or self._default_tts_voice

        cursor = await db.execute(
            """
            SELECT role, text FROM (
                SELECT id, role, text FROM history
                WHERE chat_id = ? AND user_id = ? AND mode = ?
                ORDER BY id DESC
                LIMIT ?
            ) ORDER BY id ASC
            """,
            (target_chat_id, user_id, mode, self._max_history),
        )
        rows = await cursor.fetchall()
        history = [Turn(role=r["role"], text=r["text"]) for r in rows]

        return UserState(
            model=model,
            rich_mode=rich_mode,
            voice_mode=voice_mode,
            system_prompt=system_prompt,
            quick_prompt=quick_prompt,
            tts_model=tts_model,
            tts_voice=tts_voice,
            history=history,
        )

    async def get_settings(self, user_id: int) -> UserState:
        """Получает только настройки пользователя без выборки истории сообщений."""
        db = await self._ensure_db()
        await self._ensure_user(db, user_id)

        cursor = await db.execute(
            "SELECT model, rich_mode, voice_mode, system_prompt, quick_prompt, tts_model, tts_voice FROM users WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()

        return UserState(
            model=row["model"],
            rich_mode=bool(row["rich_mode"]),
            voice_mode=bool(row["voice_mode"]),
            system_prompt=row["system_prompt"] or "",
            quick_prompt=row["quick_prompt"] or "",
            tts_model=row["tts_model"] or self._default_tts_model,
            tts_voice=row["tts_voice"] or self._default_tts_voice,
            history=[],
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

    async def set_quick_prompt(self, user_id: int, prompt: str) -> None:
        db = await self._ensure_db()
        await self._ensure_user(db, user_id)
        await db.execute(
            "UPDATE users SET quick_prompt = ? WHERE user_id = ?",
            (prompt, user_id),
        )
        await db.commit()

    async def add_turn(
        self,
        user_id: int,
        role: str,
        text: str,
        chat_id: Optional[int] = None,
        mode: str = "main",
    ) -> None:
        target_chat_id = chat_id if chat_id is not None else user_id
        db = await self._ensure_db()
        await self._ensure_user(db, user_id)
        await db.execute(
            "INSERT INTO history (chat_id, user_id, mode, role, text) VALUES (?, ?, ?, ?, ?)",
            (target_chat_id, user_id, mode, role, text),
        )
        await db.execute(
            """
            DELETE FROM history
            WHERE chat_id = ? AND user_id = ? AND mode = ? AND id NOT IN (
                SELECT id FROM history
                WHERE chat_id = ? AND user_id = ? AND mode = ?
                ORDER BY id DESC
                LIMIT ?
            )
            """,
            (target_chat_id, user_id, mode, target_chat_id, user_id, mode, self._max_history),
        )
        await db.commit()

    async def track_user_chat(
        self,
        user_id: int,
        chat_id: int,
        chat_title: str = "",
        chat_type: str = "private",
    ) -> None:
        """Сохраняет или обновляет название и тип чата для пользователя."""
        db = await self._ensure_db()
        clean_title = chat_title.strip()
        if not clean_title:
            clean_title = "Личные сообщения" if chat_id == user_id else f"Чат {chat_id}"
        await db.execute(
            """
            INSERT INTO user_chats (user_id, chat_id, chat_title, chat_type, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET
                chat_title = excluded.chat_title,
                chat_type = excluded.chat_type,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, chat_id, clean_title, chat_type),
        )
        await db.commit()

    async def get_user_chats_with_history(self, user_id: int) -> list[dict]:
        """Возвращает список всех чатов пользователя, где есть сохранённая история."""
        db = await self._ensure_db()
        cursor = await db.execute(
            """
            SELECT 
                h.chat_id,
                COALESCE(NULLIF(c.chat_title, ''), CASE WHEN h.chat_id = h.user_id THEN 'Личные сообщения' ELSE 'Чат ' || h.chat_id END) as chat_title,
                COALESCE(c.chat_type, 'private') as chat_type,
                COUNT(h.id) as message_count
            FROM history h
            LEFT JOIN user_chats c ON h.user_id = c.user_id AND h.chat_id = c.chat_id
            WHERE h.user_id = ?
            GROUP BY h.chat_id
            HAVING COUNT(h.id) > 0
            ORDER BY MAX(h.id) DESC
            """,
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "chat_id": int(r["chat_id"]),
                "chat_title": str(r["chat_title"]),
                "chat_type": str(r["chat_type"]),
                "message_count": int(r["message_count"]),
            }
            for r in rows
        ]

    async def clear_history(
        self, user_id: int, chat_id: Optional[int] = None, all_chats: bool = False
    ) -> int:
        """Очищает историю диалога: конкретного чата либо всех чатов пользователя."""
        db = await self._ensure_db()
        if all_chats:
            cursor = await db.execute(
                "DELETE FROM history WHERE user_id = ?",
                (user_id,),
            )
        else:
            target_chat_id = chat_id if chat_id is not None else user_id
            cursor = await db.execute(
                "DELETE FROM history WHERE chat_id = ? AND user_id = ?",
                (target_chat_id, user_id),
            )
        await db.commit()
        return cursor.rowcount

    async def is_user_admin(self, user_id: int) -> bool:
        """Проверяет, является ли пользователь суперадмином или админом в БД."""
        if user_id in settings.admin_ids:
            return True
        db = await self._ensure_db()
        cursor = await db.execute(
            "SELECT is_admin FROM allowed_users WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
        return bool(row and row["is_admin"])

    async def get_whitelist_mode(self) -> bool:
        """Возвращает статус режима белого списка (True = доступ только разрешённым)."""
        db = await self._ensure_db()
        cursor = await db.execute(
            "SELECT value FROM bot_settings WHERE key = 'whitelist_mode'",
        )
        row = await cursor.fetchone()
        if row is not None:
            return row["value"] == "1"
        # По умолчанию включен, если в .env задан список ALLOWED_USER_IDS
        return bool(settings.allowed_user_ids)

    async def toggle_whitelist_mode(self) -> bool:
        """Переключает режим белого списка."""
        current = await self.get_whitelist_mode()
        new_val = "0" if current else "1"
        db = await self._ensure_db()
        await db.execute(
            "INSERT INTO bot_settings (key, value) VALUES ('whitelist_mode', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (new_val,),
        )
        await db.commit()
        return new_val == "1"

    async def is_user_allowed(self, user_id: int) -> bool:
        """Проверяет, разрешён ли доступ пользователю с учётом белого списка."""
        if await self.is_user_admin(user_id):
            return True
        if not await self.get_whitelist_mode():
            return True
        if user_id in settings.allowed_user_ids:
            return True
        db = await self._ensure_db()
        cursor = await db.execute(
            "SELECT 1 FROM allowed_users WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
        return row is not None

    async def add_allowed_user(
        self,
        user_id: int,
        username: str = "",
        is_admin: bool = False,
        added_by: int = 0,
    ) -> None:
        """Добавляет пользователя в белый список."""
        db = await self._ensure_db()
        await db.execute(
            """
            INSERT INTO allowed_users (user_id, username, is_admin, added_by)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = CASE WHEN excluded.username != '' THEN excluded.username ELSE allowed_users.username END,
                is_admin = excluded.is_admin
            """,
            (user_id, username, 1 if is_admin else 0, added_by),
        )
        await db.commit()

    async def remove_allowed_user(self, user_id: int) -> bool:
        """Удаляет пользователя из белого списка."""
        db = await self._ensure_db()
        cursor = await db.execute(
            "DELETE FROM allowed_users WHERE user_id = ?",
            (user_id,),
        )
        await db.commit()
        return cursor.rowcount > 0

    async def list_allowed_users(self) -> list[dict]:
        """Возвращает список всех разрешённых пользователей."""
        db = await self._ensure_db()
        cursor = await db.execute(
            "SELECT user_id, username, is_admin, added_by, created_at FROM allowed_users ORDER BY is_admin DESC, created_at DESC"
        )
        rows = await cursor.fetchall()
        return [
            {
                "user_id": int(r["user_id"]),
                "username": str(r["username"] or ""),
                "is_admin": bool(r["is_admin"]),
                "added_by": int(r["added_by"] or 0),
                "created_at": str(r["created_at"]),
            }
            for r in rows
        ]

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

    @staticmethod
    def _make_tts_cache_key(text: str, voice: str, model: str) -> str:
        raw = f"{text.strip().lower()}:{voice}:{model}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def get_cached_tts_voice(self, text: str, voice: str, model: str) -> Optional[str]:
        """Возвращает сохранённый voice_file_id из кэша, если такой текст уже озвучивался."""
        db = await self._ensure_db()
        key = self._make_tts_cache_key(text, voice, model)
        cursor = await db.execute(
            "SELECT file_id FROM tts_cache WHERE cache_key = ?",
            (key,),
        )
        row = await cursor.fetchone()
        return row["file_id"] if row else None

    async def save_cached_tts_voice(self, text: str, voice: str, model: str, file_id: str) -> None:
        """Сохраняет сгенерированный voice_file_id в базу данных."""
        db = await self._ensure_db()
        key = self._make_tts_cache_key(text, voice, model)
        await db.execute(
            """
            INSERT INTO tts_cache (cache_key, text, voice, model, file_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET file_id = excluded.file_id
            """,
            (key, text.strip(), voice, model, file_id),
        )
        await db.commit()

    # --- Управление сохранёнными личностями (Аватар) и промптами (Stand) ---

    async def get_saved_prompts(self, user_id: int, mode: str = "main") -> list[dict]:
        """Возвращает список сохранённых пользователем промптов/личностей."""
        db = await self._ensure_db()
        cursor = await db.execute(
            """
            SELECT id, name, prompt, created_at
            FROM user_prompts
            WHERE user_id = ? AND mode = ?
            ORDER BY id ASC
            """,
            (user_id, mode),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "prompt": row["prompt"],
                "created_at": row["created_at"],
                "is_builtin": False,
            }
            for row in rows
        ]

    async def get_all_personas(self, user_id: int) -> list[dict]:
        """Возвращает встроенные личности + сохранённые пользователем личности Аватара."""
        user_personas = await self.get_saved_prompts(user_id, mode="quick")
        user_names = {p["name"].lower() for p in user_personas}
        
        combined: list[dict] = []
        # Встроенные дефолтные личности
        for idx, bp in enumerate(DEFAULT_AVATAR_PERSONAS, start=1):
            if bp["name"].lower() not in user_names:
                combined.append({
                    "id": f"builtin_{idx}",
                    "name": bp["name"],
                    "title": bp["title"],
                    "prompt": bp["prompt"],
                    "is_builtin": True,
                })
        combined.extend(user_personas)
        return combined

    async def get_all_stand_presets(self, user_id: int) -> list[dict]:
        """Возвращает встроенные пресеты + сохранённые пользователем промпты Stand."""
        user_presets = await self.get_saved_prompts(user_id, mode="main")
        user_names = {p["name"].lower() for p in user_presets}

        combined: list[dict] = []
        for idx, bp in enumerate(DEFAULT_STAND_PRESETS, start=1):
            if bp["name"].lower() not in user_names:
                combined.append({
                    "id": f"builtin_{idx}",
                    "name": bp["name"],
                    "title": bp["title"],
                    "prompt": bp["prompt"],
                    "is_builtin": True,
                })
        combined.extend(user_presets)
        return combined

    async def save_user_prompt(self, user_id: int, name: str, prompt: str, mode: str = "main") -> int:
        """Сохраняет или обновляет именованный промпт / личность."""
        db = await self._ensure_db()
        await db.execute(
            """
            INSERT INTO user_prompts (user_id, mode, name, prompt)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, mode, name) DO UPDATE SET prompt = excluded.prompt
            """,
            (user_id, mode, name.strip(), prompt.strip()),
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT id FROM user_prompts WHERE user_id = ? AND mode = ? AND name = ?",
            (user_id, mode, name.strip()),
        )
        row = await cursor.fetchone()
        return row["id"] if row else 0

    async def delete_user_prompt(self, user_id: int, prompt_id: int) -> bool:
        """Удаляет сохранённый промпт по id."""
        db = await self._ensure_db()
        cursor = await db.execute(
            "DELETE FROM user_prompts WHERE id = ? AND user_id = ?",
            (prompt_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0

    async def delete_user_prompt_by_name(self, user_id: int, name: str, mode: str = "main") -> bool:
        """Удаляет сохранённый промпт по имени."""
        db = await self._ensure_db()
        cursor = await db.execute(
            "DELETE FROM user_prompts WHERE user_id = ? AND mode = ? AND lower(name) = lower(?)",
            (user_id, mode, name.strip()),
        )
        await db.commit()
        return cursor.rowcount > 0

    async def find_persona_by_name_or_id(self, user_id: int, identifier: str) -> Optional[dict]:
        """Ищет личность Аватара по названию или id."""
        all_personas = await self.get_all_personas(user_id)
        ident_clean = identifier.strip().lower()
        for p in all_personas:
            if str(p["id"]).lower() == ident_clean or p["name"].lower() == ident_clean:
                return p
        return None

    async def find_stand_preset_by_name_or_id(self, user_id: int, identifier: str) -> Optional[dict]:
        """Ищет пресет Stand по названию или id."""
        all_presets = await self.get_all_stand_presets(user_id)
        ident_clean = identifier.strip().lower()
        for p in all_presets:
            if str(p["id"]).lower() == ident_clean or p["name"].lower() == ident_clean:
                return p
        return None

    # --- Управление закреплёнными в инлайн-меню личностями ---

    async def get_pinned_persona_ids(self, user_id: int) -> set[str]:
        """Возвращает множество закрепленных persona_id для пользователя."""
        db = await self._ensure_db()
        cursor = await db.execute(
            "SELECT persona_id FROM user_pinned_personas WHERE user_id = ?",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return {str(r["persona_id"]) for r in rows}

    async def toggle_pinned_persona(self, user_id: int, persona_id: str | int) -> bool:
        """Переключает статус закрепления личности. Возвращает True, если закреплена, False если откреплена."""
        db = await self._ensure_db()
        p_id_str = str(persona_id).strip()
        cursor = await db.execute(
            "SELECT 1 FROM user_pinned_personas WHERE user_id = ? AND persona_id = ?",
            (user_id, p_id_str),
        )
        exists = await cursor.fetchone()
        if exists:
            await db.execute(
                "DELETE FROM user_pinned_personas WHERE user_id = ? AND persona_id = ?",
                (user_id, p_id_str),
            )
            await db.commit()
            return False
        else:
            await db.execute(
                "INSERT INTO user_pinned_personas (user_id, persona_id) VALUES (?, ?)",
                (user_id, p_id_str),
            )
            await db.commit()
            return True

    async def is_persona_pinned(self, user_id: int, persona_id: str | int) -> bool:
        """Проверяет, закреплена ли личность у пользователя."""
        db = await self._ensure_db()
        cursor = await db.execute(
            "SELECT 1 FROM user_pinned_personas WHERE user_id = ? AND persona_id = ?",
            (user_id, str(persona_id).strip()),
        )
        return (await cursor.fetchone()) is not None

    async def get_pinned_personas(self, user_id: int) -> list[dict]:
        """Возвращает список личностей, которые закреплены в избранное пользователем."""
        all_personas = await self.get_all_personas(user_id)
        pinned_ids = await self.get_pinned_persona_ids(user_id)
        return [p for p in all_personas if str(p["id"]) in pinned_ids]


DEFAULT_AVATAR_PERSONAS = [
    {
        "name": "Бро",
        "title": "🕶 Бро / Свой парень",
        "prompt": "Отвечай как близкий друг и свой парень. Живой разговорный стиль, лёгкий сленг, кратко и по делу, без официоза и без лишних церемоний.",
    },
    {
        "name": "Бизнес",
        "title": "💼 Деловой / Профи",
        "prompt": "Отвечай в профессиональном деловом стиле. Вежливо, чётко, структурированно, без воды, конструктивно и уверенно.",
    },
    {
        "name": "Сарказм",
        "title": "😈 Саркастичный / Ироничный",
        "prompt": "Отвечай остроумно, с лёгкой иронией, подколом или сарказмом, но без токсичной грубости.",
    },
    {
        "name": "Краткий",
        "title": "⚡️ Лаконичный / 1-5 слов",
        "prompt": "Отвечай максимально кратко: от 1 до 5 слов. Минимум текста, максимум конкретики, без лишних знаков и вводных слов.",
    },
    {
        "name": "Флирт",
        "title": "💫 Обаятельный / Флирт",
        "prompt": "Отвечай игриво, обаятельно, тепло, с лёгким намёком на флирт и комплименты.",
    },
]

DEFAULT_STAND_PRESETS = [
    {
        "name": "Кодер",
        "title": "💻 Senior Developer",
        "prompt": "Ты — Senior Software Engineer. Отвечай чистым кодом с лучшими практиками, объясняй архитектурные решения кратко и емко.",
    },
    {
        "name": "Сисадмин",
        "title": "🛠 Linux DevOps / Sysadmin",
        "prompt": "Ты — опытный Linux DevOps и Sysadmin. Сразу давай рабочие команды, bash-скрипты, docker-compose и конфигурации, без лишних вступлений.",
    },
    {
        "name": "Переводчик",
        "title": "🌍 Синхронный переводчик",
        "prompt": "Ты — профессиональный синхронный переводчик. Переводи точно, с сохранением идиом, сленга и контекста диалога.",
    },
    {
        "name": "Аналитик",
        "title": "📊 Бизнес-аналитик",
        "prompt": "Ты — стратегический аналитик. Структурируй ответы списками, выделяй плюсы/минусы, риски и выводы.",
    },
]



