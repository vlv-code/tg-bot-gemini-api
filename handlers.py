import hashlib
import html
import io
import logging
from typing import Optional, Sequence, Union

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    ChosenInlineResult,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
)
from aiogram.utils.chat_action import ChatActionSender
from google.genai import types

from config import settings
from formatting import find_utf16_cut, markdown_to_chunks, split_plain_text, utf16_len
from gemini_client import GeminiError, GeminiResponse, build_gemini_client
from keyboards import (
    limits_keyboard,
    main_menu_keyboard,
    models_keyboard,
    prompt_keyboard,
    settings_keyboard,
    tts_menu_keyboard,
    tts_models_keyboard,
    tts_voices_keyboard,
)
from locks import UserLocks
from middlewares import AccessMiddleware
from rate_limiter import RateLimiter
from storage import UserState, UserStorage

logger = logging.getLogger(__name__)

router = Router()
router.message.outer_middleware(AccessMiddleware())
router.callback_query.outer_middleware(AccessMiddleware())
router.inline_query.outer_middleware(AccessMiddleware())
router.chosen_inline_result.outer_middleware(AccessMiddleware())

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
        f"• <b>Контекст диалога:</b> {history_count} реплик в памяти\n\n"
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
    force_voice: bool = False,
) -> None:
    """Отправляет ответ пользователю (голосовое сообщение и/или текст с цитатой запроса)."""
    if (state.voice_mode or force_voice) and response.audio_bytes:
        try:
            voice_file = BufferedInputFile(response.audio_bytes, filename="voice.ogg")
            await message.answer_voice(voice_file)
        except Exception:
            logger.exception("Не удалось отправить голосовое сообщение, переключаемся на текст")

    if response.text:
        full_text = _format_with_prompt_quote(prompt_text, response.text)
        if state.rich_mode:
            for chunk_text, chunk_entities in markdown_to_chunks(full_text):
                try:
                    await message.answer(chunk_text, entities=chunk_entities)
                except Exception:
                    logger.warning("Ошибка отправки с entities, отправляем чистым текстом")
                    await message.answer(chunk_text)
        else:
            for chunk in split_plain_text(full_text):
                await message.answer(chunk)

    await message.answer(await _limits_line(user_id))



# --- Команды бота ---

@router.message(CommandStart())
@router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    state = await storage.get(message.from_user.id)
    await message.answer(
        _render_main_menu_text(state),
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )


@router.message(Command("model"))
async def cmd_model(message: Message) -> None:
    state = await storage.get(message.from_user.id)
    await message.answer(
        f"🤖 Выберите основную модель Gemini (текущая: <code>{html.escape(state.model)}</code>):",
        parse_mode="HTML",
        reply_markup=models_keyboard(state.model),
    )


@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    state = await storage.get(message.from_user.id)
    await message.answer(
        "⚙️ <b>Параметры чата и ответов:</b>",
        parse_mode="HTML",
        reply_markup=settings_keyboard(state.rich_mode, state.voice_mode),
    )


@router.message(Command("prompt"))
async def cmd_prompt(message: Message) -> None:
    user_id = message.from_user.id
    state = await storage.get(user_id)
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

        state = await storage.get(user_id)

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

        voice_file = BufferedInputFile(audio_bytes, filename="tts.ogg")
        await message.answer_voice(voice_file)


@router.message(Command("limits"))
async def cmd_limits(message: Message) -> None:
    user_id = message.from_user.id
    status_line = await _limits_line(user_id)
    await message.answer(
        f"📊 <b>Текущие лимиты запросов:</b>\n\n{status_line}",
        parse_mode="HTML",
        reply_markup=limits_keyboard(),
    )


# --- Callback-хендлеры навигации по единому меню ---

@router.callback_query(F.data == "menu:main")
async def cb_menu_main(callback: CallbackQuery) -> None:
    state = await storage.get(callback.from_user.id)
    try:
        await callback.message.edit_text(
            _render_main_menu_text(state),
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data == "menu:model")
async def cb_menu_model(callback: CallbackQuery) -> None:
    state = await storage.get(callback.from_user.id)
    try:
        await callback.message.edit_text(
            f"🤖 Выберите основную модель Gemini (текущая: <code>{html.escape(state.model)}</code>):",
            parse_mode="HTML",
            reply_markup=models_keyboard(state.model),
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("set_model:") | F.data.startswith("model:"))
async def cb_set_model(callback: CallbackQuery) -> None:
    model = callback.data.split(":", 1)[1]
    if model not in settings.available_models:
        await callback.answer("Неизвестная модель", show_alert=True)
        return

    user_id = callback.from_user.id
    await storage.set_model(user_id, model)
    await storage.clear_history(user_id)

    try:
        await callback.message.edit_text(
            f"🤖 Выберите основную модель Gemini (текущая: <code>{html.escape(model)}</code>):",
            parse_mode="HTML",
            reply_markup=models_keyboard(model),
        )
    except TelegramBadRequest:
        pass
    await callback.answer(f"Модель переключена на {model} (контекст очищен)")


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
    state = await storage.get(callback.from_user.id)
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
    rich = await storage.toggle_rich(callback.from_user.id)
    state = await storage.get(callback.from_user.id)
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
    voice = await storage.toggle_voice(callback.from_user.id)
    state = await storage.get(callback.from_user.id)
    try:
        await callback.message.edit_text(
            "⚙️ <b>Параметры чата и ответов:</b>",
            parse_mode="HTML",
            reply_markup=settings_keyboard(state.rich_mode, voice),
        )
    except TelegramBadRequest:
        pass
    await callback.answer("Голосовые ответы: " + ("включены" if voice else "выключены"))


@router.callback_query(F.data == "clear_history" | F.data == "menu:clear_hist")
async def cb_clear_history(callback: CallbackQuery) -> None:
    await storage.clear_history(callback.from_user.id)
    state = await storage.get(callback.from_user.id)
    # Если нажали из главного меню, обновим счетчик в главном меню
    if callback.data == "menu:clear_hist":
        try:
            await callback.message.edit_text(
                _render_main_menu_text(state),
                parse_mode="HTML",
                reply_markup=main_menu_keyboard(),
            )
        except TelegramBadRequest:
            pass
    await callback.answer("История диалога очищена ✅")


@router.callback_query(F.data == "menu:prompt")
async def cb_menu_prompt(callback: CallbackQuery) -> None:
    state = await storage.get(callback.from_user.id)
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
    await storage.set_system_prompt(callback.from_user.id, "")
    state = await storage.get(callback.from_user.id)
    try:
        await callback.message.edit_text(
            _render_prompt_menu_text(state),
            parse_mode="HTML",
            reply_markup=prompt_keyboard(),
        )
    except TelegramBadRequest:
        pass
    await callback.answer("Промпт сброшен к дефолтному ✅")


@router.callback_query(F.data == "menu:limits" | F.data == "refresh_limits")
async def cb_menu_limits(callback: CallbackQuery) -> None:
    status_line = await _limits_line(callback.from_user.id)
    try:
        await callback.message.edit_text(
            f"📊 <b>Текущие лимиты запросов:</b>\n\n{status_line}",
            parse_mode="HTML",
            reply_markup=limits_keyboard(),
        )
    except TelegramBadRequest:
        pass
    await callback.answer("Лимиты обновлены")



# --- Общий обработчик запросов (текст / фото / документы / голосовые) ---

async def _process_user_turn(
    message: Message,
    content_input: Union[str, Sequence[Union[str, types.Part]]],
    history_text: str,
    force_voice_reply: bool = False,
) -> None:
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

        state = await storage.get(user_id)
        action = (
            ChatActionSender.record_voice
            if (state.voice_mode or force_voice_reply)
            else ChatActionSender.typing
        )

        async with action(bot=message.bot, chat_id=message.chat.id):
            try:
                response = await gemini_client.ask(
                    model=state.model,
                    history_turns=state.history,
                    message=content_input,
                    system_prompt=state.system_prompt,
                    want_audio=(state.voice_mode or force_voice_reply),
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
            await storage.add_turn(user_id, "user", history_text)
            if response.text:
                await storage.add_turn(user_id, "model", response.text)

        await _send_response(
            message=message,
            state=state,
            response=response,
            user_id=user_id,
            prompt_text=history_text,
            force_voice=force_voice_reply,
        )


@router.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message) -> None:
    await _process_user_turn(
        message=message,
        content_input=message.text,
        history_text=message.text,
        force_voice_reply=False,
    )


@router.message(F.photo)
async def handle_photo(message: Message) -> None:
    photo = message.photo[-1]
    prompt_text = message.caption or "Опиши подробно, что изображено на этом фото."

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
        force_voice_reply=False,
    )


@router.message(F.voice | F.audio)
async def handle_voice(message: Message) -> None:
    media = message.voice or message.audio
    mime_type = media.mime_type or ("audio/ogg" if message.voice else "audio/mp3")
    prompt_text = message.caption or "Ответь на аудиосообщение."

    file_io = io.BytesIO()
    await message.bot.download(media.file_id, destination=file_io)
    audio_bytes = file_io.getvalue()

    audio_part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)
    content_input = [audio_part, prompt_text]
    history_text = f"[Голосовое сообщение] {prompt_text}"

    # Если пользователь прислал голосовое — отвечаем голосом
    await _process_user_turn(
        message=message,
        content_input=content_input,
        history_text=history_text,
        force_voice_reply=True,
    )


@router.message(F.document)
async def handle_document(message: Message) -> None:
    doc = message.document
    mime_type = doc.mime_type or "application/pdf"
    prompt_text = message.caption or f"Проанализируй документ '{doc.file_name or 'файл'}'."

    file_io = io.BytesIO()
    await message.bot.download(doc.file_id, destination=file_io)
    doc_bytes = file_io.getvalue()

    doc_part = types.Part.from_bytes(data=doc_bytes, mime_type=mime_type)
    content_input = [doc_part, prompt_text]
    history_text = f"[Документ {doc.file_name or ''}] {prompt_text}"

    await _process_user_turn(
        message=message,
        content_input=content_input,
        history_text=history_text,
        force_voice_reply=False,
    )


# --- Inline mode (без сжигания квоты при наборе текста) ---

INLINE_ANSWER_LIMIT = 3900


@router.inline_query()
async def handle_inline(query: InlineQuery) -> None:
    raw_query = query.query.strip()
    if not raw_query:
        # Если запрос пустой, подсказываем формат использования
        help_article = InlineQueryResultArticle(
            id="inline_help",
            title="💬 Задайте вопрос Gemini...",
            description="Наберите текст запроса: @bot_username ваш вопрос",
            input_message_content=InputTextMessageContent(
                message_text="Чтобы задать вопрос Gemini в любом чате, наберите: <code>@bot_username ваш вопрос</code>",
                parse_mode="HTML",
            ),
        )
        await query.answer(
            results=[help_article],
            cache_time=0,
            is_personal=True,
        )
        return

    # Не вызываем Gemini API при вводе текста, отдаём карточку мгновенно!
    result_id = hashlib.sha256(raw_query.encode("utf-8")).hexdigest()[:24]
    prompt_short = raw_query[:80] + ("…" if len(raw_query) > 80 else "")

    article = InlineQueryResultArticle(
        id=result_id,
        title="💬 Отправить запрос к Gemini",
        description=f"«{prompt_short}» (нажмите, чтобы сгенерировать ответ)",
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

    user_id = chosen.from_user.id
    raw_query = chosen.query.strip()
    if not raw_query:
        return

    inline_msg_id = chosen.inline_message_id

    async with user_locks.get(user_id):
        limit_status = await limiter.check(user_id)
        if not limit_status.allowed:
            wait_seconds = int(limit_status.retry_after) + 1
            try:
                await chosen.bot.edit_message_text(
                    text=(
                        f"💬 <b>Запрос:</b>\n<blockquote>{html.escape(raw_query)}</blockquote>\n\n"
                        f"⏳ <i>Лимит запросов исчерпан. Попробуйте снова через {wait_seconds} сек.</i>"
                    ),
                    inline_message_id=inline_msg_id,
                    parse_mode="HTML",
                )
            except TelegramBadRequest:
                pass
            return

        state = await storage.get(user_id)

        try:
            response = await gemini_client.ask(
                model=state.model,
                history_turns=state.history,
                message=raw_query,
                system_prompt=state.system_prompt,
                want_audio=False,
            )
        except GeminiError as exc:
            try:
                await chosen.bot.edit_message_text(
                    text=(
                        f"💬 <b>Запрос:</b>\n<blockquote>{html.escape(raw_query)}</blockquote>\n\n"
                        f"⚠️ <i>Ошибка Gemini: {html.escape(str(exc))}</i>"
                    ),
                    inline_message_id=inline_msg_id,
                    parse_mode="HTML",
                )
            except TelegramBadRequest:
                pass
            return
        except Exception:
            logger.exception("Unexpected error in chosen_inline_result")
            try:
                await chosen.bot.edit_message_text(
                    text=(
                        f"💬 <b>Запрос:</b>\n<blockquote>{html.escape(raw_query)}</blockquote>\n\n"
                        "⚠️ <i>Непредвиденная ошибка при генерации ответа.</i>"
                    ),
                    inline_message_id=inline_msg_id,
                    parse_mode="HTML",
                )
            except TelegramBadRequest:
                pass
            return

        await limiter.hit(user_id)
        await storage.add_turn(user_id, "user", raw_query)
        if response.text:
            await storage.add_turn(user_id, "model", response.text)

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
                await chosen.bot.edit_message_text(
                    text=chunk_text,
                    entities=chunk_entities,
                    inline_message_id=inline_msg_id,
                )
                return
            except TelegramBadRequest:
                pass

    try:
        await chosen.bot.edit_message_text(
            text=full_text,
            inline_message_id=inline_msg_id,
        )
    except TelegramBadRequest:
        pass


