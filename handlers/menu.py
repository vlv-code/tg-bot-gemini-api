import hashlib
import html
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    Message,
)
from aiogram.utils.chat_action import ChatActionSender

from audio import convert_gemini_audio
from config import settings
from gemini_client import GeminiError
from keyboards import (
    CODE_TO_MODE,
    clear_history_chats_keyboard,
    info_menu_keyboard,
    limits_keyboard,
    main_menu_keyboard,
    mode_model_selector_keyboard,
    models_hub_keyboard,
    settings_keyboard,
    tts_menu_keyboard,
    tts_models_keyboard,
    tts_voices_keyboard,
)
from handlers.common import (
    _get_user_priority,
    _limits_line,
    _render_info_text,
    _render_limits_menu_text,
    _render_main_menu_text,
    _render_tts_menu_text,
    gemini_client,
    global_queue,
    limiter,
    storage,
    user_locks,
)
from storage import UserState

logger = logging.getLogger(__name__)

router = Router()

MODE_NAMES = {
    "stand": "🥊 Stand (чат)",
    "avatar": "🎭 Avatar (/q)",
    "business": "💼 Business (Secretary)",
    "stt": "🎙 Voice-to-Text (STT)",
    "tts": "🎧 Озвучка (TTS)",
}


def _render_models_hub_text(state: UserState) -> str:
    stand = state.stand_model
    avatar = state.avatar_model or state.model
    business = state.business_model or state.model
    stt = state.stt_model or state.model
    tts = state.tts_model
    return (
        "🤖 <b>Управление моделями Gemini по режимам:</b>\n\n"
        f"🥊 <b>Stand (Основной чат):</b> <code>{html.escape(stand)}</code>\n"
        f"🎭 <b>Avatar (Режим /q, инлайн):</b> <code>{html.escape(avatar)}</code>\n"
        f"💼 <b>Business (Secretary Mode):</b> <code>{html.escape(business)}</code>\n"
        f"🎙 <b>Voice-to-Text (Распознавание):</b> <code>{html.escape(stt)}</code>\n"
        f"🎧 <b>Озвучка (TTS):</b> <code>{html.escape(tts)}</code>\n\n"
        "<i>Нажмите на нужный режим ниже, чтобы выбрать для него активную модель:</i>"
    )


# --- Команды навигации и меню ---

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
    if message.text and "init_tts" in message.text:
        await message.answer(
            "🎙 <b>Озвучка для инлайн-режима успешно активирована!</b>\n"
            "Теперь бот может отправлять вам аудиофайлы. Можете вернуться в чат и использовать "
            "<code>@bot_username /tts</code> или <code>@bot_username ~текст</code>.",
            parse_mode="HTML",
        )

    state = await storage.get_settings(user_id)
    is_admin = await storage.is_user_admin(user_id)
    await message.answer(
        _render_main_menu_text(state),
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(is_admin=is_admin),
    )


@router.message(Command("help", "info", "guide", "справка", "инфо"))
async def cmd_info(message: Message) -> None:
    """О боте, руководство и список возможностей."""
    await message.answer(
        _render_info_text(),
        parse_mode="HTML",
        reply_markup=info_menu_keyboard(),
    )


@router.message(Command("model", "models"))
async def cmd_model(message: Message) -> None:
    state = await storage.get_settings(message.from_user.id)
    await message.answer(
        _render_models_hub_text(state),
        parse_mode="HTML",
        reply_markup=models_hub_keyboard(state),
    )


@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    state = await storage.get_settings(message.from_user.id)
    await message.answer(
        "⚙️ <b>Параметры чата и ответов:</b>",
        parse_mode="HTML",
        reply_markup=settings_keyboard(state.rich_mode, state.voice_mode),
    )


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

        state = await storage.get_settings(user_id)
        priority = await _get_user_priority(user_id)

        async with global_queue.acquire(user_id, priority=priority):
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

        audio_data, audio_filename, _ = await convert_gemini_audio(audio_bytes)
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


# --- Callback-хэндлеры меню и настроек ---

@router.callback_query(F.data.startswith("speak_response"))
async def cb_speak_response(callback: CallbackQuery) -> None:
    """Озвучивает текст сообщения по клику на кнопку под ответом."""
    parts = callback.data.split(":", 1)
    if len(parts) > 1:
        try:
            owner_id = int(parts[1])
            if owner_id > 0 and callback.from_user.id != owner_id and callback.from_user.id not in settings.admin_ids:
                await callback.answer("⛔️ Только автор запроса может нажать эту кнопку!", show_alert=True)
                return
        except ValueError:
            pass

    msg = callback.message
    if not msg or not msg.text:
        await callback.answer("Текст сообщения недоступен", show_alert=True)
        return

    # Очищаем текст от цитаты запроса (blockquote)
    text_to_speak = ""
    if msg.entities:
        quote_end = 0
        for ent in msg.entities:
            if ent.type in ("blockquote", "expandable_blockquote"):
                quote_end = max(quote_end, ent.offset + ent.length)
        if quote_end > 0:
            text_to_speak = msg.text[quote_end:].strip()

    if not text_to_speak:
        # Fallback для plain-режима (строки с >)
        lines = msg.text.split("\n")
        cleaned_lines = [line for line in lines if not line.startswith(">")]
        text_to_speak = "\n".join(cleaned_lines).strip() or msg.text

    if not text_to_speak:
        await callback.answer("Нечего озвучивать", show_alert=True)
        return

    user_id = callback.from_user.id
    async with user_locks.get(user_id):
        limit_status = await limiter.check(user_id)
        if not limit_status.allowed:
            wait_seconds = int(limit_status.retry_after) + 1
            await callback.answer(
                f"Лимит запросов исчерпан. Попробуйте через {wait_seconds} сек.",
                show_alert=True,
            )
            return

        state = await storage.get_settings(user_id)
        priority = await _get_user_priority(user_id)

        await callback.answer("Синтезирую голосовое...")

        try:
            async with global_queue.acquire(user_id, priority=priority):
                audio_bytes = await gemini_client.generate_speech(
                    text=text_to_speak[:2000],
                    voice_name=state.tts_voice,
                    model=state.tts_model,
                )
                await limiter.hit(user_id)
            audio_data, audio_filename, _ = await convert_gemini_audio(audio_bytes)
            voice_file = BufferedInputFile(audio_data, filename=audio_filename)
            await msg.reply_voice(voice_file)
        except Exception as exc:
            logger.exception("Ошибка при озвучке ответа")
            await msg.reply(f"⚠️ Не удалось озвучить: {exc}")


@router.callback_query(F.data == "menu:main")
async def cb_menu_main(callback: CallbackQuery) -> None:
    await callback.answer()
    user_id = callback.from_user.id
    state = await storage.get_settings(user_id)
    is_admin = await storage.is_user_admin(user_id)
    try:
        await callback.message.edit_text(
            _render_main_menu_text(state),
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(is_admin=is_admin),
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.in_({"menu:business", "bizmenu:main"}))
async def cb_menu_business(callback: CallbackQuery) -> None:
    from handlers.business import render_business_main_screen

    await render_business_main_screen(callback)



@router.callback_query(F.data == "menu:model")
async def cb_menu_model(callback: CallbackQuery) -> None:
    await callback.answer()
    state = await storage.get_settings(callback.from_user.id)
    try:
        await callback.message.edit_text(
            _render_models_hub_text(state),
            parse_mode="HTML",
            reply_markup=models_hub_keyboard(state),
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            logger.error("Failed to render models hub: %s", exc)
            await callback.answer("Ошибка при открытии хаба моделей", show_alert=True)


@router.callback_query(F.data.startswith("pick_mode_model:"))
async def cb_pick_mode_model(callback: CallbackQuery) -> None:
    await callback.answer()
    mode = callback.data.split(":", 1)[1]
    state = await storage.get_settings(callback.from_user.id)
    category = "tts" if mode == "tts" else "text"
    available = await storage.get_available_models(category=category, enabled_only=True)

    mode_current = {
        "stand": state.stand_model,
        "avatar": state.avatar_model or state.model,
        "business": state.business_model or state.model,
        "stt": state.stt_model or state.model,
        "tts": state.tts_model,
    }.get(mode, state.model)

    # Определяем начальную страницу пагинации (чтобы активная модель сразу была видна)
    idx = next((i for i, m in enumerate(available) if m["model_id"] == mode_current), 0)
    init_page = (idx // 6) + 1

    mode_title = MODE_NAMES.get(mode, mode)
    text = (
        f"⚙️ <b>Выбор модели для режима {mode_title}:</b>\n\n"
        f"Текущая активная модель: <code>{html.escape(mode_current)}</code>\n\n"
        "<i>Выберите модель из списка актуальных:</i>"
    )
    try:
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=mode_model_selector_keyboard(mode, mode_current, available, page=init_page),
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            logger.error("Failed to open mode model selector (%s): %s", mode, exc)
            await callback.answer("Ошибка при открытии списка моделей", show_alert=True)


@router.callback_query(F.data.startswith("smm_noop:"))
async def cb_smm_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith("smm_page:"))
async def cb_page_mode_model(callback: CallbackQuery) -> None:
    await callback.answer()
    parts = callback.data.split(":", 2)
    if len(parts) != 3:
        return
    _, mode_code, page_str = parts
    try:
        target_page = int(page_str)
    except ValueError:
        target_page = 1
    mode = CODE_TO_MODE.get(mode_code, mode_code)
    state = await storage.get_settings(callback.from_user.id)
    category = "tts" if mode == "tts" else "text"
    available = await storage.get_available_models(category=category, enabled_only=True)

    mode_current = {
        "stand": state.stand_model,
        "avatar": state.avatar_model or state.model,
        "business": state.business_model or state.model,
        "stt": state.stt_model or state.model,
        "tts": state.tts_model,
    }.get(mode, state.model)

    mode_title = MODE_NAMES.get(mode, mode)
    text = (
        f"⚙️ <b>Выбор модели для режима {mode_title}:</b>\n\n"
        f"Текущая активная модель: <code>{html.escape(mode_current)}</code>\n\n"
        "<i>Выберите модель из списка актуальных:</i>"
    )
    try:
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=mode_model_selector_keyboard(mode, mode_current, available, page=target_page),
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            logger.error("Failed to page mode model selector (%s): %s", mode, exc)
            await callback.answer("Ошибка при переключении страницы", show_alert=True)


@router.callback_query(F.data.startswith(("set_mode_model:", "smm:")))
async def cb_set_mode_model(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    page = 1
    if callback.data.startswith("smm:"):
        # Формат: smm:{mode_code}:{model_id} или smm:{mode_code}:{model_id}:p{page}
        if len(parts) < 3:
            await callback.answer("Некорректный запрос", show_alert=True)
            return
        mode_code = parts[1]
        model_id_raw = parts[2]
        if len(parts) > 3 and parts[3].startswith("p"):
            try:
                page = int(parts[3][1:])
            except ValueError:
                page = 1
        mode = CODE_TO_MODE.get(mode_code, mode_code)
    else:
        # Legacy формат: set_mode_model:{mode}:{model_id}
        if len(parts) != 3:
            await callback.answer("Некорректный запрос", show_alert=True)
            return
        mode = parts[1]
        model_id_raw = parts[2]

    user_id = callback.from_user.id
    category = "tts" if mode == "tts" else "text"
    available = await storage.get_available_models(category=category, enabled_only=True)
    available_ids = [m["model_id"] for m in available]

    # Сопоставляем ID модели (в том числе укороченный по хешу)
    model_id = model_id_raw
    if (
        model_id not in available_ids
        and model_id not in settings.available_models
        and model_id not in settings.available_tts_models
    ):
        # Пробуем найти точное совпадение по префиксу и md5 хешу
        matched = None
        if "_" in model_id_raw:
            prefix_candidate, hash_candidate = model_id_raw.rsplit("_", 1)
            matched = next(
                (
                    m_id
                    for m_id in available_ids
                    if m_id.startswith(prefix_candidate)
                    and hashlib.md5(m_id.encode("utf-8")).hexdigest()[:6] == hash_candidate
                ),
                None,
            )
            if not matched:
                matched = next((m_id for m_id in available_ids if m_id.startswith(prefix_candidate)), None)

        if matched:
            model_id = matched
        else:
            await callback.answer("Модель недоступна или отключена", show_alert=True)
            return

    await storage.set_user_mode_model(user_id, mode, model_id)
    if mode == "stand":
        chat_id = callback.message.chat.id if callback.message else user_id
        await storage.clear_history(user_id, chat_id=chat_id)

    mode_title = MODE_NAMES.get(mode, mode)
    clear_msg = " (история чата очищена)" if mode == "stand" else ""
    await callback.answer(f"Модель для {mode_title} переключена на {model_id}{clear_msg}")

    text = (
        f"⚙️ <b>Выбор модели для режима {mode_title}:</b>\n\n"
        f"Текущая активная модель: <code>{html.escape(model_id)}</code>\n\n"
        "<i>Выберите модель из списка актуальных:</i>"
    )
    try:
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=mode_model_selector_keyboard(mode, model_id, available, page=page),
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            logger.error("Failed to update mode model selector (%s): %s", mode, exc)
            await callback.answer("Ошибка при обновлении списка моделей", show_alert=True)


@router.callback_query(F.data == "menu:tts")
async def cb_menu_tts(callback: CallbackQuery) -> None:
    await callback.answer()
    state = await storage.get_settings(callback.from_user.id)
    try:
        await callback.message.edit_text(
            _render_tts_menu_text(state),
            parse_mode="HTML",
            reply_markup=tts_menu_keyboard(state.tts_model, state.tts_voice, state.voice_mode),
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            logger.error("Failed to render TTS menu: %s", exc)
            await callback.answer("Ошибка при открытии меню TTS", show_alert=True)


@router.callback_query(F.data == "menu:tts_models")
async def cb_menu_tts_models(callback: CallbackQuery) -> None:
    await callback.answer()
    state = await storage.get_settings(callback.from_user.id)
    try:
        await callback.message.edit_text(
            f"🎙 Выберите модель для синтеза речи TTS (текущая: <code>{html.escape(state.tts_model)}</code>):",
            parse_mode="HTML",
            reply_markup=tts_models_keyboard(state.tts_model),
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("set_tts_model:"))
async def cb_set_tts_model(callback: CallbackQuery) -> None:
    model = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id

    is_valid = (
        model in settings.available_tts_models
        or await storage.is_model_enabled(model)
    )
    if not is_valid:
        await callback.answer("Модель недоступна или отключена", show_alert=True)
        return

    await storage.set_tts_model(user_id, model)
    await callback.answer(f"TTS модель: {model}")

    try:
        await callback.message.edit_text(
            f"🎙 Выберите модель для синтеза речи TTS (текущая: <code>{html.escape(model)}</code>):",
            parse_mode="HTML",
            reply_markup=tts_models_keyboard(model),
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "menu:tts_voices")
async def cb_menu_tts_voices(callback: CallbackQuery) -> None:
    await callback.answer()
    state = await storage.get_settings(callback.from_user.id)
    try:
        await callback.message.edit_text(
            f"🗣 Выберите голос для озвучки (текущий: <code>{html.escape(state.tts_voice)}</code>):",
            parse_mode="HTML",
            reply_markup=tts_voices_keyboard(state.tts_voice),
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("set_tts_voice:"))
async def cb_set_tts_voice(callback: CallbackQuery) -> None:
    voice = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id

    if voice not in settings.available_voices:
        await callback.answer("Голос недоступен", show_alert=True)
        return

    await storage.set_tts_voice(user_id, voice)
    await callback.answer(f"Голос озвучки: {voice}")

    try:
        await callback.message.edit_text(
            f"🗣 Выберите голос для озвучки (текущий: <code>{html.escape(voice)}</code>):",
            parse_mode="HTML",
            reply_markup=tts_voices_keyboard(voice),
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "toggle_voice_tts")
async def cb_toggle_voice_tts(callback: CallbackQuery) -> None:
    voice = await storage.toggle_voice(callback.from_user.id)
    state = await storage.get_settings(callback.from_user.id)
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
    await callback.answer()
    state = await storage.get_settings(callback.from_user.id)
    try:
        await callback.message.edit_text(
            "⚙️ <b>Параметры чата и ответов:</b>",
            parse_mode="HTML",
            reply_markup=settings_keyboard(state.rich_mode, state.voice_mode),
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "toggle_rich")
async def cb_toggle_rich(callback: CallbackQuery) -> None:
    rich = await storage.toggle_rich(callback.from_user.id)
    state = await storage.get_settings(callback.from_user.id)
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
    state = await storage.get_settings(callback.from_user.id)
    try:
        await callback.message.edit_text(
            "⚙️ <b>Параметры чата и ответов:</b>",
            parse_mode="HTML",
            reply_markup=settings_keyboard(state.rich_mode, voice),
        )
    except TelegramBadRequest:
        pass
    await callback.answer("Голосовые ответы: " + ("включены" if voice else "выключены"))


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
        state = await storage.get_settings(user_id)
        try:
            await callback.message.edit_text(
                "⚙️ <b>Параметры чата и ответов:</b>\n\n✅ <i>Вся история диалогов очищена.</i>",
                parse_mode="HTML",
                reply_markup=settings_keyboard(state.rich_mode, state.voice_mode),
            )
        except TelegramBadRequest:
            pass


@router.callback_query(F.data == "menu:info")
async def cb_menu_info(callback: CallbackQuery) -> None:
    await callback.answer()
    try:
        await callback.message.edit_text(
            _render_info_text(),
            parse_mode="HTML",
            reply_markup=info_menu_keyboard(),
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.in_({"menu:limits", "refresh_limits"}))
async def cb_menu_limits(callback: CallbackQuery) -> None:
    await callback.answer()
    text = await _render_limits_menu_text(callback.from_user.id)
    try:
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=limits_keyboard(),
        )
    except TelegramBadRequest:
        pass

