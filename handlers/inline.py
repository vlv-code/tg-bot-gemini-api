import asyncio
import hashlib
import html
import logging
import time

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
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
)

from audio import convert_gemini_audio
from config import settings
from handlers.common import (
    URL_REGEX,
    InlineSession,
    _active_inline_tts_tasks,
    _cleanup_inline_sessions,
    _execute_inline_generation,
    _get_user_priority,
    _inline_sessions,
    _parse_inline_query_intent,
    _run_background_task,
    gemini_client,
    global_queue,
    limiter,
    storage,
    user_locks,
)

logger = logging.getLogger(__name__)

router = Router()


# --- Инлайн-режим (карточки Stand, Avatar, TTS) ---

@router.inline_query()
async def handle_inline(query: InlineQuery) -> None:
    raw_query = query.query.strip()
    user_id = query.from_user.id
    _cleanup_inline_sessions()

    if not raw_query:
        help_article = InlineQueryResultArticle(
            id="inline_help",
            title="💬 Введите запрос для Gemini...",
            description="⚡️ Быстрый режим • 👁 Предпросмотр с кнопками • 🎭 Выбор Личностей",
            input_message_content=InputTextMessageContent(
                message_text=(
                    "🌟 <b>Использование бота в инлайн-режиме:</b>\n\n"
                    "• <code>@bot_username ваш текст</code> — быстрый выбор вариантов отправки\n"
                    "• <code>@bot_username /bro текст</code> — ответ в стиле «Бро»\n"
                    "• <code>@bot_username /бизнес текст</code> — ответ в деловом стиле\n"
                    "• <code>@bot_username /tts текст</code> — голосовая озвучка (TTS)\n"
                    "• <code>@bot_username https://картинка вопрос</code> — анализ изображения\n\n"
                    "💡 <i>В выпадающем меню доступны карточки «⚡️ Быстро» (без кнопок) и «👁 Предпросмотр» (с кнопками перегенерации и смены стиля прямо в чате).</i>"
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

    # Режим 1: Озвучка текста (/tts или tts) -> нативное голосовое сообщение в чат
    if raw_query.lower().startswith(("/tts", "tts")):
        parts = raw_query.split(maxsplit=1)
        tts_text = parts[1].strip() if len(parts) > 1 else ""
        if not tts_text:
            article = InlineQueryResultArticle(
                id="tts_hint",
                title="🎙 Озвучить текст (TTS)",
                description="Наберите: @bot_username /tts Текст для озвучки",
                input_message_content=InputTextMessageContent(
                    message_text="Использование TTS: <code>@bot_username /tts Текст для озвучки</code>",
                    parse_mode="HTML",
                ),
            )
            await query.answer(results=[article], cache_time=0, is_personal=True)
            return

        result_id = hashlib.sha256(raw_query.encode("utf-8")).hexdigest()[:24]
        state = await storage.get(user_id)

        # 1. Проверяем постоянный кэш в SQLite (0 мс, 0 квоты)
        cached_file_id = await storage.get_cached_tts_voice(
            text=tts_text,
            voice=state.tts_voice,
            model=state.tts_model,
        )

        if cached_file_id:
            voice_res = InlineQueryResultCachedVoice(
                id=result_id,
                voice_file_id=cached_file_id,
                title=f"🎙 {tts_text[:60]}",
            )
            await query.answer(results=[voice_res], cache_time=300, is_personal=True)
            return

        # 2. Если текста мало (набор только начался) — показываем статус
        if len(tts_text) < 3:
            article = InlineQueryResultArticle(
                id="tts_typing",
                title="🎙 Озвучить текст (TTS)",
                description=f"«{tts_text}» (продолжайте ввод...)",
                input_message_content=InputTextMessageContent(
                    message_text=f"🎙 <b>Озвучка:</b> {html.escape(tts_text)}",
                    parse_mode="HTML",
                ),
            )
            await query.answer(results=[article], cache_time=0, is_personal=True)
            return

        # 3. Debounce: ждем паузу в наборе текста
        query_token = f"{query.id}:{result_id}"
        _active_inline_tts_tasks[user_id] = query_token

        if settings.inline_tts_debounce_seconds > 0:
            await asyncio.sleep(settings.inline_tts_debounce_seconds)

        if _active_inline_tts_tasks.get(user_id) != query_token:
            return

        # 4. Генерируем речь через Gemini API
        async with user_locks.get(user_id):
            limit_status = await limiter.check(user_id)
            if not limit_status.allowed:
                return

            priority = await _get_user_priority(user_id)
            try:
                async with global_queue.acquire(user_id, priority=priority):
                    audio_bytes = await gemini_client.generate_speech(
                        text=tts_text,
                        voice_name=state.tts_voice,
                        model=state.tts_model,
                    )
                    await limiter.hit(user_id)
            except Exception as exc:
                logger.warning("Ошибка при генерации TTS в инлайн-режиме: %s", exc)
                friendly_msg = str(exc)
                article = InlineQueryResultArticle(
                    id="tts_error",
                    title="⚠️ Озвучка TTS временно недоступна",
                    description=f"{friendly_msg[:80]}",
                    input_message_content=InputTextMessageContent(
                        message_text=(
                            f"🎙 <b>Озвучка текста (TTS):</b>\n<blockquote>{html.escape(tts_text)}</blockquote>\n\n"
                            f"⚠️ <i>{html.escape(friendly_msg)}</i>"
                        ),
                        parse_mode="HTML",
                    ),
                )
                try:
                    await query.answer(results=[article], cache_time=10, is_personal=True)
                except Exception:
                    pass
                return

            audio_data, audio_filename, _ = await convert_gemini_audio(audio_bytes)
            voice_file = BufferedInputFile(audio_data, filename=audio_filename)

            # Бесшумный upload на серверы Telegram для получения voice_file_id
            target_chat_id = user_id
            try:
                sent_msg = await query.bot.send_voice(
                    chat_id=target_chat_id,
                    voice=voice_file,
                    disable_notification=True,
                )
            except Exception:
                if settings.admin_ids:
                    target_chat_id = settings.admin_ids[0]
                    try:
                        sent_msg = await query.bot.send_voice(
                            chat_id=target_chat_id,
                            voice=voice_file,
                            disable_notification=True,
                        )
                    except Exception as exc2:
                        logger.warning("Не удалось выполнить silent upload voice суперадмину: %s", exc2)
                        return
                else:
                    return

            file_id = sent_msg.voice.file_id
            try:
                await query.bot.delete_message(chat_id=target_chat_id, message_id=sent_msg.message_id)
            except Exception:
                pass

            await storage.save_cached_tts_voice(
                text=tts_text,
                voice=state.tts_voice,
                model=state.tts_model,
                file_id=file_id,
            )

        voice_res = InlineQueryResultCachedVoice(
            id=result_id,
            voice_file_id=file_id,
            title=f"🎙 {tts_text[:60]}",
        )
        try:
            await query.answer(results=[voice_res], cache_time=300, is_personal=True)
        except Exception:
            pass
        return

    # Режимы Avatar и Stand
    state = await storage.get(user_id)
    personas = await storage.get_all_personas(user_id)

    clean_query, matched_persona, intent = _parse_inline_query_intent(raw_query, personas)
    if not clean_query:
        clean_query = raw_query

    # Определяем активную личность Аватара
    if matched_persona:
        active_p_id = matched_persona["id"]
        active_p_name = matched_persona.get("title") or matched_persona["name"]
        active_p_prompt = matched_persona["prompt"]
    else:
        active_p_name = "Дефолтный суфлёр"
        active_p_id = "default"
        active_p_prompt = state.quick_prompt or settings.quick_prompt
        for p in personas:
            if p["prompt"].strip() == active_p_prompt.strip():
                active_p_name = p.get("title") or p["name"]
                active_p_id = p["id"]
                break

    query_hash = hashlib.sha256(f"{clean_query}:{time.time()}".encode("utf-8")).hexdigest()[:16]
    prompt_short = clean_query[:65] + ("…" if len(clean_query) > 65 else "")

    # 1. Основной режим: 🥊 Stand (ИИ-стенд / помощник)
    stand_sid = f"stand_{query_hash}"
    _inline_sessions[stand_sid] = InlineSession(
        session_id=stand_sid,
        user_id=user_id,
        query=clean_query,
        persona_id=None,
        persona_name="Stand",
        persona_prompt="",
        is_quick=False,
        interactive=False,
    )
    is_image = bool(URL_REGEX.search(clean_query))
    stand_title = "🖼 Stand: Анализ картинки" if is_image else "🥊 Stand (ИИ-стенд)"
    stand_article = InlineQueryResultArticle(
        id=stand_sid,
        title=stand_title,
        description=f"«{prompt_short}» • Ответ ИИ с цитатой",
        input_message_content=InputTextMessageContent(
            message_text=(
                f"💬 <b>Запрос:</b>\n<blockquote>{html.escape(clean_query)}</blockquote>\n\n"
                "⏳ <i>Запрос отправлен в Stand-режиме. Генерирую ответ...</i>"
            ),
            parse_mode="HTML",
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⏳ Генерация...", callback_data=f"inl_start:{stand_sid}")]]
        ),
    )

    # 2. Основной режим: ⚡️ Avatar: Быстро (отправить сразу от 1-го лица без кнопок)
    fast_sid = f"fast_{query_hash}"
    _inline_sessions[fast_sid] = InlineSession(
        session_id=fast_sid,
        user_id=user_id,
        query=clean_query,
        persona_id=active_p_id,
        persona_name=active_p_name,
        persona_prompt=active_p_prompt,
        is_quick=True,
        interactive=False,
    )
    fast_article = InlineQueryResultArticle(
        id=fast_sid,
        title=f"⚡️ Avatar: Быстро ({active_p_name})",
        description=f"«{prompt_short}» • Отправить сразу от 1-го лица",
        input_message_content=InputTextMessageContent(
            message_text="⏳ <i>Генерирую сообщение от вашего лица...</i>",
            parse_mode="HTML",
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⏳ Генерация...", callback_data=f"inl_start:{fast_sid}")]]
        ),
    )

    # 3. Основной режим: 👁 Avatar: Предпросмотр (от 1-го лица с кнопками перегенерации и смены стиля)
    prev_sid = f"prev_{query_hash}"
    _inline_sessions[prev_sid] = InlineSession(
        session_id=prev_sid,
        user_id=user_id,
        query=clean_query,
        persona_id=active_p_id,
        persona_name=active_p_name,
        persona_prompt=active_p_prompt,
        is_quick=True,
        interactive=True,
    )
    prev_article = InlineQueryResultArticle(
        id=prev_sid,
        title=f"👁 Avatar: Предпросмотр ({active_p_name})",
        description=f"«{prompt_short}» • С кнопками перегенерации и смены стиля",
        input_message_content=InputTextMessageContent(
            message_text=f"⏳ <i>Генерирую сообщение в стиле «{active_p_name}»...</i>",
            parse_mode="HTML",
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⏳ Генерация...", callback_data=f"inl_start:{prev_sid}")]]
        ),
    )

    # 4. Закреплённые пользователем в избранное личности Аватара (ниже основных трёх)
    pinned_personas = await storage.get_pinned_personas(user_id)
    pinned_articles = []
    for p in pinned_personas:
        if str(p["id"]) == str(active_p_id):
            continue
        p_title = p.get("title") or f"🎭 {p['name']}"
        p_sid = f"p_{p['id']}_{query_hash}"
        _inline_sessions[p_sid] = InlineSession(
            session_id=p_sid,
            user_id=user_id,
            query=clean_query,
            persona_id=p["id"],
            persona_name=p_title,
            persona_prompt=p["prompt"],
            is_quick=True,
            interactive=False,
        )
        pinned_articles.append(
            InlineQueryResultArticle(
                id=p_sid,
                title=f"⚡️ {p_title}",
                description=f"«{prompt_short}» • Отправить сразу от 1-го лица",
                input_message_content=InputTextMessageContent(
                    message_text=f"⏳ <i>Генерирую сообщение в стиле «{p_title}»...</i>",
                    parse_mode="HTML",
                ),
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text="⏳ Генерация...", callback_data=f"inl_start:{p_sid}")]]
                ),
            )
        )

    # 3 основные карточки всегда сверху: Stand, Avatar Быстро, Avatar Предпросмотр
    if intent == "avatar":
        results = [fast_article, prev_article, stand_article] + pinned_articles
    elif intent == "stand":
        results = [stand_article, fast_article, prev_article] + pinned_articles
    else:
        results = [stand_article, fast_article, prev_article] + pinned_articles

    await query.answer(
        results=results,
        cache_time=0,
        is_personal=True,
    )


@router.chosen_inline_result()
async def handle_chosen_inline_result(chosen: ChosenInlineResult) -> None:
    """Срабатывает ровно в момент, когда пользователь кликнул карточку и отправил сообщение."""
    if not chosen.inline_message_id:
        return

    if chosen.query.strip().lower().startswith(("/tts", "tts")):
        return

    session_id = chosen.result_id
    _run_background_task(
        _execute_inline_generation(
            bot=chosen.bot,
            user_id=chosen.from_user.id,
            session_id=session_id,
            inline_message_id=chosen.inline_message_id,
            raw_query_override=chosen.query.strip(),
        )
    )


@router.callback_query(F.data == "inl_noop")
async def cb_inl_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith("inl_start:"))
async def cb_inl_start(callback: CallbackQuery) -> None:
    session_id = callback.data.split(":", 1)[1]
    session = _inline_sessions.get(session_id)
    if not session:
        await callback.answer("Сессия устарела. Создайте новый запрос через @bot.", show_alert=True)
        return
    await callback.answer("Генерирую... ⏳")
    if callback.inline_message_id:
        _run_background_task(
            _execute_inline_generation(
                bot=callback.bot,
                user_id=session.user_id,
                session_id=session_id,
                inline_message_id=callback.inline_message_id,
            )
        )


@router.callback_query(F.data.startswith("inl_regen:"))
async def cb_inl_regen(callback: CallbackQuery) -> None:
    session_id = callback.data.split(":", 1)[1]
    session = _inline_sessions.get(session_id)
    if not session:
        await callback.answer("Сессия устарела. Создайте новый запрос через @bot.", show_alert=True)
        return

    if callback.from_user.id != session.user_id and callback.from_user.id not in settings.admin_ids:
        await callback.answer("⛔️ Только автор сообщения может управлять генерацией!", show_alert=True)
        return

    if not callback.inline_message_id:
        await callback.answer("Ошибка: идентификатор сообщения не найден", show_alert=True)
        return

    await callback.answer("Генерирую новый вариант... ⏳")
    _run_background_task(
        _execute_inline_generation(
            bot=callback.bot,
            user_id=session.user_id,
            session_id=session_id,
            inline_message_id=callback.inline_message_id,
            temperature_jitter=True,
        )
    )


@router.callback_query(F.data.startswith("inl_style:"))
async def cb_inl_style(callback: CallbackQuery) -> None:
    session_id = callback.data.split(":", 1)[1]
    session = _inline_sessions.get(session_id)
    if not session:
        await callback.answer("Сессия устарела. Создайте новый запрос через @bot.", show_alert=True)
        return

    if callback.from_user.id != session.user_id and callback.from_user.id not in settings.admin_ids:
        await callback.answer("⛔️ Только автор сообщения может управлять стилем!", show_alert=True)
        return

    if not callback.inline_message_id:
        await callback.answer("Ошибка: идентификатор сообщения не найден", show_alert=True)
        return

    personas = await storage.get_all_personas(session.user_id)
    if not personas:
        await callback.answer("Список личностей пуст", show_alert=True)
        return

    # Находим следующую личность по кругу
    current_idx = -1
    for idx, p in enumerate(personas):
        if str(p["id"]) == str(session.persona_id):
            current_idx = idx
            break

    next_idx = (current_idx + 1) % len(personas)
    next_p = personas[next_idx]
    next_title = next_p.get("title") or f"🎭 {next_p['name']}"

    session.persona_id = next_p["id"]
    session.persona_name = next_title
    session.persona_prompt = next_p["prompt"]

    await callback.answer(f"Стиль изменён на: {next_title} 🎭")
    _run_background_task(
        _execute_inline_generation(
            bot=callback.bot,
            user_id=session.user_id,
            session_id=session_id,
            inline_message_id=callback.inline_message_id,
        )
    )


@router.callback_query(F.data.startswith("inl_fix:"))
async def cb_inl_fix(callback: CallbackQuery) -> None:
    session_id = callback.data.split(":", 1)[1]
    session = _inline_sessions.get(session_id)
    if session and callback.from_user.id != session.user_id and callback.from_user.id not in settings.admin_ids:
        await callback.answer("⛔️ Только автор сообщения может зафиксировать текст!", show_alert=True)
        return

    if callback.inline_message_id:
        try:
            await callback.bot.edit_message_reply_markup(
                inline_message_id=callback.inline_message_id,
                reply_markup=None,
            )
        except TelegramBadRequest:
            pass
    await callback.answer("Сообщение зафиксировано, кнопки удалены ✅")


@router.callback_query(F.data.startswith("inl_del:"))
async def cb_inl_del(callback: CallbackQuery) -> None:
    session_id = callback.data.split(":", 1)[1]
    session = _inline_sessions.get(session_id)
    if session and callback.from_user.id != session.user_id and callback.from_user.id not in settings.admin_ids:
        await callback.answer("⛔️ Только автор сообщения может удалить его!", show_alert=True)
        return

    if callback.inline_message_id:
        try:
            await callback.bot.edit_message_text(
                text="🗑 <i>Сообщение удалено автором.</i>",
                inline_message_id=callback.inline_message_id,
                parse_mode="HTML",
                reply_markup=None,
            )
        except TelegramBadRequest:
            pass
    await callback.answer("Сообщение удалено 🗑")

