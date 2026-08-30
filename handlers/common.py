import asyncio
from dataclasses import dataclass, field
import hashlib
import html
import io
import ipaddress
import logging
import re
import time
from typing import Optional, Sequence, Union
import urllib.parse

import aiohttp
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.chat_action import ChatActionSender
from google.genai import types

from audio import convert_gemini_audio
from config import settings
from formatting import find_utf16_cut, markdown_to_chunks, split_plain_text, utf16_len
from gemini_client import GeminiError, GeminiResponse, build_gemini_client
from keyboards import inline_control_keyboard
from locks import GlobalQueueManager, UserLocks
from rate_limiter import RateLimiter
from storage import UserState, UserStorage

logger = logging.getLogger(__name__)

URL_REGEX = re.compile(r'https?://[^\s<>"]+')
IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp')
INLINE_ANSWER_LIMIT = 3900

# Защита от сборщика мусора CPython для фоновых inline-задач
_background_tasks: set[asyncio.Task] = set()


def _run_background_task(coro) -> asyncio.Task:
    """Запускает фоновую задачу с защитой от сборщика мусора CPython."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


@dataclass
class InlineSession:
    session_id: str
    user_id: int
    query: str
    persona_id: Optional[Union[str, int]] = None
    persona_name: str = "Дефолтный суфлёр"
    persona_prompt: str = ""
    is_quick: bool = False
    interactive: bool = False
    created_at: float = field(default_factory=time.time)


# Временный кэш сессий для инлайн-генерации и инлайн-кнопок (session_id -> InlineSession)
_inline_sessions: dict[str, InlineSession] = {}
# Активные задачи debounce для инлайн TTS (user_id -> query_token)
_active_inline_tts_tasks: dict[int, str] = {}


def _cleanup_inline_sessions() -> None:
    now = time.time()
    expired = [sid for sid, s in _inline_sessions.items() if now - s.created_at > 7200]
    for sid in expired:
        _inline_sessions.pop(sid, None)
    if len(_inline_sessions) > 2000:
        for sid in list(_inline_sessions.keys())[:500]:
            _inline_sessions.pop(sid, None)


async def try_download_image_from_url(url: str) -> Optional[tuple[bytes, str]]:
    """Безопасно скачивает изображение по ссылке с защитой от SSRF и лимитом 15 МБ."""
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return None

        # SSRF-защита: валидируем IP хоста
        loop = asyncio.get_running_loop()
        addr_info = await loop.getaddrinfo(parsed.hostname, None)
        for *_, sockaddr in addr_info:
            ip_str = sockaddr[0]
            ip = ipaddress.ip_address(ip_str)
            if (
                ip.is_unspecified
                or ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
            ):
                logger.warning("Заблокирован SSRF-запрос к адресу %s (%s)", parsed.hostname, ip_str)
                return None

        max_bytes = 15 * 1024 * 1024
        headers = {"User-Agent": "Mozilla/5.0 (compatible; GeminiTelegramBot/1.0)"}
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8), allow_redirects=False) as resp:
                if resp.status == 200:
                    content_type = resp.headers.get("Content-Type", "").lower()
                    if not ("image/" in content_type or url.lower().endswith(IMAGE_EXTENSIONS)):
                        return None

                    mime = content_type.split(";")[0] if "image/" in content_type else "image/jpeg"

                    chunks = []
                    total_size = 0
                    async for chunk in resp.content.iter_chunked(64 * 1024):
                        total_size += len(chunk)
                        if total_size > max_bytes:
                            logger.warning("Изображение по ссылке %s превышает лимит 15 МБ, отмена", url)
                            return None
                        chunks.append(chunk)

                    return b"".join(chunks), mime
    except Exception as exc:
        logger.debug("Не удалось скачать изображение по ссылке %s: %s", url, exc)
    return None


storage = UserStorage(
    db_path=settings.db_path,
    default_model=settings.default_model,
    max_history=settings.max_history_messages,
    default_tts_model=settings.tts_model,
    default_tts_voice=settings.tts_voice,
)

limiter = RateLimiter(
    per_minute=settings.rate_limit_per_minute, per_day=settings.rate_limit_per_day
)
gemini_client = build_gemini_client(
    api_key=settings.gemini_api_key,
    default_system_prompt=settings.system_prompt,
    default_voice=settings.tts_voice,
    default_tts_model=settings.tts_model,
)
user_locks = UserLocks()
global_queue = GlobalQueueManager(max_concurrent=settings.max_concurrent_requests)


async def _limits_line(user_id: int) -> str:
    status = await limiter.status(user_id)
    return f"📊 {status.used_minute}/{status.limit_minute} в минуту · {status.used_day}/{status.limit_day} сегодня"


async def _get_user_priority(user_id: int) -> int:
    """Определяет уровень приоритета в глобальной очереди: 0 (суперадмин), 1 (админ), 2 (пользователь)."""
    if user_id in settings.admin_ids:
        return 0
    if await storage.is_user_admin(user_id):
        return 1
    return 2


def _render_main_menu_text(state: UserState) -> str:
    rich_str = "вкл ✅" if state.rich_mode else "выкл ❌"
    voice_str = "вкл 🎙" if state.voice_mode else "выкл 🔇"
    prompt_status = "индивидуальный" if state.system_prompt else "по умолчанию"
    history_count = len(state.history)

    return (
        "🤖 <b>Главное меню Gemini Bot</b>\n\n"
        f"• <b>Основная модель:</b> <code>{html.escape(state.model)}</code>\n"
        f"• <b>Озвучка (TTS):</b> <code>{html.escape(state.tts_model)}</code>\n"
        f"• <b>Голос синтеза:</b> <code>{html.escape(state.tts_voice)}</code>\n"
        f"• <b>Голосовые ответы:</b> {voice_str}\n"
        f"• <b>Rich-разметка:</b> {rich_str}\n"
        f"• <b>Системный промпт:</b> {prompt_status}\n"
        f"• <b>Контекст диалога:</b> {history_count} реплик в памяти этого чата\n\n"
        "<i>Используйте кнопки ниже для быстрой настройки без спама в чате:</i>"
    )


def _render_tts_menu_text(state: UserState) -> str:
    voice_str = "включены 🎙" if state.voice_mode else "выключены 🔇"
    return (
        "🎙 <b>Настройки озвучки и синтеза речи (TTS)</b>\n\n"
        f"• <b>Текущая TTS модель:</b> <code>{html.escape(state.tts_model)}</code>\n"
        f"• <b>Текущий голос:</b> <code>{html.escape(state.tts_voice)}</code>\n"
        f"• <b>Авто-ответы голосовыми:</b> {voice_str}\n\n"
        "<i>Выберите параметр для изменения:</i>"
    )


def _render_prompt_menu_text(state: UserState) -> str:
    effective = (
        state.system_prompt
        or settings.system_prompt
        or "(не задан — поведение Gemini по умолчанию)"
    )
    is_custom = bool(state.system_prompt)
    status = " (индивидуальный)" if is_custom else " (глобальный дефолт)"

    return (
        f"🥊 <b>Системный промпт (Stand-режим)</b>{status}:\n\n"
        f"<code>{html.escape(effective)}</code>\n\n"
        "• Чтобы выбрать готовый пресет или изменить свой: нажмите <b>«📚 Каталог промптов Stand»</b>\n"
        "• Чтобы сохранить/изменить промпт: <code>/prompt edit Имя = Текст</code>\n"
        "• Чтобы задать произвольный текст: <code>/prompt текст промпта</code>\n"
        "• Чтобы сбросить к дефолту: <code>/prompt reset</code>"
    )


def _render_qprompt_menu_text(state: UserState) -> str:
    effective = (
        state.quick_prompt
        or settings.quick_prompt
        or "(не задан — поведение Gemini по умолчанию)"
    )
    is_custom = bool(state.quick_prompt)
    status = " (индивидуальный)" if is_custom else " (глобальный дефолт)"

    return (
        f"🎭 <b>Системный промпт (Режим Аватара)</b>{status}:\n\n"
        f"<code>{html.escape(effective)}</code>\n\n"
        "• Чтобы выбрать или изменить личность: нажмите <b>«🎭 Личности Аватара»</b>\n"
        "• Чтобы создать/изменить личность: <code>/avatar edit Имя = Текст</code>\n"
        "• Чтобы сбросить к дефолту: <code>/avatar reset</code>"
    )


def _render_personas_menu_text(state: UserState, personas: list[dict]) -> str:
    current_prompt = state.quick_prompt or settings.quick_prompt
    active_name = "Дефолтный суфлёр"
    for p in personas:
        if p["prompt"].strip() == current_prompt.strip():
            active_name = p.get("title") or p["name"]
            break

    return (
        f"🎭 <b>Личности Аватара (Ghostwriter / Режим Аватара)</b>:\n\n"
        f"Активная личность: <b>{html.escape(active_name)}</b>\n\n"
        f"Текущий системный промпт:\n<code>{html.escape(current_prompt[:250])}{'…' if len(current_prompt) > 250 else ''}</code>\n\n"
        "<b>Управление личностями:</b>\n"
        "• <i>Выбор/Редактирование:</i> Нажмите на нужную личность в списке ниже\n"
        "• <i>Создать/Изменить:</i> <code>/avatar edit Имя = Текст</code>\n"
        "• <i>Переключить по имени:</i> <code>/avatar Имя</code>\n"
        "• <i>Удалить свою:</i> <code>/avatar del Имя</code>"
    )


def _render_stand_prompts_menu_text(state: UserState, presets: list[dict]) -> str:
    current_prompt = state.system_prompt or settings.system_prompt or "(по умолчанию)"
    active_name = "Дефолтный Stand"
    for p in presets:
        if p["prompt"].strip() == current_prompt.strip():
            active_name = p.get("title") or p["name"]
            break

    return (
        f"🥊 <b>Промпты и Роли Stand-режима</b>:\n\n"
        f"Активная роль: <b>{html.escape(active_name)}</b>\n\n"
        f"Текущий системный промпт:\n<code>{html.escape(current_prompt[:250])}{'…' if len(current_prompt) > 250 else ''}</code>\n\n"
        "<b>Управление промптами:</b>\n"
        "• <i>Выбор/Редактирование:</i> Нажмите на пресет в списке ниже\n"
        "• <i>Создать/Изменить:</i> <code>/prompt edit Имя = Текст</code>\n"
        "• <i>Активировать по имени:</i> <code>/prompt Имя</code>\n"
        "• <i>Задать произвольный:</i> <code>/prompt текст</code>\n"
        "• <i>Удалить свой:</i> <code>/prompt del Имя</code>"
    )


def _render_info_text() -> str:
    """Генерирует справочный текст с кратким руководством по всем функциям и режимам бота."""
    return (
        "🌟 <b>Справка и руководство по боту (Gemini Bot)</b>\n\n"
        "Бот работает в <b>двух независимых режимах</b> с раздельной памятью:\n\n"
        "🥊 <b>1. Stand-режим (Основной ИИ-помощник за спиной)</b>\n"
        "• <i>Как пользоваться:</i> Пишите боту напрямую, присылайте голосовые, фото, PDF или документы.\n"
        "• <i>Поведение:</i> Экспертный ИИ-собеседник. Анализирует код и файлы, помнит контекст диалога, цитирует запросы.\n"
        "• <i>Роли и промпты:</i> <code>/prompts</code> (Кодер, Сисадмин, Переводчик, Аналитик или свой через <code>/prompt edit</code>).\n\n"
        "🎭 <b>2. Режим Аватара (Ghostwriter / Текстовый суфлёр)</b>\n"
        "• <i>Как пользоваться:</i> В любом чате наберите <code>@bot_username ваш черновик</code> и выберите <b>«🎭 Отправить Avatar»</b>, либо ответьте на сообщение в чате через <code>/avatar</code>.\n"
        "• <i>Поведение:</i> Бот пишет <b>готовое сообщение за вас от 1-го лица</b> («я», «мне») в заданном стиле, без цитирования и без метатекста.\n"
        "• <i>Личности:</i> <code>/avatars</code> (Бро, Бизнес, Сарказм, Краткий, Флирт или своя через <code>/avatar edit</code>).\n\n"
        "🎙 <b>Озвучка (TTS) и Инлайн-режим:</b>\n"
        "• <code>/tts текст</code> — озвучить любой текст выбранным голосом.\n"
        "• В любом чате наберите <code>@bot_username текст</code> для выбора карточек: <b>🥊 Stand</b>, <b>🎭 Avatar</b> или <b>🎧 TTS</b>.\n\n"
        "🛠 <b>Полезные команды:</b>\n"
        "• <code>/menu</code> — главное интерактивное меню всех настроек\n"
        "• <code>/model</code> — выбор нейросети (Gemini 3.5 Flash Lite, 3.7 Flash и др.)\n"
        "• <code>/limits</code> — остаток лимитов запросов и токенов\n"
        "• <code>/clear</code> — очистка истории диалога"
    )


async def _render_limits_menu_text(user_id: int) -> str:
    status = await limiter.status(user_id)
    token_stats = await storage.get_token_stats(user_id)
    rem_min = max(0, status.limit_minute - status.used_minute)
    rem_day = max(0, status.limit_day - status.used_day)

    today_tok = f"{token_stats['today_total']:,}".replace(",", " ")
    today_prompt = f"{token_stats['today_prompt']:,}".replace(",", " ")
    today_cand = f"{token_stats['today_candidates']:,}".replace(",", " ")
    all_tok = f"{token_stats['all_total']:,}".replace(",", " ")

    return (
        "📊 <b>Статус квот и расхода токенов:</b>\n\n"
        "⏱ <b>Лимиты запросов (Rate Limits):</b>\n"
        f"• <b>В минуту (RPM):</b> <code>{status.used_minute}/{status.limit_minute}</code> (осталось: {rem_min})\n"
        f"• <b>В сутки (RPD):</b> <code>{status.used_day}/{status.limit_day}</code> (осталось: {rem_day})\n\n"
        "🚦 <b>Глобальная очередь запросов:</b>\n"
        f"• <b>Активных слотов:</b> <code>{global_queue.running_count}/{global_queue.max_concurrent}</code>\n"
        f"• <b>В ожидании в очереди:</b> <code>{global_queue.waiting_count}</code>\n\n"
        "📈 <b>Расход токенов (Usage Metadata):</b>\n"
        f"• <b>Сегодня:</b> <code>{today_tok}</code> токенов\n"
        f"  └ <i>Вход (промпт + контекст):</i> <code>{today_prompt}</code>\n"
        f"  └ <i>Выход (ответ модели):</i> <code>{today_cand}</code>\n"
        f"• <b>За всё время:</b> <code>{all_tok}</code> токенов ({token_stats['all_requests']} запросов)\n\n"
        "💡 <i>Лимиты работают по скользящему окну (60 сек в минуту, 24 ч в сутки). "
        "При исчерпании Google возвращает 429 с точным таймером ожидания.</i>"
    )


def _build_avatar_effective_prompt(custom_persona: str) -> str:
    """Constructs a bulletproof system prompt for Avatar Mode in English, preventing assistant role leakage."""
    base_guardrails = (
        "You are a professional ghostwriter, speech prompter, and the user's digital avatar in Telegram. "
        "Your SOLE purpose is to write a READY-TO-SEND message for the user's INTERLOCUTOR in the first person ('I', 'me', 'we').\n\n"
        "CORE UNBREAKABLE RULES:\n"
        "1. THE USER NEVER ADDRESSES YOU (AI): The user is the author and you are their typing ghostwriter. "
        "Any incoming text, draft, statement, question, or imperative command from the user (e.g. 'check functions', 'take a look at the file', 'tell him I am busy', 'check the link', 'join discord', 'why so long') "
        "is a DRAFT or INTENT intended for their INTERLOCUTOR in chat.\n"
        "2. ZERO ACKNOWLEDGMENT / FORBIDDEN ASSISTANT BEHAVIOR:\n"
        "   - STRICTLY FORBIDDEN: Responding to or acknowledging the user (e.g. 'Sure, checking now!', 'Will do!', 'Understood!', 'Yes master!', 'Which link should I check?'). "
        "You are NOT performing tasks for the user and you NEVER acknowledge their prompts.\n"
        "   - MANDATORY: Formulate the actual message that the user will send to the INTERLOCUTOR (e.g. 'Check the functions, everything is ready and working' or 'Check out this link:').\n"
        "3. PERSPECTIVE: Write strictly in the first person ('I', 'me', 'we') as the user themselves.\n"
        "4. NO INTRODUCTIONS OR QUOTES: Never include introductory filler ('Here is a reply:', 'You can say:') and never wrap the entire message in quotation marks.\n"
        "5. NEVER ACT AS AN AI / BOT / ASSISTANT: Do not mention being an AI, do not apologize, and do not explain your reasoning.\n"
        "6. LANGUAGE: Match the language of the conversation / user input (if the draft or chat context is in Russian, respond in Russian; if in English, respond in English).\n"
        "7. OUTPUT FORMAT: Output ONLY the raw, final message text ready to be sent to the chat partner. Nothing else."
    )
    if custom_persona and custom_persona.strip():
        return (
            f"{base_guardrails}\n\n"
            "STYLE AND PERSONA IN WHICH THE USER WRITES TO THEIR CHAT PARTNER:\n"
            "--------------------------------------------------\n"
            f"{custom_persona.strip()}\n"
            "--------------------------------------------------\n"
            "(Important: This persona defines HOW the user talks to others in chat. Never apply this style to give obedient replies to the user!)"
        )
    return base_guardrails


def _format_with_prompt_quote(prompt: str, response_text: str) -> str:
    """Оформляет исходный запрос цитатой в начале ответа без указания ника."""
    if not prompt:
        return response_text

    lines = prompt.strip().split("\n")
    quoted_lines = [f"> {line}" for line in lines]
    quote_block = "\n".join(quoted_lines)

    return f"{quote_block}\n\n{response_text}"


async def _send_response(
    message: Message,
    state: UserState,
    response: GeminiResponse,
    prompt_text: str = "",
    want_audio: bool = False,
) -> None:
    """Отправляет ответ пользователю (голосовое сообщение и/или текст с цитатой запроса)."""
    audio_bytes = response.audio_bytes
    if want_audio and not audio_bytes and response.text:
        try:
            audio_bytes = await gemini_client.generate_speech(
                text=response.text,
                voice_name=state.tts_voice,
                model=state.tts_model,
            )
        except Exception as exc:
            logger.warning("Не удалось синтезировать TTS для голосового ответа: %s", exc)

    if want_audio and audio_bytes:
        try:
            audio_data, audio_filename, _ = await convert_gemini_audio(audio_bytes)
            voice_file = BufferedInputFile(audio_data, filename=audio_filename)
            await message.answer_voice(voice_file)
        except Exception:
            logger.exception("Не удалось отправить голосовое сообщение, переключаемся на текст")

    if response.text:
        full_text = _format_with_prompt_quote(prompt_text, response.text)
        speak_kb = None
        if not want_audio:
            author_id = message.from_user.id if message.from_user else 0
            speak_kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🎙 Озвучить ответ",
                            callback_data=f"speak_response:{author_id}",
                        )
                    ]
                ]
            )

        if state.rich_mode:
            chunks = markdown_to_chunks(full_text)
            for idx, (chunk_text, chunk_entities) in enumerate(chunks):
                is_last = (idx == len(chunks) - 1)
                try:
                    await message.answer(
                        chunk_text,
                        entities=chunk_entities,
                        reply_markup=(speak_kb if is_last else None),
                    )
                except Exception:
                    logger.warning("Ошибка отправки с entities, отправляем чистым текстом")
                    plain_chunks = split_plain_text(chunk_text)
                    for p_idx, chunk in enumerate(plain_chunks):
                        p_is_last = is_last and (p_idx == len(plain_chunks) - 1)
                        await message.answer(
                            chunk,
                            reply_markup=(speak_kb if p_is_last else None),
                        )
        else:
            plain_chunks = split_plain_text(full_text)
            for idx, chunk in enumerate(plain_chunks):
                is_last = (idx == len(plain_chunks) - 1)
                await message.answer(
                    chunk,
                    reply_markup=(speak_kb if is_last else None),
                )


def _extract_reply_info(message: Message) -> tuple[Optional[str], Optional[Message]]:
    """
    Извлекает цитируемый текст и/или сообщение с медиа.
    Поддерживает:
    1. message.quote (цитирование текста в Telegram 10.2+)
    2. message.reply_to_message (стандартный Reply)
    3. message.external_reply (цитаты из других чатов/каналов)
    """
    replied_text: Optional[str] = None
    replied_msg: Optional[Message] = message.reply_to_message

    if getattr(message, "quote", None) and message.quote and getattr(message.quote, "text", None):
        replied_text = message.quote.text.strip()
    elif replied_msg:
        replied_text = (replied_msg.text or replied_msg.caption or "").strip()
    elif getattr(message, "external_reply", None):
        ext = message.external_reply
        if hasattr(ext, "quote") and ext.quote and hasattr(ext.quote, "text"):
            replied_text = (ext.quote.text or "").strip()

    return (replied_text or None), replied_msg


def _parse_caption_voice_flags(caption: Optional[str]) -> tuple[str, bool, bool]:
    """Проверяет команды /voice или /text и упоминания бота в подписи к файлу. Возвращает (clean_prompt, force_voice, force_text)."""
    if not caption:
        return "", False, False
    cap = caption.strip()
    cap = re.sub(r"^@\w+\s*", "", cap).strip()
    if cap.startswith(("/voice", "/v")):
        parts = cap.split(maxsplit=1)
        clean = parts[1].strip() if len(parts) > 1 else ""
        return clean, True, False
    if cap.startswith(("/text", "/t")):
        parts = cap.split(maxsplit=1)
        clean = parts[1].strip() if len(parts) > 1 else ""
        return clean, False, True
    return cap, False, False


async def _process_user_turn(
    message: Message,
    content_input: Union[str, Sequence[Union[str, types.Part]]],
    history_text: str,
    force_voice_reply: bool = False,
    force_text_only: bool = False,
    no_quote: bool = False,
    use_quick_prompt: bool = False,
) -> None:
    user_id = message.from_user.id
    chat_id = message.chat.id
    chat_title = message.chat.title or message.chat.full_name or "Личные сообщения"
    await storage.track_user_chat(
        user_id=user_id,
        chat_id=chat_id,
        chat_title=chat_title,
        chat_type=message.chat.type,
    )

    async with user_locks.get(user_id):
        limit_status = await limiter.check(user_id)
        if not limit_status.allowed:
            wait_seconds = int(limit_status.retry_after) + 1
            await message.answer(
                f"⏳ Лимит запросов исчерпан, попробуй снова через {wait_seconds} сек.\n"
                f"({await _limits_line(user_id)})"
            )
            return

        priority = await _get_user_priority(user_id)
        waiting_msg: Optional[Message] = None

        async def notify_waiting(pos: int) -> None:
            nonlocal waiting_msg
            try:
                role_badge = " 👑" if priority == 0 else ""
                waiting_msg = await message.answer(
                    f"⏳ <i>Запрос в очереди сервера (ваша позиция: {pos}{role_badge})...</i>",
                    parse_mode="HTML",
                )
            except Exception:
                pass

        async with global_queue.acquire(user_id, priority=priority, on_waiting=notify_waiting):
            if waiting_msg is not None:
                try:
                    await waiting_msg.delete()
                except Exception:
                    pass

            history_mode = "quick" if use_quick_prompt else "main"
            state = await storage.get(user_id, chat_id=chat_id, mode=history_mode)
            want_audio = False if force_text_only else (state.voice_mode or force_voice_reply)
            action = (
                ChatActionSender.record_voice
                if want_audio
                else ChatActionSender.typing
            )

            effective_prompt = (
                _build_avatar_effective_prompt(state.quick_prompt or settings.quick_prompt)
                if use_quick_prompt
                else state.system_prompt
            )

            async with action(bot=message.bot, chat_id=message.chat.id):
                try:
                    response = await gemini_client.ask(
                        model=state.model,
                        history_turns=state.history,
                        message=content_input,
                        system_prompt=effective_prompt,
                        want_audio=want_audio,
                        voice_name=state.tts_voice,
                    )
                except GeminiError as exc:
                    await message.answer(f"⚠️ {exc}")
                    return
                except Exception:
                    logger.exception("Unexpected error while calling Gemini API")
                    await message.answer("⚠️ Непредвиденная ошибка при обращении к Gemini API.")
                    return

                await limiter.hit(user_id)
                await storage.add_turn(user_id, "user", history_text, chat_id=chat_id, mode=history_mode)
                if response.text:
                    await storage.add_turn(user_id, "model", response.text, chat_id=chat_id, mode=history_mode)
                if response.total_tokens > 0:
                    await storage.record_token_usage(
                        user_id=user_id,
                        chat_id=chat_id,
                        model=state.model,
                        prompt_tokens=response.prompt_tokens,
                        candidates_tokens=response.candidates_tokens,
                        total_tokens=response.total_tokens,
                    )

        await _send_response(
            message=message,
            state=state,
            response=response,
            prompt_text="" if no_quote else history_text,
            want_audio=want_audio,
        )


def _parse_inline_query_intent(raw_query: str, personas: list[dict]) -> tuple[str, Optional[dict], str]:
    """
    Парсит интент инлайн-запроса:
    Возвращает (очищенный_запрос, выбранная_персона, режим: 'avatar' | 'stand' | 'default').
    """
    clean_q = raw_query.strip()

    # Явный префикс Stand
    for prefix in ("/stand ", "stand: ", "/stand: ", "stand "):
        if clean_q.lower().startswith(prefix):
            clean_q = clean_q[len(prefix):].strip()
            return clean_q, None, "stand"

    # Явный префикс Аватара
    for prefix in ("/q ", "q ", "/quick ", "quick ", "/avatar ", "avatar "):
        if clean_q.lower().startswith(prefix):
            clean_q = clean_q[len(prefix):].strip()
            return clean_q, None, "avatar"

    # Проверяем совпадение по имени или id любой из доступных личностей Аватара
    for p in personas:
        p_name = p["name"].lower()
        p_id = str(p["id"]).lower()
        candidates = [
            f"/{p_name} ", f"/{p_id} ",
            f"{p_name}: ", f"{p_id}: ",
            f"{p_name} ",
        ]
        for c in candidates:
            if clean_q.lower().startswith(c):
                clean_q = clean_q[len(c):].strip()
                return clean_q, p, "avatar"

    return clean_q, None, "default"


async def _execute_inline_generation(
    bot: Bot,
    user_id: int,
    session_id: str,
    inline_message_id: str,
    raw_query_override: Optional[str] = None,
    temperature_jitter: bool = False,
) -> None:
    """Генерирует ответ Gemini (текст или анализ картинки по ссылке) и обновляет инлайн-сообщение."""
    session = _inline_sessions.get(session_id)
    if session:
        raw_query = session.query
        is_quick = session.is_quick
        interactive = session.interactive
        persona_prompt = session.persona_prompt
        persona_name = session.persona_name
    else:
        raw_query = raw_query_override or ""
        is_quick = not session_id.startswith("stand_")
        interactive = session_id.startswith("prev_")
        persona_prompt = ""
        persona_name = "Аватар" if is_quick else "Stand"

    try:
        status_text = (
            f"✨ <i>Генерирую сообщение в стиле «{html.escape(persona_name)}»...</i>"
            if is_quick
            else f"💬 <b>Запрос:</b>\n<blockquote>{html.escape(raw_query)}</blockquote>\n\n✨ <i>Gemini генерирует ответ...</i>"
        )
        status_markup = (
            inline_control_keyboard(session_id)
            if interactive
            else InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⏳ Генерация...", callback_data=f"inl_start:{session_id}")]]
            )
        )
        await bot.edit_message_text(
            text=status_text,
            inline_message_id=inline_message_id,
            parse_mode="HTML",
            reply_markup=status_markup,
        )
    except TelegramBadRequest:
        pass

    async with user_locks.get(user_id):
        limit_status = await limiter.check(user_id)
        if not limit_status.allowed:
            wait_seconds = int(limit_status.retry_after) + 1
            err_text = (
                f"⏳ <i>Лимит запросов исчерпан. Попробуйте снова через {wait_seconds} сек.</i>"
                if is_quick
                else f"💬 <b>Запрос:</b>\n<blockquote>{html.escape(raw_query)}</blockquote>\n\n⏳ <i>Лимит запросов исчерпан. Попробуйте снова через {wait_seconds} сек.</i>"
            )
            try:
                await bot.edit_message_text(
                    text=err_text,
                    inline_message_id=inline_message_id,
                    parse_mode="HTML",
                    reply_markup=None,
                )
            except TelegramBadRequest:
                pass
            return

        state = await storage.get(user_id)
        priority = await _get_user_priority(user_id)

        content_input: Union[str, Sequence[Union[str, types.Part]]] = raw_query
        url_match = URL_REGEX.search(raw_query)
        if url_match:
            img_url = url_match.group(0)
            img_data = await try_download_image_from_url(img_url)
            if img_data:
                img_bytes, mime = img_data
                image_part = types.Part.from_bytes(data=img_bytes, mime_type=mime)
                clean_query = raw_query.replace(img_url, "").strip() or "Опиши подробно, что изображено на этой картинке."
                if is_quick:
                    content_input = [
                        image_part,
                        f"USER DRAFT: {clean_query}\n\nFormulate a ready-to-send message in the first person for the chat partner."
                    ]
                else:
                    content_input = [image_part, clean_query]
        elif is_quick:
            regen_hint = "\n4. VARIATION: Formulate a FRESH and distinct alternative variation of the message." if temperature_jitter else ""
            content_input = (
                "USER DRAFT / INTENT TO BE SENT TO CHAT PARTNER:\n"
                "\"\"\"\n"
                f"{raw_query}\n"
                "\"\"\"\n\n"
                "GHOSTWRITER INSTRUCTION:\n"
                "1. Formulate the ready-to-send outgoing message for the chat partner in the first person ('I', 'me') based on the draft.\n"
                "2. Strictly NEVER respond to or acknowledge the user ('Sure, doing it', 'Checking now'). The user is NOT addressing you.\n"
                "3. Output ONLY the raw final message for the interlocutor in the matching conversation language."
                f"{regen_hint}"
            )

        effective_prompt = (
            _build_avatar_effective_prompt(persona_prompt or state.quick_prompt or settings.quick_prompt)
            if is_quick
            else state.system_prompt
        )

        try:
            async with global_queue.acquire(user_id, priority=priority):
                response = await gemini_client.ask(
                    model=state.model,
                    history_turns=[],
                    message=content_input,
                    system_prompt=effective_prompt,
                    want_audio=False,
                )
                await limiter.hit(user_id)
        except GeminiError as exc:
            err_text = (
                f"⚠️ <i>Ошибка Gemini: {html.escape(str(exc))}</i>"
                if is_quick
                else f"💬 <b>Запрос:</b>\n<blockquote>{html.escape(raw_query)}</blockquote>\n\n⚠️ <i>Ошибка Gemini: {html.escape(str(exc))}</i>"
            )
            try:
                await bot.edit_message_text(
                    text=err_text,
                    inline_message_id=inline_message_id,
                    parse_mode="HTML",
                    reply_markup=None,
                )
            except TelegramBadRequest:
                pass
            return
        except Exception:
            logger.exception("Unexpected error in inline generation")
            err_text = (
                "⚠️ <i>Непредвиденная ошибка при генерации ответа.</i>"
                if is_quick
                else f"💬 <b>Запрос:</b>\n<blockquote>{html.escape(raw_query)}</blockquote>\n\n⚠️ <i>Непредвиденная ошибка при генерации ответа.</i>"
            )
            try:
                await bot.edit_message_text(
                    text=err_text,
                    inline_message_id=inline_message_id,
                    parse_mode="HTML",
                    reply_markup=None,
                )
            except TelegramBadRequest:
                pass
            return

        if response.total_tokens > 0:
            await storage.record_token_usage(
                user_id=user_id,
                chat_id=user_id,
                model=state.model,
                prompt_tokens=response.prompt_tokens,
                candidates_tokens=response.candidates_tokens,
                total_tokens=response.total_tokens,
            )

    if is_quick:
        full_text = (response.text or "Готово.").strip()
    else:
        full_text = _format_with_prompt_quote(raw_query, response.text or "Готово.")

    if utf16_len(full_text) > INLINE_ANSWER_LIMIT:
        cut = find_utf16_cut(full_text, INLINE_ANSWER_LIMIT)
        full_text = (
            full_text[:cut] + "…\n\n(ответ обрезан — длинные вопросы лучше задавать в личку боту)"
        )

    markup = inline_control_keyboard(session_id) if interactive else None

    if state.rich_mode and not interactive:
        chunks = markdown_to_chunks(full_text, max_len=INLINE_ANSWER_LIMIT)
        if chunks:
            chunk_text, chunk_entities = chunks[0]
            try:
                await bot.edit_message_text(
                    text=chunk_text,
                    entities=chunk_entities,
                    inline_message_id=inline_message_id,
                    reply_markup=None,
                )
                return
            except TelegramBadRequest:
                pass

    try:
        await bot.edit_message_text(
            text=full_text,
            inline_message_id=inline_message_id,
            parse_mode="HTML",
            reply_markup=markup,
        )
    except TelegramBadRequest:
        pass

