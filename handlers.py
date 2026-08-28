import asyncio
import hashlib
import html
import io
import logging
import re
from typing import Optional, Sequence, Union

import aiohttp
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    ChosenInlineResult,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultCachedVoice,
    InputTextMessageContent,
    Message,
)
from aiogram.utils.chat_action import ChatActionSender
from google.genai import types

from audio import convert_gemini_audio
from config import settings
from formatting import find_utf16_cut, markdown_to_chunks, split_plain_text, utf16_len
from gemini_client import GeminiError, GeminiResponse, build_gemini_client
from keyboards import (
    admin_panel_keyboard,
    admin_users_keyboard,
    clear_history_chats_keyboard,
    limits_keyboard,
    main_menu_keyboard,
    models_keyboard,
    prompt_keyboard,
    settings_keyboard,
    tts_menu_keyboard,
    tts_models_keyboard,
    tts_voices_keyboard,
)
from locks import GlobalQueueManager, UserLocks
from middlewares import AccessMiddleware
from rate_limiter import RateLimiter
from storage import UserState, UserStorage

logger = logging.getLogger(__name__)

URL_REGEX = re.compile(r'https?://[^\s<>"]+')
IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp')


async def try_download_image_from_url(url: str) -> Optional[tuple[bytes, str]]:
    """Пытается скачать изображение по ссылке (до 15 МБ)."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    content_type = resp.headers.get("Content-Type", "").lower()
                    if "image/" in content_type or url.lower().endswith(IMAGE_EXTENSIONS):
                        mime = content_type.split(";")[0] if "image/" in content_type else "image/jpeg"
                        data = await resp.read()
                        if len(data) <= 15 * 1024 * 1024:
                            return data, mime
    except Exception:
        pass
    return None

storage = UserStorage(
    db_path=settings.db_path,
    default_model=settings.default_model,
    max_history=settings.max_history_messages,
    default_tts_model=settings.tts_model,
    default_tts_voice=settings.tts_voice,
)

router = Router()
router.message.outer_middleware(AccessMiddleware(storage=storage))
router.callback_query.outer_middleware(AccessMiddleware(storage=storage))
router.inline_query.outer_middleware(AccessMiddleware(storage=storage))
router.chosen_inline_result.outer_middleware(AccessMiddleware(storage=storage))

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
        f"📝 <b>Системный промпт</b>{status}:\n\n"
        f"<code>{html.escape(effective)}</code>\n\n"
        "• Чтобы задать новый промпт: отправьте команду <code>/prompt текст промпта</code>\n"
        "• Чтобы сбросить к дефолту: нажмите кнопку ниже или <code>/prompt reset</code>"
    )


def _format_with_prompt_quote(prompt: str, response_text: str) -> str:
    """Оформляет исходный запрос цитатой в начале ответа без указания ника."""
    if not prompt:
        return response_text

    # Формируем цитату исходного запроса (Markdown blockquote)
    lines = prompt.strip().split("\n")
    quoted_lines = [f"> {line}" for line in lines]
    quote_block = "\n".join(quoted_lines)

    return f"{quote_block}\n\n{response_text}"


async def _send_response(
    message: Message,
    state: UserState,
    response: GeminiResponse,
    user_id: int,
    prompt_text: str = "",
    want_audio: bool = False,
) -> None:
    """Отправляет ответ пользователю (голосовое сообщение и/или текст с цитатой запроса)."""
    if want_audio and response.audio_bytes:
        try:
            audio_data, audio_filename, _ = convert_gemini_audio(response.audio_bytes)
            voice_file = BufferedInputFile(audio_data, filename=audio_filename)
            await message.answer_voice(voice_file)
        except Exception:
            logger.exception("Не удалось отправить голосовое сообщение, переключаемся на текст")

    if response.text:
        full_text = _format_with_prompt_quote(prompt_text, response.text)
        speak_kb = None
        if not want_audio:
            speak_kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🎙 Озвучить ответ",
                            callback_data="speak_response",
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
        "📈 <b>Расход токенов (Usage Metadata):</b>\n"
        f"• <b>Сегодня:</b> <code>{today_tok}</code> токенов\n"
        f"  └ <i>Вход (промпт + контекст):</i> <code>{today_prompt}</code>\n"
        f"  └ <i>Выход (ответ модели):</i> <code>{today_cand}</code>\n"
        f"• <b>За всё время:</b> <code>{all_tok}</code> токенов ({token_stats['all_requests']} запросов)\n\n"
        "💡 <i>Лимиты работают по скользящему окну (60 сек в минуту, 24 ч в сутки). "
        "При исчерпании Google возвращает 429 с точным таймером ожидания.</i>"
    )



# --- Команды бота ---

@router.message(CommandStart())
@router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    user_id = message.from_user.id
    chat_id = message.chat.id
    chat_title = message.chat.title or message.chat.full_name or "Личные сообщения"
    await storage.track_user_chat(
        user_id=user_id,
        chat_id=chat_id,
        chat_title=chat_title,
        chat_type=message.chat.type,
    )
    state = await storage.get(user_id, chat_id=chat_id)
    is_admin = await storage.is_user_admin(user_id)
    await message.answer(
        _render_main_menu_text(state),
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(is_admin=is_admin),
    )


@router.message(Command("model"))
async def cmd_model(message: Message) -> None:
    state = await storage.get(message.from_user.id, chat_id=message.chat.id)
    await message.answer(
        f"🤖 Выберите основную модель Gemini (текущая: <code>{html.escape(state.model)}</code>):",
        parse_mode="HTML",
        reply_markup=models_keyboard(state.model),
    )


@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    state = await storage.get(message.from_user.id, chat_id=message.chat.id)
    await message.answer(
        "⚙️ <b>Параметры чата и ответов:</b>",
        parse_mode="HTML",
        reply_markup=settings_keyboard(state.rich_mode, state.voice_mode),
    )


@router.message(Command("prompt"))
async def cmd_prompt(message: Message) -> None:
    user_id = message.from_user.id
    state = await storage.get(user_id, chat_id=message.chat.id)
    args = message.text.split(maxsplit=1)

    if len(args) == 1:
        await message.answer(
            _render_prompt_menu_text(state),
            parse_mode="HTML",
            reply_markup=prompt_keyboard(),
        )
        return

    new_prompt = args[1].strip()
    if new_prompt.lower() == "reset":
        await storage.set_system_prompt(user_id, "")
        await message.answer("Системный промпт сброшен к значению по умолчанию ✅")
    else:
        await storage.set_system_prompt(user_id, new_prompt)
        await message.answer("Индивидуальный system prompt сохранён в базе данных ✅")


@router.message(Command("tts"))
async def cmd_tts(message: Message) -> None:
    args = message.text.split(maxsplit=1)
    if len(args) == 1:
        await message.answer(
            "Использование: <code>/tts Текст для озвучки</code>\n\n"
            "Либо настройте параметры озвучки в меню: /menu",
            parse_mode="HTML",
        )
        return

    text_to_speak = args[1].strip()
    user_id = message.from_user.id

    async with user_locks.get(user_id):
        limit_status = await limiter.check(user_id)
        if not limit_status.allowed:
            wait_seconds = int(limit_status.retry_after) + 1
            await message.answer(
                f"⏳ Лимит запросов исчерпан, попробуй снова через {wait_seconds} сек.\n"
                f"({await _limits_line(user_id)})"
            )
            return

        state = await storage.get(user_id, chat_id=message.chat.id)

        async with ChatActionSender.record_voice(bot=message.bot, chat_id=message.chat.id):
            try:
                audio_bytes = await gemini_client.generate_speech(
                    text=text_to_speak,
                    voice_name=state.tts_voice,
                    model=state.tts_model,
                )
            except GeminiError as exc:
                await message.answer(f"⚠️ {exc}")
                return
            except Exception:
                logger.exception("Ошибка при генерации TTS")
                await message.answer("⚠️ Непредвиденная ошибка при генерации речи.")
                return

            await limiter.hit(user_id)

        audio_data, audio_filename, _ = convert_gemini_audio(audio_bytes)
        voice_file = BufferedInputFile(audio_data, filename=audio_filename)
        await message.answer_voice(voice_file)


@router.message(Command("limits"))
async def cmd_limits(message: Message) -> None:
    user_id = message.from_user.id
    text = await _render_limits_menu_text(user_id)
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=limits_keyboard(),
    )


@router.message(Command("voice", "v"))
async def cmd_voice(message: Message) -> None:
    """Принудительный ответ голосом на вопрос или вложение."""
    args = message.text.split(maxsplit=1) if message.text else []
    query_text = args[1].strip() if len(args) > 1 else ""

    # 1. Если был реплай на фото / документ / аудио
    if message.reply_to_message:
        replied = message.reply_to_message
        if replied.photo:
            photo = replied.photo[-1]
            file_io = io.BytesIO()
            await message.bot.download(photo.file_id, destination=file_io)
            image_part = types.Part.from_bytes(data=file_io.getvalue(), mime_type="image/jpeg")
            prompt = query_text or "Опиши подробно голосом, что изображено на этом фото."
            await _process_user_turn(
                message=message,
                content_input=[image_part, prompt],
                history_text=f"[Фото из ответа] {prompt}",
                force_voice_reply=True,
            )
            return

        if replied.document:
            doc = replied.document
            file_io = io.BytesIO()
            await message.bot.download(doc.file_id, destination=file_io)
            doc_part = types.Part.from_bytes(
                data=file_io.getvalue(),
                mime_type=doc.mime_type or "application/octet-stream",
            )
            prompt = query_text or f"Проанализируй документ {doc.file_name or ''} и ответь голосом."
            await _process_user_turn(
                message=message,
                content_input=[doc_part, prompt],
                history_text=f"[Документ из ответа: {doc.file_name or 'файл'}] {prompt}",
                force_voice_reply=True,
            )
            return

        if replied.voice or replied.audio:
            media = replied.voice or replied.audio
            file_io = io.BytesIO()
            await message.bot.download(media.file_id, destination=file_io)
            audio_part = types.Part.from_bytes(
                data=file_io.getvalue(),
                mime_type=media.mime_type or "audio/ogg",
            )
            prompt = query_text or "Ответь голосом на это аудиосообщение."
            await _process_user_turn(
                message=message,
                content_input=[audio_part, prompt],
                history_text=f"[Голосовое сообщение] {prompt}",
                force_voice_reply=True,
            )
            return

    if not query_text:
        await message.answer(
            "Использование: <code>/voice ваш вопрос</code>\n\n"
            "Либо ответьте командой <code>/voice</code> на любое фото, документ или сообщение в чате, чтобы получить ответ голосом.",
            parse_mode="HTML",
        )
        return

    # Проверяем ссылку на изображение
    url_match = URL_REGEX.search(query_text)
    if url_match:
        url = url_match.group(0)
        img_data = await try_download_image_from_url(url)
        if img_data:
            img_bytes, mime = img_data
            image_part = types.Part.from_bytes(data=img_bytes, mime_type=mime)
            clean_text = query_text.replace(url, "").strip() or "Опиши подробно голосом, что на фото."
            await _process_user_turn(
                message=message,
                content_input=[image_part, clean_text],
                history_text=f"[Фото по ссылке] {clean_text}",
                force_voice_reply=True,
            )
            return

    await _process_user_turn(
        message=message,
        content_input=query_text,
        history_text=query_text,
        force_voice_reply=True,
    )


@router.message(Command("text", "t"))
async def cmd_text(message: Message) -> None:
    """Принудительный ответ текстом на вопрос или вложение."""
    args = message.text.split(maxsplit=1) if message.text else []
    query_text = args[1].strip() if len(args) > 1 else ""

    # 1. Если был реплай на фото / документ / аудио
    if message.reply_to_message:
        replied = message.reply_to_message
        if replied.photo:
            photo = replied.photo[-1]
            file_io = io.BytesIO()
            await message.bot.download(photo.file_id, destination=file_io)
            image_part = types.Part.from_bytes(data=file_io.getvalue(), mime_type="image/jpeg")
            prompt = query_text or "Опиши подробно текстом, что изображено на этом фото."
            await _process_user_turn(
                message=message,
                content_input=[image_part, prompt],
                history_text=f"[Фото из ответа] {prompt}",
                force_text_only=True,
            )
            return

        if replied.document:
            doc = replied.document
            file_io = io.BytesIO()
            await message.bot.download(doc.file_id, destination=file_io)
            doc_part = types.Part.from_bytes(
                data=file_io.getvalue(),
                mime_type=doc.mime_type or "application/octet-stream",
            )
            prompt = query_text or f"Проанализируй документ {doc.file_name or ''}."
            await _process_user_turn(
                message=message,
                content_input=[doc_part, prompt],
                history_text=f"[Документ из ответа: {doc.file_name or 'файл'}] {prompt}",
                force_text_only=True,
            )
            return

        if replied.voice or replied.audio:
            media = replied.voice or replied.audio
            file_io = io.BytesIO()
            await message.bot.download(media.file_id, destination=file_io)
            audio_part = types.Part.from_bytes(
                data=file_io.getvalue(),
                mime_type=media.mime_type or "audio/ogg",
            )
            prompt = query_text or "Ответь текстом на аудиосообщение."
            await _process_user_turn(
                message=message,
                content_input=[audio_part, prompt],
                history_text=f"[Голосовое сообщение] {prompt}",
                force_text_only=True,
            )
            return

    if not query_text:
        await message.answer(
            "Использование: <code>/text ваш вопрос</code>\n\n"
            "Либо ответьте командой <code>/text</code> на любое голосовое, фото или документ, чтобы получить ответ строго текстом.",
            parse_mode="HTML",
        )
        return

    # Проверяем ссылку на изображение
    url_match = URL_REGEX.search(query_text)
    if url_match:
        url = url_match.group(0)
        img_data = await try_download_image_from_url(url)
        if img_data:
            img_bytes, mime = img_data
            image_part = types.Part.from_bytes(data=img_bytes, mime_type=mime)
            clean_text = query_text.replace(url, "").strip() or "Опиши подробно, что на фото."
            await _process_user_turn(
                message=message,
                content_input=[image_part, clean_text],
                history_text=f"[Фото по ссылке] {clean_text}",
                force_text_only=True,
            )
            return

    await _process_user_turn(
        message=message,
        content_input=query_text,
        history_text=query_text,
        force_text_only=True,
    )


@router.callback_query(F.data == "speak_response")
async def cb_speak_response(callback: CallbackQuery) -> None:
    """Озвучивает текст сообщения по клику на кнопку под ответом."""
    msg = callback.message
    if not msg or not msg.text:
        await callback.answer("Текст сообщения недоступен", show_alert=True)
        return

    # Очищаем текст от цитаты запроса (blockquote)
    lines = msg.text.split("\n")
    cleaned_lines = [line for line in lines if not line.startswith(">")]
    text_to_speak = "\n".join(cleaned_lines).strip() or msg.text

    if not text_to_speak:
        await callback.answer("Нечего озвучивать", show_alert=True)
        return

    user_id = callback.from_user.id
    state = await storage.get(user_id)

    await callback.answer("Синтезирую голосовое...")

    try:
        audio_bytes = await gemini_client.generate_speech(
            text=text_to_speak[:2000],
            voice_name=state.tts_voice,
            model=state.tts_model,
        )
        audio_data, audio_filename, _ = convert_gemini_audio(audio_bytes)
        voice_file = BufferedInputFile(audio_data, filename=audio_filename)
        await msg.reply_voice(voice_file)
    except Exception as exc:
        logger.exception("Ошибка при озвучке ответа")
        await msg.reply(f"⚠️ Не удалось озвучить: {exc}")


# --- Callback-хендлеры навигации по единому меню ---

@router.callback_query(F.data == "menu:main")
async def cb_menu_main(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id if callback.message else user_id
    state = await storage.get(user_id, chat_id=chat_id)
    is_admin = await storage.is_user_admin(user_id)
    try:
        await callback.message.edit_text(
            _render_main_menu_text(state),
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(is_admin=is_admin),
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data == "menu:model")
async def cb_menu_model(callback: CallbackQuery) -> None:
    chat_id = callback.message.chat.id if callback.message else callback.from_user.id
    state = await storage.get(callback.from_user.id, chat_id=chat_id)
    try:
        await callback.message.edit_text(
            f"🤖 Выберите основную модель Gemini (текущая: <code>{html.escape(state.model)}</code>):",
            parse_mode="HTML",
            reply_markup=models_keyboard(state.model),
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith(("set_model:", "model:")))
async def cb_set_model(callback: CallbackQuery) -> None:
    model = callback.data.split(":", 1)[1]
    if model not in settings.available_models:
        await callback.answer("Неизвестная модель", show_alert=True)
        return

    user_id = callback.from_user.id
    chat_id = callback.message.chat.id if callback.message else user_id
    await storage.set_model(user_id, model)
    await storage.clear_history(user_id, chat_id=chat_id)

    try:
        await callback.message.edit_text(
            f"🤖 Выберите основную модель Gemini (текущая: <code>{html.escape(model)}</code>):",
            parse_mode="HTML",
            reply_markup=models_keyboard(model),
        )
    except TelegramBadRequest:
        pass
    await callback.answer(f"Модель переключена на {model} (контекст чата очищен)")



@router.callback_query(F.data == "menu:tts")
async def cb_menu_tts(callback: CallbackQuery) -> None:
    state = await storage.get(callback.from_user.id)
    try:
        await callback.message.edit_text(
            _render_tts_menu_text(state),
            parse_mode="HTML",
            reply_markup=tts_menu_keyboard(state.tts_model, state.tts_voice, state.voice_mode),
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data == "menu:tts_models")
async def cb_menu_tts_models(callback: CallbackQuery) -> None:
    state = await storage.get(callback.from_user.id)
    try:
        await callback.message.edit_text(
            f"🎙 Выберите модель для синтеза речи TTS (текущая: <code>{html.escape(state.tts_model)}</code>):",
            parse_mode="HTML",
            reply_markup=tts_models_keyboard(state.tts_model),
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("set_tts_model:"))
async def cb_set_tts_model(callback: CallbackQuery) -> None:
    model = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    await storage.set_tts_model(user_id, model)

    try:
        await callback.message.edit_text(
            f"🎙 Выберите модель для синтеза речи TTS (текущая: <code>{html.escape(model)}</code>):",
            parse_mode="HTML",
            reply_markup=tts_models_keyboard(model),
        )
    except TelegramBadRequest:
        pass
    await callback.answer(f"TTS модель: {model}")


@router.callback_query(F.data == "menu:tts_voices")
async def cb_menu_tts_voices(callback: CallbackQuery) -> None:
    state = await storage.get(callback.from_user.id)
    try:
        await callback.message.edit_text(
            f"🗣 Выберите голос для озвучки (текущий: <code>{html.escape(state.tts_voice)}</code>):",
            parse_mode="HTML",
            reply_markup=tts_voices_keyboard(state.tts_voice),
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("set_tts_voice:"))
async def cb_set_tts_voice(callback: CallbackQuery) -> None:
    voice = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    await storage.set_tts_voice(user_id, voice)

    try:
        await callback.message.edit_text(
            f"🗣 Выберите голос для озвучки (текущий: <code>{html.escape(voice)}</code>):",
            parse_mode="HTML",
            reply_markup=tts_voices_keyboard(voice),
        )
    except TelegramBadRequest:
        pass
    await callback.answer(f"Голос озвучки: {voice}")


@router.callback_query(F.data == "toggle_voice_tts")
async def cb_toggle_voice_tts(callback: CallbackQuery) -> None:
    voice = await storage.toggle_voice(callback.from_user.id)
    state = await storage.get(callback.from_user.id)
    try:
        await callback.message.edit_text(
            _render_tts_menu_text(state),
            parse_mode="HTML",
            reply_markup=tts_menu_keyboard(state.tts_model, state.tts_voice, voice),
        )
    except TelegramBadRequest:
        pass
    await callback.answer("Голосовые ответы: " + ("включены" if voice else "выключены"))


@router.callback_query(F.data == "menu:settings")
async def cb_menu_settings(callback: CallbackQuery) -> None:
    chat_id = callback.message.chat.id if callback.message else callback.from_user.id
    state = await storage.get(callback.from_user.id, chat_id=chat_id)
    try:
        await callback.message.edit_text(
            "⚙️ <b>Параметры чата и ответов:</b>",
            parse_mode="HTML",
            reply_markup=settings_keyboard(state.rich_mode, state.voice_mode),
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data == "toggle_rich")
async def cb_toggle_rich(callback: CallbackQuery) -> None:
    chat_id = callback.message.chat.id if callback.message else callback.from_user.id
    rich = await storage.toggle_rich(callback.from_user.id)
    state = await storage.get(callback.from_user.id, chat_id=chat_id)
    try:
        await callback.message.edit_text(
            "⚙️ <b>Параметры чата и ответов:</b>",
            parse_mode="HTML",
            reply_markup=settings_keyboard(rich, state.voice_mode),
        )
    except TelegramBadRequest:
        pass
    await callback.answer("Rich-режим: " + ("включен" if rich else "выключен"))


@router.callback_query(F.data == "toggle_voice")
async def cb_toggle_voice(callback: CallbackQuery) -> None:
    chat_id = callback.message.chat.id if callback.message else callback.from_user.id
    voice = await storage.toggle_voice(callback.from_user.id)
    state = await storage.get(callback.from_user.id, chat_id=chat_id)
    try:
        await callback.message.edit_text(
            "⚙️ <b>Параметры чата и ответов:</b>",
            parse_mode="HTML",
            reply_markup=settings_keyboard(state.rich_mode, voice),
        )
    except TelegramBadRequest:
        pass
    await callback.answer("Голосовые ответы: " + ("включены" if voice else "выключены"))


@router.message(Command("clear"))
async def cmd_clear(message: Message) -> None:
    user_id = message.from_user.id
    chats = await storage.get_user_chats_with_history(user_id)
    if not chats:
        await message.answer("ℹ️ У вас нет сохранённой истории диалогов ни в одном чате.")
        return
    await message.answer(
        "🗑 <b>Выберите чат для очистки истории диалога:</b>",
        parse_mode="HTML",
        reply_markup=clear_history_chats_keyboard(chats),
    )


@router.callback_query(F.data.in_({"clear_history", "menu:clear_hist"}))
async def cb_clear_history(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    chats = await storage.get_user_chats_with_history(user_id)
    if not chats:
        await callback.answer("У вас нет сохранённой истории ни в одном чате.", show_alert=True)
        return
    try:
        await callback.message.edit_text(
            "🗑 <b>Выберите чат для очистки истории диалога:</b>",
            parse_mode="HTML",
            reply_markup=clear_history_chats_keyboard(chats),
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("clear_chat:"))
async def cb_clear_chat(callback: CallbackQuery) -> None:
    target = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    if target == "all":
        deleted = await storage.clear_history(user_id, all_chats=True)
        await callback.answer(f"История во всех чатах удалена ({deleted} реплик) 💥", show_alert=True)
    else:
        try:
            target_chat_id = int(target)
            deleted = await storage.clear_history(user_id, chat_id=target_chat_id)
            await callback.answer(f"История выбранного чата удалена ({deleted} реплик) ✅", show_alert=True)
        except ValueError:
            await callback.answer("Ошибка определения чата", show_alert=True)
            return

    # Обновляем список чатов
    chats = await storage.get_user_chats_with_history(user_id)
    if chats:
        try:
            await callback.message.edit_text(
                "🗑 <b>Выберите чат для очистки истории диалога:</b>",
                parse_mode="HTML",
                reply_markup=clear_history_chats_keyboard(chats),
            )
        except TelegramBadRequest:
            pass
    else:
        chat_id = callback.message.chat.id if callback.message else user_id
        state = await storage.get(user_id, chat_id=chat_id)
        try:
            await callback.message.edit_text(
                "⚙️ <b>Параметры чата и ответов:</b>\n\n✅ <i>Вся история диалогов очищена.</i>",
                parse_mode="HTML",
                reply_markup=settings_keyboard(state.rich_mode, state.voice_mode),
            )
        except TelegramBadRequest:
            pass


@router.callback_query(F.data == "menu:prompt")
async def cb_menu_prompt(callback: CallbackQuery) -> None:
    chat_id = callback.message.chat.id if callback.message else callback.from_user.id
    state = await storage.get(callback.from_user.id, chat_id=chat_id)
    try:
        await callback.message.edit_text(
            _render_prompt_menu_text(state),
            parse_mode="HTML",
            reply_markup=prompt_keyboard(),
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data == "reset_prompt")
async def cb_reset_prompt(callback: CallbackQuery) -> None:
    chat_id = callback.message.chat.id if callback.message else callback.from_user.id
    await storage.set_system_prompt(callback.from_user.id, "")
    state = await storage.get(callback.from_user.id, chat_id=chat_id)
    try:
        await callback.message.edit_text(
            _render_prompt_menu_text(state),
            parse_mode="HTML",
            reply_markup=prompt_keyboard(),
        )
    except TelegramBadRequest:
        pass
    await callback.answer("Промпт сброшен к дефолтному ✅")


@router.callback_query(F.data.in_({"menu:limits", "refresh_limits"}))
async def cb_menu_limits(callback: CallbackQuery) -> None:
    text = await _render_limits_menu_text(callback.from_user.id)
    try:
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=limits_keyboard(),
        )
    except TelegramBadRequest:
        pass
    await callback.answer("Лимиты обновлены")


# --- Панель администратора ---

@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    user_id = message.from_user.id
    if not await storage.is_user_admin(user_id):
        await message.answer("⛔️ У вас нет прав администратора.")
        return
    whitelist_mode = await storage.get_whitelist_mode()
    await message.answer(
        "👑 <b>Панель управления администратора:</b>\n\n"
        f"• <b>Режим белого списка:</b> {'ВКЛ ✅ (доступ только разрешённым)' if whitelist_mode else 'ВЫКЛ ❌ (доступен всем)'}\n\n"
        "<i>Используйте кнопки ниже для управления пользователями и доступом:</i>",
        parse_mode="HTML",
        reply_markup=admin_panel_keyboard(whitelist_mode),
    )


@router.callback_query(F.data == "menu:admin")
async def cb_menu_admin(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    if not await storage.is_user_admin(user_id):
        await callback.answer("⛔️ У вас нет прав администратора.", show_alert=True)
        return
    whitelist_mode = await storage.get_whitelist_mode()
    try:
        await callback.message.edit_text(
            "👑 <b>Панель управления администратора:</b>\n\n"
            f"• <b>Режим белого списка:</b> {'ВКЛ ✅ (доступ только разрешённым)' if whitelist_mode else 'ВЫКЛ ❌ (доступен всем)'}\n\n"
            "<i>Используйте кнопки ниже для управления пользователями и доступом:</i>",
            parse_mode="HTML",
            reply_markup=admin_panel_keyboard(whitelist_mode),
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data == "admin:toggle_whitelist")
async def cb_admin_toggle_whitelist(callback: CallbackQuery) -> None:
    if not await storage.is_user_admin(callback.from_user.id):
        await callback.answer("⛔️ Доступ запрещён", show_alert=True)
        return
    new_state = await storage.toggle_whitelist_mode()
    try:
        await callback.message.edit_text(
            "👑 <b>Панель управления администратора:</b>\n\n"
            f"• <b>Режим белого списка:</b> {'ВКЛ ✅ (доступ только разрешённым)' if new_state else 'ВЫКЛ ❌ (доступен всем)'}\n\n"
            "<i>Используйте кнопки ниже для управления пользователями и доступом:</i>",
            parse_mode="HTML",
            reply_markup=admin_panel_keyboard(new_state),
        )
    except TelegramBadRequest:
        pass
    await callback.answer(f"Белый список: {'ВКЛ ✅' if new_state else 'ВЫКЛ ❌'}")


@router.callback_query(F.data == "admin:users")
async def cb_admin_users(callback: CallbackQuery) -> None:
    if not await storage.is_user_admin(callback.from_user.id):
        await callback.answer("⛔️ Доступ запрещён", show_alert=True)
        return
    users = await storage.list_allowed_users()
    count = len(users)
    text = f"👥 <b>Разрешённые пользователи ({count}):</b>\n\n"
    if not users:
        text += "<i>Список пуст. Вы можете добавить пользователей командой <code>/adduser &lt;id&gt;</code>.</i>"
    else:
        text += "<i>Нажмите на ❌ напротив пользователя, чтобы удалить доступ:</i>"
    try:
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=admin_users_keyboard(users),
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("admin:del_user:"))
async def cb_admin_del_user(callback: CallbackQuery) -> None:
    if not await storage.is_user_admin(callback.from_user.id):
        await callback.answer("⛔️ Доступ запрещён", show_alert=True)
        return
    target_id_str = callback.data.split(":", 2)[2]
    try:
        target_id = int(target_id_str)
        if target_id in settings.admin_ids and target_id == callback.from_user.id:
            await callback.answer("Нельзя удалить самого себя из супер-админов!", show_alert=True)
            return
        await storage.remove_allowed_user(target_id)
        await callback.answer(f"Пользователь {target_id} удалён ✅")
    except ValueError:
        await callback.answer("Неверный ID", show_alert=True)
        return

    users = await storage.list_allowed_users()
    try:
        await callback.message.edit_text(
            f"👥 <b>Разрешённые пользователи ({len(users)}):</b>\n\n"
            "<i>Нажмите на ❌ напротив пользователя, чтобы удалить доступ:</i>",
            parse_mode="HTML",
            reply_markup=admin_users_keyboard(users),
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "admin:add_user_hint")
async def cb_admin_add_user_hint(callback: CallbackQuery) -> None:
    if not await storage.is_user_admin(callback.from_user.id):
        await callback.answer("⛔️ Доступ запрещён", show_alert=True)
        return
    await callback.message.answer(
        "➕ <b>Добавление пользователя:</b>\n\n"
        "Отправьте команду:\n"
        "• <code>/adduser 123456789 username</code> — добавить обычного пользователя\n"
        "• <code>/addadmin 123456789 username</code> — добавить администратора\n\n"
        "<i>User ID можно узнать, переслав сообщение пользователя в бот @userinfobot.</i>",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(Command("adduser", "addadmin"))
async def cmd_adduser(message: Message) -> None:
    if not await storage.is_user_admin(message.from_user.id):
        await message.answer("⛔️ У вас нет прав администратора.")
        return
    args = message.text.split() if message.text else []
    if len(args) < 2:
        await message.answer(
            "Использование: <code>/adduser &lt;user_id&gt; [username]</code> или <code>/addadmin &lt;user_id&gt;</code>",
            parse_mode="HTML",
        )
        return
    try:
        target_uid = int(args[1])
    except ValueError:
        await message.answer("⚠️ ID пользователя должен быть числом.")
        return
    username = args[2] if len(args) > 2 else ""
    is_admin = args[0].lower().startswith("/addadmin")
    await storage.add_allowed_user(
        user_id=target_uid,
        username=username,
        is_admin=is_admin,
        added_by=message.from_user.id,
    )
    role_text = "Администратор" if is_admin else "Пользователь"
    await message.answer(
        f"✅ {role_text} <code>{target_uid}</code> ({username or 'без ника'}) добавлен в белый список!",
        parse_mode="HTML",
    )


@router.message(Command("deluser"))
async def cmd_deluser(message: Message) -> None:
    if not await storage.is_user_admin(message.from_user.id):
        await message.answer("⛔️ У вас нет прав администратора.")
        return
    args = message.text.split() if message.text else []
    if len(args) < 2:
        await message.answer("Использование: <code>/deluser &lt;user_id&gt;</code>", parse_mode="HTML")
        return
    try:
        target_uid = int(args[1])
    except ValueError:
        await message.answer("⚠️ ID пользователя должен быть числом.")
        return
    if target_uid in settings.admin_ids and target_uid == message.from_user.id:
        await message.answer("⚠️ Нельзя удалить самого себя из супер-админов.")
        return
    removed = await storage.remove_allowed_user(target_uid)
    if removed:
        await message.answer(f"✅ Пользователь <code>{target_uid}</code> удалён из белого списка.", parse_mode="HTML")
    else:
        await message.answer(f"ℹ️ Пользователь <code>{target_uid}</code> не был найден в базе.", parse_mode="HTML")


@router.message(Command("users"))
async def cmd_users(message: Message) -> None:
    if not await storage.is_user_admin(message.from_user.id):
        await message.answer("⛔️ У вас нет прав администратора.")
        return
    users = await storage.list_allowed_users()
    if not users:
        await message.answer("👥 В базе данных пока нет добавленных пользователей.")
        return
    await message.answer(
        f"👥 <b>Разрешённые пользователи ({len(users)}):</b>",
        parse_mode="HTML",
        reply_markup=admin_users_keyboard(users),
    )



# --- Общий обработчик запросов (текст / фото / документы / голосовые) ---

def _parse_caption_voice_flags(caption: Optional[str]) -> tuple[str, bool, bool]:
    """Проверяет команды /voice или /text в подписи к файлу. Возвращает (clean_prompt, force_voice, force_text)."""
    if not caption:
        return "", False, False
    cap = caption.strip()
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

        waiting_msg: Optional[Message] = None

        async def notify_waiting(pos: int) -> None:
            nonlocal waiting_msg
            try:
                waiting_msg = await message.answer(
                    f"⏳ <i>Запрос в очереди сервера (ваша позиция: {pos})...</i>",
                    parse_mode="HTML",
                )
            except Exception:
                pass

        async with global_queue.acquire(user_id, on_waiting=notify_waiting):
            if waiting_msg is not None:
                try:
                    await waiting_msg.delete()
                except Exception:
                    pass

            state = await storage.get(user_id, chat_id=chat_id)
            want_audio = False if force_text_only else (state.voice_mode or force_voice_reply)
            action = (
                ChatActionSender.record_voice
                if want_audio
                else ChatActionSender.typing
            )

            async with action(bot=message.bot, chat_id=message.chat.id):
                try:
                    response = await gemini_client.ask(
                        model=state.model,
                        history_turns=state.history,
                        message=content_input,
                        system_prompt=state.system_prompt,
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
                await storage.add_turn(user_id, "user", history_text, chat_id=chat_id)
                if response.text:
                    await storage.add_turn(user_id, "model", response.text, chat_id=chat_id)
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
            user_id=user_id,
            prompt_text=history_text,
            want_audio=want_audio,
        )


@router.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message) -> None:
    text = message.text

    # 1. Если пользователь ответил текстом на сообщение с фото или документом-картинкой
    if message.reply_to_message:
        replied = message.reply_to_message
        if replied.photo:
            photo = replied.photo[-1]
            file_io = io.BytesIO()
            await message.bot.download(photo.file_id, destination=file_io)
            image_bytes = file_io.getvalue()
            image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
            content_input = [image_part, text]
            history_text = f"[Фото из ответа] {text}"
            await _process_user_turn(
                message=message,
                content_input=content_input,
                history_text=history_text,
                force_voice_reply=False,
            )
            return

        if replied.document and (replied.document.mime_type or "").startswith("image/"):
            doc = replied.document
            file_io = io.BytesIO()
            await message.bot.download(doc.file_id, destination=file_io)
            doc_bytes = file_io.getvalue()
            doc_part = types.Part.from_bytes(data=doc_bytes, mime_type=doc.mime_type or "image/jpeg")
            content_input = [doc_part, text]
            history_text = f"[Изображение-документ из ответа] {text}"
            await _process_user_turn(
                message=message,
                content_input=content_input,
                history_text=history_text,
                force_voice_reply=False,
            )
            return

    # 2. Если в тексте есть прямая ссылка на изображение
    url_match = URL_REGEX.search(text)
    if url_match:
        url = url_match.group(0)
        img_data = await try_download_image_from_url(url)
        if img_data:
            img_bytes, mime = img_data
            image_part = types.Part.from_bytes(data=img_bytes, mime_type=mime)
            clean_text = text.replace(url, "").strip() or "Опиши подробно, что изображено на этом фото."
            content_input = [image_part, clean_text]
            history_text = f"[Фото по ссылке] {clean_text}"
            await _process_user_turn(
                message=message,
                content_input=content_input,
                history_text=history_text,
                force_voice_reply=False,
            )
            return

    # 3. Обычный текстовый запрос
    await _process_user_turn(
        message=message,
        content_input=message.text,
        history_text=message.text,
        force_voice_reply=False,
    )


@router.message(F.photo)
async def handle_photo(message: Message) -> None:
    photo = message.photo[-1]
    clean_cap, force_voice, force_text = _parse_caption_voice_flags(message.caption)
    prompt_text = clean_cap or "Опиши подробно, что изображено на этом фото."

    file_io = io.BytesIO()
    await message.bot.download(photo.file_id, destination=file_io)
    image_bytes = file_io.getvalue()

    image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
    content_input = [image_part, prompt_text]
    history_text = f"[Фото] {prompt_text}"

    await _process_user_turn(
        message=message,
        content_input=content_input,
        history_text=history_text,
        force_voice_reply=force_voice,
        force_text_only=force_text,
    )


@router.message(F.voice | F.audio)
async def handle_voice(message: Message) -> None:
    media = message.voice or message.audio
    clean_cap, force_voice, force_text = _parse_caption_voice_flags(message.caption)
    mime_type = media.mime_type or ("audio/ogg" if message.voice else "audio/mp3")
    prompt_text = clean_cap or "Ответь на аудиосообщение."

    file_io = io.BytesIO()
    await message.bot.download(media.file_id, destination=file_io)
    audio_bytes = file_io.getvalue()

    audio_part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)
    content_input = [audio_part, prompt_text]
    history_text = f"[Голосовое сообщение] {prompt_text}"

    # По умолчанию для голосовых отвечаем голосом, если явно не запрошен только текст
    await _process_user_turn(
        message=message,
        content_input=content_input,
        history_text=history_text,
        force_voice_reply=True if not force_text else False,
        force_text_only=force_text,
    )


@router.message(F.document)
async def handle_document(message: Message) -> None:
    doc = message.document
    clean_cap, force_voice, force_text = _parse_caption_voice_flags(message.caption)
    mime_type = doc.mime_type or "application/octet-stream"
    prompt_text = clean_cap or f"Проанализируй документ {doc.file_name or ''}."

    file_io = io.BytesIO()
    await message.bot.download(doc.file_id, destination=file_io)
    doc_bytes = file_io.getvalue()

    doc_part = types.Part.from_bytes(data=doc_bytes, mime_type=mime_type)
    content_input = [doc_part, prompt_text]
    history_text = f"[Документ: {doc.file_name or 'файл'}] {prompt_text}"

    await _process_user_turn(
        message=message,
        content_input=content_input,
        history_text=history_text,
        force_voice_reply=force_voice,
        force_text_only=force_text,
    )


# --- Inline mode (без сжигания квоты при наборе текста) ---

INLINE_ANSWER_LIMIT = 3900

# Временный кэш промптов (result_id -> raw_query) для inline-генерации
_pending_inline_prompts: dict[str, str] = {}
# Кэш voice_file_id для отправки реальных ГС прямо в чат через InlineQueryResultCachedVoice
_tts_voice_cache: dict[str, str] = {}


async def _execute_inline_tts_generation(
    bot: Bot,
    user_id: int,
    tts_text: str,
    inline_message_id: str,
) -> None:
    """Генерирует озвучку текста и отправляет голосовое сообщение в ЛС пользователю."""
    async with user_locks.get(user_id):
        limit_status = await limiter.check(user_id)
        if not limit_status.allowed:
            wait_seconds = int(limit_status.retry_after) + 1
            try:
                await bot.edit_message_text(
                    text=(
                        f"🎙 <b>Озвучка текста (TTS):</b>\n<blockquote>{html.escape(tts_text)}</blockquote>\n\n"
                        f"⏳ <i>Лимит запросов исчерпан. Попробуйте снова через {wait_seconds} сек.</i>"
                    ),
                    inline_message_id=inline_message_id,
                    parse_mode="HTML",
                    reply_markup=None,
                )
            except TelegramBadRequest:
                pass
            return

        state = await storage.get(user_id)

        try:
            audio_bytes = await gemini_client.generate_speech(
                text=tts_text,
                voice_name=state.tts_voice,
                model=state.tts_model,
            )
        except GeminiError as exc:
            try:
                await bot.edit_message_text(
                    text=(
                        f"🎙 <b>Озвучка текста (TTS):</b>\n<blockquote>{html.escape(tts_text)}</blockquote>\n\n"
                        f"⚠️ <i>Ошибка генерации речи: {html.escape(str(exc))}</i>"
                    ),
                    inline_message_id=inline_message_id,
                    parse_mode="HTML",
                    reply_markup=None,
                )
            except TelegramBadRequest:
                pass
            return
        except Exception:
            logger.exception("Unexpected error in inline TTS generation")
            try:
                await bot.edit_message_text(
                    text=(
                        f"🎙 <b>Озвучка текста (TTS):</b>\n<blockquote>{html.escape(tts_text)}</blockquote>\n\n"
                        "⚠️ <i>Непредвиденная ошибка при генерации речи.</i>"
                    ),
                    inline_message_id=inline_message_id,
                    parse_mode="HTML",
                    reply_markup=None,
                )
            except TelegramBadRequest:
                pass
            return

        await limiter.hit(user_id)

    audio_data, audio_filename, _ = convert_gemini_audio(audio_bytes)
    bot_info = await bot.get_me()
    bot_username = bot_info.username or ""

    pm_status = "✅ <i>Голосовое сообщение сгенерировано и отправлено в личку с ботом!</i>"
    try:
        voice_file = BufferedInputFile(audio_data, filename=audio_filename)
        await bot.send_voice(
            chat_id=user_id,
            voice=voice_file,
            caption=f"🎙 Озвучка из инлайн-режима:\n{tts_text[:200]}",
        )
    except Exception:
        pm_status = (
            "✅ <i>Озвучка готова! Чтобы бот мог присылать аудиофайлы в личку, "
            "откройте диалог с ботом по кнопке ниже.</i>"
        )

    keyboard = None
    if bot_username:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🎧 Открыть голосовое в боте",
                        url=f"https://t.me/{bot_username}",
                    )
                ]
            ]
        )

    try:
        await bot.edit_message_text(
            text=(
                f"🎙 <b>Озвучка текста (TTS):</b>\n"
                f"<blockquote>{html.escape(tts_text)}</blockquote>\n\n"
                f"{pm_status}"
            ),
            inline_message_id=inline_message_id,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    except TelegramBadRequest:
        pass


async def _execute_inline_generation(
    bot: Bot,
    user_id: int,
    raw_query: str,
    inline_message_id: str,
) -> None:
    """Генерирует ответ Gemini (текст или анализ картинки по ссылке) и редактирует инлайн-сообщение."""
    async with user_locks.get(user_id):
        limit_status = await limiter.check(user_id)
        if not limit_status.allowed:
            wait_seconds = int(limit_status.retry_after) + 1
            try:
                await bot.edit_message_text(
                    text=(
                        f"💬 <b>Запрос:</b>\n<blockquote>{html.escape(raw_query)}</blockquote>\n\n"
                        f"⏳ <i>Лимит запросов исчерпан. Попробуйте снова через {wait_seconds} сек.</i>"
                    ),
                    inline_message_id=inline_message_id,
                    parse_mode="HTML",
                    reply_markup=None,
                )
            except TelegramBadRequest:
                pass
            return

        state = await storage.get(user_id)

        # Проверяем наличие ссылки на изображение
        content_input: Union[str, Sequence[Union[str, types.Part]]] = raw_query
        url_match = URL_REGEX.search(raw_query)
        if url_match:
            img_url = url_match.group(0)
            img_data = await try_download_image_from_url(img_url)
            if img_data:
                img_bytes, mime = img_data
                image_part = types.Part.from_bytes(data=img_bytes, mime_type=mime)
                clean_query = raw_query.replace(img_url, "").strip() or "Опиши подробно, что изображено на этой картинке."
                content_input = [image_part, clean_query]

        try:
            # Inline-запросы выполняются как изолированные разовые обращения
            response = await gemini_client.ask(
                model=state.model,
                history_turns=[],
                message=content_input,
                system_prompt=state.system_prompt,
                want_audio=False,
            )
        except GeminiError as exc:
            try:
                await bot.edit_message_text(
                    text=(
                        f"💬 <b>Запрос:</b>\n<blockquote>{html.escape(raw_query)}</blockquote>\n\n"
                        f"⚠️ <i>Ошибка Gemini: {html.escape(str(exc))}</i>"
                    ),
                    inline_message_id=inline_message_id,
                    parse_mode="HTML",
                    reply_markup=None,
                )
            except TelegramBadRequest:
                pass
            return
        except Exception:
            logger.exception("Unexpected error in inline generation")
            try:
                await bot.edit_message_text(
                    text=(
                        f"💬 <b>Запрос:</b>\n<blockquote>{html.escape(raw_query)}</blockquote>\n\n"
                        "⚠️ <i>Непредвиденная ошибка при генерации ответа.</i>"
                    ),
                    inline_message_id=inline_message_id,
                    parse_mode="HTML",
                    reply_markup=None,
                )
            except TelegramBadRequest:
                pass
            return

        await limiter.hit(user_id)
        if response.total_tokens > 0:
            await storage.record_token_usage(
                user_id=user_id,
                chat_id=user_id,
                model=state.model,
                prompt_tokens=response.prompt_tokens,
                candidates_tokens=response.candidates_tokens,
                total_tokens=response.total_tokens,
            )

    # Оформляем цитату исходного запроса в начале сообщения без указания ника
    full_text = _format_with_prompt_quote(raw_query, response.text or "Готово.")

    if utf16_len(full_text) > INLINE_ANSWER_LIMIT:
        cut = find_utf16_cut(full_text, INLINE_ANSWER_LIMIT)
        full_text = (
            full_text[:cut] + "…\n\n(ответ обрезан — длинные вопросы лучше задавать в личку боту)"
        )

    if state.rich_mode:
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
            reply_markup=None,
        )
    except TelegramBadRequest:
        pass


@router.inline_query()
async def handle_inline(query: InlineQuery) -> None:
    raw_query = query.query.strip()
    if not raw_query:
        help_article = InlineQueryResultArticle(
            id="inline_help",
            title="💬 Задайте вопрос Gemini...",
            description="Наберите: @bot_username вопрос (или /tts текст, или ссылка на картинку)",
            input_message_content=InputTextMessageContent(
                message_text=(
                    "Чтобы обратиться к Gemini в любом чате, наберите:\n"
                    "• <code>@bot_username ваш вопрос</code>\n"
                    "• <code>@bot_username /tts текст для озвучки</code>\n"
                    "• <code>@bot_username https://ссылка_на_картинку вопрос</code>"
                ),
                parse_mode="HTML",
            ),
        )
        await query.answer(
            results=[help_article],
            cache_time=0,
            is_personal=True,
        )
        return

    result_id = hashlib.sha256(raw_query.encode("utf-8")).hexdigest()[:24]
    _pending_inline_prompts[result_id] = raw_query

    if len(_pending_inline_prompts) > 1000:
        for k in list(_pending_inline_prompts.keys())[:200]:
            _pending_inline_prompts.pop(k, None)

    # Режим 1: Озвучка текста (/tts или tts) -> генерация только после отправки в чат
    if raw_query.lower().startswith(("/tts", "tts")):
        parts = raw_query.split(maxsplit=1)
        tts_text = parts[1].strip() if len(parts) > 1 else ""
        if not tts_text:
            article = InlineQueryResultArticle(
                id="tts_hint",
                title="🎙 Озвучить текст (TTS)",
                description="Наберите текст: @bot_username /tts Текст для озвучки",
                input_message_content=InputTextMessageContent(
                    message_text="Использование TTS: <code>@bot_username /tts Текст для озвучки</code>",
                    parse_mode="HTML",
                ),
            )
            await query.answer(results=[article], cache_time=0, is_personal=True)
            return

        prompt_short = tts_text[:70] + ("…" if len(tts_text) > 70 else "")
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🎙 Синтезирую голосовое...",
                        callback_data=f"inline_tts:{result_id}",
                    )
                ]
            ]
        )
        article = InlineQueryResultArticle(
            id=result_id,
            title="🎙 Озвучить текст (TTS)",
            description=f"«{prompt_short}» (нажмите для отправки и озвучки)",
            reply_markup=keyboard,
            input_message_content=InputTextMessageContent(
                message_text=(
                    f"🎙 <b>Озвучка текста (TTS):</b>\n<blockquote>{html.escape(tts_text)}</blockquote>\n\n"
                    "⏳ <i>Синтезирую голосовое сообщение...</i>"
                ),
                parse_mode="HTML",
            ),
        )
        await query.answer(results=[article], cache_time=0, is_personal=True)
        return

    # Режим 2: Картинка по ссылке
    is_image = bool(URL_REGEX.search(raw_query))
    title = "🖼 Анализ картинки по ссылке" if is_image else "💬 Отправить запрос к Gemini"
    prompt_short = raw_query[:80] + ("…" if len(raw_query) > 80 else "")

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⏳ Анализирую..." if is_image else "⏳ Генерирую ответ...",
                    callback_data=f"inline_gen:{result_id}",
                )
            ]
        ]
    )

    article = InlineQueryResultArticle(
        id=result_id,
        title=title,
        description=f"«{prompt_short}» (нажмите для отправки)",
        reply_markup=keyboard,
        input_message_content=InputTextMessageContent(
            message_text=(
                f"💬 <b>Запрос:</b>\n<blockquote>{html.escape(raw_query)}</blockquote>\n\n"
                "⏳ <i>Генерирую ответ от Gemini...</i>"
            ),
            parse_mode="HTML",
        ),
    )

    await query.answer(
        results=[article],
        cache_time=0,
        is_personal=True,
    )


@router.chosen_inline_result()
async def handle_chosen_inline_result(chosen: ChosenInlineResult) -> None:
    """Срабатывает ровно в момент, когда пользователь кликнул карточку и отправил сообщение."""
    if not chosen.inline_message_id:
        return

    raw_query = chosen.query.strip() or _pending_inline_prompts.get(chosen.result_id, "")
    if not raw_query:
        return

    if raw_query.lower().startswith(("/tts", "tts")):
        parts = raw_query.split(maxsplit=1)
        tts_text = parts[1].strip() if len(parts) > 1 else ""
        if tts_text:
            asyncio.create_task(
                _execute_inline_tts_generation(
                    bot=chosen.bot,
                    user_id=chosen.from_user.id,
                    tts_text=tts_text,
                    inline_message_id=chosen.inline_message_id,
                )
            )
            return

    asyncio.create_task(
        _execute_inline_generation(
            bot=chosen.bot,
            user_id=chosen.from_user.id,
            raw_query=raw_query,
            inline_message_id=chosen.inline_message_id,
        )
    )


@router.callback_query(F.data.startswith("inline_gen:"))
async def cb_inline_gen(callback: CallbackQuery) -> None:
    """Fallback-хендлер для текстовых/визуальных запросов."""
    if not callback.inline_message_id:
        await callback.answer("Ошибка: сообщение устарело", show_alert=True)
        return

    result_id = callback.data.split(":", 1)[1]
    raw_query = _pending_inline_prompts.get(result_id, "")
    if not raw_query:
        await callback.answer("Генерирую...")
        return

    await callback.answer("Генерация запущена...")
    asyncio.create_task(
        _execute_inline_generation(
            bot=callback.bot,
            user_id=callback.from_user.id,
            raw_query=raw_query,
            inline_message_id=callback.inline_message_id,
        )
    )


@router.callback_query(F.data.startswith("inline_tts:"))
async def cb_inline_tts(callback: CallbackQuery) -> None:
    """Fallback-хендлер для голосовой озвучки (TTS)."""
    if not callback.inline_message_id:
        await callback.answer("Ошибка: сообщение устарело", show_alert=True)
        return

    result_id = callback.data.split(":", 1)[1]
    raw_query = _pending_inline_prompts.get(result_id, "")
    if not raw_query:
        await callback.answer("Генерирую озвучку...")
        return

    parts = raw_query.split(maxsplit=1)
    tts_text = parts[1].strip() if len(parts) > 1 else raw_query
    await callback.answer("Синтез речи запущен...")
    asyncio.create_task(
        _execute_inline_tts_generation(
            bot=callback.bot,
            user_id=callback.from_user.id,
            tts_text=tts_text,
            inline_message_id=callback.inline_message_id,
        )
    )



