"""Secretary Mode (Telegram Business Bots).

Как это устроено:
1. Владелец подключает бота к своему личному аккаунту через
   Settings → Telegram Business → Chatbots — это отдельная фича Telegram,
   не связанная с ALLOWED_USER_IDS/whitelist обычного чата с ботом.
2. Telegram присылает апдейт business_connection с правами (BusinessBotRights).
   Мы сохраняем его в storage, чтобы знать owner_user_id и can_reply.
3. На каждое сообщение в подключённых личных чатах приходит апдейт
   business_message. Если для этого конкретного чата НЕ включён точечный
   авто-ответ — бот готовит черновик через Gemini и присылает его владельцу
   в личку с ботом на подтверждение (ничего не уходит без тапа). Если
   авто-ответ включён — отвечает сразу через business_connection_id.
4. Message.answer() в aiogram сам подставляет business_connection_id для
   сообщений, полученных как business_message — поэтому для авто-ответа
   достаточно обычного message.answer(). Для отправки уже сохранённого
   черновика по кнопке (из чата с ботом, а не из бизнес-чата) business_connection_id
   передаём в bot.send_message() явно.

Ограничения самого Telegram (не наши): только личные 1-на-1 чаты, никаких
групп/каналов; отвечать можно только в чатах, где было реальное входящее
сообщение за последние 24 часа.
"""

import html
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Optional

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import BusinessConnection, CallbackQuery, Message

from config import settings
from formatting import markdown_to_chunks, split_plain_text
from gemini_client import GeminiError
from handlers.common import _get_user_priority, gemini_client, global_queue, limiter, storage, user_locks
from keyboards import (
    business_autoreply_chats_keyboard,
    business_draft_keyboard,
    business_facts_keyboard,
    business_keywords_keyboard,
    business_menu_back_keyboard,
    business_menu_keyboard,
)
from storage import MAX_BUSINESS_FACTS_PER_OWNER, MAX_BUSINESS_KEYWORD_RULES_PER_OWNER

logger = logging.getLogger(__name__)

router = Router()

_PENDING_DRAFT_TTL_SECONDS = 3600  # черновик старше часа считаем неактуальным


@dataclass
class PendingDraft:
    draft_id: str
    business_connection_id: str
    chat_id: int
    owner_user_id: int
    draft_text: str
    created_at: float = field(default_factory=time.time)


# Временное хранилище черновиков на подтверждение (draft_id -> PendingDraft).
# Живёт только в памяти процесса: рестарт бота = черновики теряются, собеседник
# просто не получит ответ на то конкретное сообщение — не страшно и не опасно,
# в отличие от хранения в БД ради пары часов жизни объекта.
_pending_drafts: dict[str, PendingDraft] = {}


def _cleanup_pending_drafts() -> None:
    now = time.time()
    expired = [did for did, d in _pending_drafts.items() if now - d.created_at > _PENDING_DRAFT_TTL_SECONDS]
    for did in expired:
        _pending_drafts.pop(did, None)


def _build_secretary_prompt(
    user_system_prompt: str,
    facts: Optional[list[dict]] = None,
    hints: Optional[list[str]] = None,
) -> str:
    """Системный промпт для Secretary Mode: ответ уходит реальному человеку в личной
    переписке владельца, поэтому рамки строже, чем в обычном чате с ботом."""
    base = (
        "You are acting as the personal secretary/assistant for the Telegram account owner, "
        "replying directly to a real person in the owner's private chat. "
        "Write the reply in the FIRST PERSON, as if the owner is speaking themselves — "
        "never mention that you are an AI, a bot, or an assistant, and never break character. "
        "Keep the reply natural, concise, and appropriate for a real personal conversation. "
        "If you don't have enough context to answer confidently (e.g. a question about the "
        "owner's plans, availability, or commitments you don't know), write a brief, polite "
        "holding reply instead of guessing or inventing facts."
    )
    parts = [base]

    if user_system_prompt and user_system_prompt.strip():
        parts.append(f"OWNER'S PREFERRED TONE/STYLE:\n{user_system_prompt.strip()}")

    if facts:
        facts_block = "\n".join(f"- {f['fact_key']}: {f['fact_value']}" for f in facts)
        parts.append(
            "KEY BUSINESS FACTS (use these when relevant to answer accurately; "
            f"do not invent other facts not listed here):\n{facts_block}"
        )

    if hints:
        hints_block = "\n".join(f"- {h}" for h in hints)
        parts.append(
            "SPECIFIC GUIDANCE FOR THIS MESSAGE (the client's message matched these "
            f"owner-configured triggers — follow this guidance for this reply):\n{hints_block}"
        )

    return "\n\n".join(parts)


def _match_keyword_rules(rules: list[dict], text: str) -> list[dict]:
    """Простое регистронезависимое совпадение по вхождению подстроки — намеренно
    без учёта границ слов, чтобы ловить словоформы (цена/цену/ценам) без морфологии.
    Может вернуть несколько совпадений сразу."""
    text_lower = text.lower()
    return [r for r in rules if r["keyword"].lower() in text_lower]


def _render_business_menu_text() -> str:
    return (
        "🏢 <b>Бизнес-меню (Secretary Mode)</b>\n\n"
        "Факты и правила ниже подмешиваются в ответы Gemini клиентам в подключённых "
        "личных чатах — независимо от обычных настроек чата с ботом."
    )


def _render_business_facts_text(facts: list[dict]) -> str:
    if not facts:
        return (
            "📋 <b>Факты о бизнесе</b>\n\n"
            "Пока не добавлено ни одного факта.\n\n"
            "Добавить/изменить: <code>/bizfact Часы работы = Пн-Пт 9:00-18:00</code>\n"
            "Удалить: <code>/bizfact del Часы работы</code> или кнопкой ниже."
        )
    lines = ["📋 <b>Факты о бизнесе:</b>\n"]
    for f in facts:
        lines.append(f"• <b>{html.escape(f['fact_key'])}:</b> {html.escape(f['fact_value'])}")
    lines.append(
        f"\n(использовано {len(facts)}/{MAX_BUSINESS_FACTS_PER_OWNER})\n"
        "Добавить/изменить: <code>/bizfact Ключ = Значение</code>\nУдалить: кнопкой ниже."
    )
    return "\n".join(lines)


def _render_business_keywords_text(rules: list[dict]) -> str:
    if not rules:
        return (
            "🔑 <b>Ключевые слова</b>\n\n"
            "Пока не добавлено ни одного правила.\n\n"
            "📄 Готовый шаблон (без Gemini, мгновенно):\n"
            "<code>/bizkeyword template цена = Актуальные цены на сайте example.com</code>\n\n"
            "💡 Подсказка для Gemini (ответ всё равно живой):\n"
            "<code>/bizkeyword hint жалоба = Отвечай с эмпатией, предложи скидку 10%</code>\n\n"
            "Удалить: <code>/bizkeyword del цена</code> или кнопкой ниже."
        )
    lines = ["🔑 <b>Правила по ключевым словам:</b>\n"]
    for r in rules:
        badge = "📄 шаблон" if r["rule_type"] == "template" else "💡 подсказка"
        content_preview = r["content"][:150] + ("…" if len(r["content"]) > 150 else "")
        lines.append(f"• <b>{html.escape(r['keyword'])}</b> ({badge}):\n  {html.escape(content_preview)}")
    lines.append(
        f"\n(использовано {len(rules)}/{MAX_BUSINESS_KEYWORD_RULES_PER_OWNER})\n"
        "Добавить/изменить: <code>/bizkeyword template|hint Слово = Текст</code>\nУдалить: кнопкой ниже."
    )
    return "\n".join(lines)


async def _render_connections_status_text(owner_user_id: int) -> str:
    connections = await storage.get_business_connections_for_owner(owner_user_id)
    if not connections:
        return (
            "🔌 <b>Secretary Mode пока не подключён.</b>\n\n"
            "Подключить: Настройки Telegram → Telegram Business → Chatbots → выбрать этого бота, "
            "выдать право «Read and reply»."
        )
    lines = ["🔌 <b>Ваши Business-подключения:</b>\n"]
    for c in connections:
        status = "✅ активно" if c["is_enabled"] and c["can_reply"] else "⚠️ ограничено/отключено"
        lines.append(f"• <code>{html.escape(c['business_connection_id'][:16])}…</code> — {status}")
    return "\n".join(lines)


async def _send_business_reply(message: Message, text: str, rich_mode: bool) -> None:
    """Отправляет ответ напрямую собеседнику от имени владельца. message.answer() сам
    подставляет business_connection_id, т.к. это Message из апдейта business_message."""
    if rich_mode:
        for chunk_text, chunk_entities in markdown_to_chunks(text):
            try:
                await message.answer(chunk_text, entities=chunk_entities)
            except Exception:
                logger.warning("Secretary Mode: ошибка entities при авто-ответе, шлём чистым текстом")
                for plain_chunk in split_plain_text(chunk_text):
                    await message.answer(plain_chunk)
    else:
        for chunk in split_plain_text(text):
            await message.answer(chunk)


@router.business_connection()
async def on_business_connection(event: BusinessConnection, bot: Bot) -> None:
    """Обрабатывает подключение/изменение/отключение бота к личному аккаунту владельца."""
    owner_user_id = event.user.id
    can_reply = bool(event.rights and event.rights.can_reply)

    await storage.upsert_business_connection(
        business_connection_id=event.id,
        owner_user_id=owner_user_id,
        user_chat_id=event.user_chat_id,
        can_reply=can_reply,
        is_enabled=event.is_enabled,
    )

    logger.info(
        "Business connection %s: owner=%s can_reply=%s is_enabled=%s",
        event.id, owner_user_id, can_reply, event.is_enabled,
    )

    if not event.is_enabled:
        status_text = "🔌 Secretary Mode отключён для этого подключения."
    elif not can_reply:
        status_text = (
            "🔒 Бот подключён к вашему аккаунту, но право «Read and reply» не выдано — "
            "секретарь не сможет отвечать. Проверьте права подключения в Настройках Telegram "
            "(Settings → Telegram Business → Chatbots)."
        )
    else:
        status_text = (
            "✅ <b>Secretary Mode подключён.</b>\n\n"
            "Для новых чатов по умолчанию бот присылает вам черновик ответа на подтверждение — "
            "ничего не уходит собеседнику без вашего тапа. Авто-ответ можно включить точечно "
            "прямо из карточки черновика. Статус подключения и список авто-чатов — командой /business."
        )

    try:
        await bot.send_message(chat_id=event.user_chat_id, text=status_text, parse_mode="HTML")
    except Exception:
        logger.warning("Не удалось отправить уведомление о статусе Business-подключения владельцу %s", owner_user_id)


@router.business_message()
async def on_business_message(message: Message, bot: Bot) -> None:
    """Обрабатывает входящее сообщение в подключённом личном чате владельца."""
    business_connection_id = message.business_connection_id
    if not business_connection_id:
        return  # защитный случай — по спецификации Bot API всегда должен быть заполнен

    connection = await storage.get_business_connection(business_connection_id)
    if connection is None or not connection["is_enabled"] or not connection["can_reply"]:
        return  # подключение неизвестно / отключено / без права отвечать — молча игнорируем

    owner_user_id = connection["owner_user_id"]

    # Сообщения, которые владелец отправил сам через свой обычный клиент Telegram,
    # тоже прилетают как business_message — не отвечаем на них, иначе бот начнёт
    # реагировать на собственные реплики владельца.
    if message.from_user and message.from_user.id == owner_user_id:
        return

    incoming_text = (message.text or message.caption or "").strip()
    if not incoming_text:
        return  # V1: только текст; фото/голос/документы в бизнес-чатах — задел на будущее

    chat_id = message.chat.id
    counterparty_name = (
        message.chat.first_name or message.chat.title or message.chat.full_name or "Собеседник"
    )
    await storage.track_business_chat(business_connection_id, chat_id, counterparty_name)

    # Проверяем совпадения по ключевым словам ДО обращения к Gemini.
    rules = await storage.get_business_keyword_rules(owner_user_id)
    matched_rules = _match_keyword_rules(rules, incoming_text)
    template_match = next((r for r in matched_rules if r["rule_type"] == "template"), None)

    if template_match is not None:
        # Шаблон уже заранее одобрен владельцем — отправляем мгновенно: без Gemini
        # (значит без лимитера и очереди, это не запрос к API) и без черновика на
        # подтверждение (владелец и так согласился с этим текстом, когда его сохранял).
        await storage.add_turn(owner_user_id, "user", incoming_text, chat_id=chat_id, mode="business")
        await storage.add_turn(owner_user_id, "model", template_match["content"], chat_id=chat_id, mode="business")
        owner_settings = await storage.get_settings(owner_user_id)
        await _send_business_reply(message, template_match["content"], owner_settings.rich_mode)
        return

    hint_texts = [r["content"] for r in matched_rules if r["rule_type"] == "hint"]

    response = None
    state = None

    async with user_locks.get(owner_user_id):
        limit_status = await limiter.check(owner_user_id)
        if not limit_status.allowed:
            # Владелец исчерпал личный лимит запросов — молча пропускаем сообщение,
            # а не будим его алертом об ошибке на каждое чужое входящее.
            logger.info(
                "Secretary Mode: лимит запросов исчерпан для владельца %s, сообщение из чата %s пропущено",
                owner_user_id, chat_id,
            )
            return

        priority = await _get_user_priority(owner_user_id)

        async with global_queue.acquire(owner_user_id, priority=priority):
            state = await storage.get(owner_user_id, chat_id=chat_id, mode="business")
            facts = await storage.get_business_facts(owner_user_id)
            effective_prompt = _build_secretary_prompt(
                state.system_prompt or settings.system_prompt, facts=facts, hints=hint_texts
            )

            try:
                response = await gemini_client.ask(
                    model=state.model,
                    history_turns=state.history,
                    message=incoming_text,
                    system_prompt=effective_prompt,
                    want_audio=False,
                )
            except GeminiError as exc:
                logger.warning("Secretary Mode: ошибка Gemini для владельца %s: %s", owner_user_id, exc)
                return
            except Exception:
                logger.exception("Secretary Mode: непредвиденная ошибка при обращении к Gemini API")
                return

            await limiter.hit(owner_user_id)
            await storage.add_turn(owner_user_id, "user", incoming_text, chat_id=chat_id, mode="business")
            if response.text:
                await storage.add_turn(owner_user_id, "model", response.text, chat_id=chat_id, mode="business")
            if response.total_tokens > 0:
                await storage.record_token_usage(
                    user_id=owner_user_id,
                    chat_id=chat_id,
                    model=state.model,
                    prompt_tokens=response.prompt_tokens,
                    candidates_tokens=response.candidates_tokens,
                    total_tokens=response.total_tokens,
                )

    if response is None or not response.text:
        return

    auto_reply = await storage.is_auto_reply_enabled(business_connection_id, chat_id)
    if auto_reply:
        await _send_business_reply(message, response.text, state.rich_mode)
        return

    _cleanup_pending_drafts()
    draft_id = secrets.token_urlsafe(6)
    _pending_drafts[draft_id] = PendingDraft(
        draft_id=draft_id,
        business_connection_id=business_connection_id,
        chat_id=chat_id,
        owner_user_id=owner_user_id,
        draft_text=response.text,
    )

    preview_incoming = incoming_text[:300] + ("…" if len(incoming_text) > 300 else "")
    preview = (
        f"✉️ <b>Черновик ответа для «{html.escape(counterparty_name)}»:</b>\n\n"
        f"<blockquote>{html.escape(preview_incoming)}</blockquote>\n\n"
        f"{html.escape(response.text)}"
    )
    try:
        await bot.send_message(
            chat_id=connection["user_chat_id"],
            text=preview,
            parse_mode="HTML",
            reply_markup=business_draft_keyboard(draft_id),
        )
    except Exception:
        logger.warning("Не удалось отправить черновик владельцу %s", owner_user_id)


@router.callback_query(F.data.startswith("biz_draft:"))
async def cb_business_draft(callback: CallbackQuery, bot: Bot) -> None:
    parts = (callback.data or "").split(":", 2)
    if len(parts) != 3:
        await callback.answer()
        return
    _, action, draft_id = parts

    draft = _pending_drafts.get(draft_id)
    if draft is None:
        await callback.answer("Черновик устарел или уже обработан.", show_alert=True)
        return

    if callback.from_user.id != draft.owner_user_id:
        await callback.answer("Это не ваш черновик.", show_alert=True)
        return

    if action == "discard":
        _pending_drafts.pop(draft_id, None)
        try:
            await callback.message.edit_text("🗑 Черновик отклонён, ничего не отправлено.")
        except TelegramBadRequest:
            pass
        await callback.answer()
        return

    if action == "send":
        try:
            await bot.send_message(
                chat_id=draft.chat_id,
                text=draft.draft_text,
                business_connection_id=draft.business_connection_id,
            )
        except Exception as exc:
            logger.warning("Не удалось отправить черновик собеседнику: %s", exc)
            await callback.answer(
                "⚠️ Не удалось отправить — возможно, чат неактивен более 24ч.", show_alert=True
            )
            return
        _pending_drafts.pop(draft_id, None)
        try:
            await callback.message.edit_text("✅ Отправлено собеседнику.")
        except TelegramBadRequest:
            pass
        await callback.answer()
        return

    if action == "autoreply":
        await storage.set_auto_reply(draft.business_connection_id, draft.chat_id, True)
        try:
            await bot.send_message(
                chat_id=draft.chat_id,
                text=draft.draft_text,
                business_connection_id=draft.business_connection_id,
            )
            note = "Черновик отправлен, "
        except Exception as exc:
            logger.warning("Не удалось отправить черновик при включении авто-ответа: %s", exc)
            note = "⚠️ Этот черновик отправить не удалось, но "
        _pending_drafts.pop(draft_id, None)
        try:
            await callback.message.edit_text(
                f"{note}авто-ответ для этого чата включён ✅\n\nВыключить: команда /business."
            )
        except TelegramBadRequest:
            pass
        await callback.answer()
        return

    await callback.answer()


@router.callback_query(F.data.startswith("biz_autooff:"))
async def cb_business_autoreply_off(callback: CallbackQuery) -> None:
    parts = (callback.data or "").split(":", 1)
    if len(parts) != 2:
        await callback.answer()
        return
    try:
        chat_id = int(parts[1])
    except ValueError:
        await callback.answer()
        return

    owner_user_id = callback.from_user.id
    connections = await storage.get_business_connections_for_owner(owner_user_id)
    for conn in connections:
        await storage.set_auto_reply(conn["business_connection_id"], chat_id, False)

    await callback.answer("Авто-ответ выключен ✅")
    connections_chats: list[dict] = []
    for conn in connections:
        connections_chats.extend(await storage.get_auto_reply_chats(conn["business_connection_id"]))
    try:
        if connections_chats:
            await callback.message.edit_text(
                "🔁 <b>Чаты с включённым авто-ответом:</b>",
                parse_mode="HTML",
                reply_markup=business_autoreply_chats_keyboard(connections_chats),
            )
        else:
            await callback.message.edit_text("🔁 Авто-ответ сейчас не включён ни в одном чате.")
    except TelegramBadRequest:
        pass


@router.message(Command("business"))
async def cmd_business_status(message: Message) -> None:
    """Корень бизнес-меню Secretary Mode: факты, ключевые слова, авто-ответ, статус подключений."""
    owner_user_id = message.from_user.id
    connections = await storage.get_business_connections_for_owner(owner_user_id)
    if not connections:
        await message.answer(
            "🔌 <b>Secretary Mode пока не подключён.</b>\n\n"
            "Подключить: Настройки Telegram → Telegram Business → Chatbots → выбрать этого бота, "
            "выдать право «Read and reply».",
            parse_mode="HTML",
        )
        return

    facts = await storage.get_business_facts(owner_user_id)
    rules = await storage.get_business_keyword_rules(owner_user_id)
    autoreply_chats: list[dict] = []
    for c in connections:
        autoreply_chats.extend(await storage.get_auto_reply_chats(c["business_connection_id"]))

    await message.answer(
        _render_business_menu_text(),
        parse_mode="HTML",
        reply_markup=business_menu_keyboard(len(facts), len(rules), len(autoreply_chats)),
    )


@router.callback_query(F.data.startswith("bizmenu:"))
async def cb_business_menu(callback: CallbackQuery) -> None:
    """Навигация по разделам бизнес-меню (аналог главного /menu, но для Secretary Mode)."""
    owner_user_id = callback.from_user.id
    section = (callback.data or "").split(":", 1)[1]

    if section == "main":
        facts = await storage.get_business_facts(owner_user_id)
        rules = await storage.get_business_keyword_rules(owner_user_id)
        connections = await storage.get_business_connections_for_owner(owner_user_id)
        autoreply_chats: list[dict] = []
        for c in connections:
            autoreply_chats.extend(await storage.get_auto_reply_chats(c["business_connection_id"]))
        text, markup = _render_business_menu_text(), business_menu_keyboard(
            len(facts), len(rules), len(autoreply_chats)
        )
    elif section == "facts":
        facts = await storage.get_business_facts(owner_user_id)
        text, markup = _render_business_facts_text(facts), business_facts_keyboard(facts)
    elif section == "keywords":
        rules = await storage.get_business_keyword_rules(owner_user_id)
        text, markup = _render_business_keywords_text(rules), business_keywords_keyboard(rules)
    elif section == "chats":
        connections = await storage.get_business_connections_for_owner(owner_user_id)
        chats: list[dict] = []
        for c in connections:
            chats.extend(await storage.get_auto_reply_chats(c["business_connection_id"]))
        if chats:
            text, markup = "🔁 <b>Чаты с включённым авто-ответом:</b>", business_autoreply_chats_keyboard(chats)
        else:
            text, markup = "🔁 Авто-ответ пока нигде не включён.", business_menu_back_keyboard()
    elif section == "status":
        text, markup = await _render_connections_status_text(owner_user_id), business_menu_back_keyboard()
    else:
        await callback.answer()
        return

    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("biz_fact_del:"))
async def cb_business_fact_delete(callback: CallbackQuery) -> None:
    owner_user_id = callback.from_user.id
    try:
        fact_id = int((callback.data or "").split(":", 1)[1])
    except ValueError:
        await callback.answer()
        return

    deleted = await storage.delete_business_fact_by_id(fact_id, owner_user_id)
    await callback.answer("Удалено ✅" if deleted else "Не найдено")

    facts = await storage.get_business_facts(owner_user_id)
    try:
        await callback.message.edit_text(
            _render_business_facts_text(facts), parse_mode="HTML",
            reply_markup=business_facts_keyboard(facts),
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("biz_kw_del:"))
async def cb_business_keyword_delete(callback: CallbackQuery) -> None:
    owner_user_id = callback.from_user.id
    try:
        rule_id = int((callback.data or "").split(":", 1)[1])
    except ValueError:
        await callback.answer()
        return

    deleted = await storage.delete_business_keyword_rule_by_id(rule_id, owner_user_id)
    await callback.answer("Удалено ✅" if deleted else "Не найдено")

    rules = await storage.get_business_keyword_rules(owner_user_id)
    try:
        await callback.message.edit_text(
            _render_business_keywords_text(rules), parse_mode="HTML",
            reply_markup=business_keywords_keyboard(rules),
        )
    except TelegramBadRequest:
        pass


@router.message(Command("bizfact"))
async def cmd_bizfact(message: Message) -> None:
    """Карточки фактов о бизнесе: /bizfact Ключ = Значение, /bizfact del Ключ."""
    owner_user_id = message.from_user.id
    args = message.text.split(maxsplit=1)

    if len(args) == 1:
        facts = await storage.get_business_facts(owner_user_id)
        await message.answer(
            _render_business_facts_text(facts), parse_mode="HTML",
            reply_markup=business_facts_keyboard(facts),
        )
        return

    raw_arg = args[1].strip()

    if raw_arg.lower().startswith(("del ", "delete ", "удалить ")):
        fact_key = raw_arg.split(maxsplit=1)[1].strip()
        deleted = await storage.delete_business_fact(owner_user_id, fact_key)
        if deleted:
            await message.answer(f"Факт «{html.escape(fact_key)}» удалён ✅", parse_mode="HTML")
        else:
            await message.answer(f"Факт «{html.escape(fact_key)}» не найден ❌", parse_mode="HTML")
        return

    for prefix in ("edit ", "add ", "изменить ", "добавить "):
        if raw_arg.lower().startswith(prefix):
            raw_arg = raw_arg[len(prefix):].strip()
            break

    if "=" not in raw_arg:
        await message.answer(
            "Формат: <code>/bizfact Ключ = Значение</code>\n"
            "Например: <code>/bizfact Часы работы = Пн-Пт 9:00-18:00</code>",
            parse_mode="HTML",
        )
        return

    left, value = raw_arg.split("=", 1)
    fact_key, fact_value = left.strip(), value.strip()
    if not fact_key or not fact_value:
        await message.answer("И ключ, и значение должны быть непустыми.")
        return

    saved = await storage.save_business_fact(owner_user_id, fact_key, fact_value)
    if saved:
        await message.answer(f"Факт «<b>{html.escape(fact_key)}</b>» сохранён ✅", parse_mode="HTML")
    else:
        await message.answer(
            f"⚠️ Достигнут лимит в {MAX_BUSINESS_FACTS_PER_OWNER} фактов. "
            "Удалите что-то ненужное: /bizfact del Ключ или кнопкой в /business."
        )


_KEYWORD_TYPE_ALIASES = {
    "template": "template", "шаблон": "template", "шаблонный": "template",
    "hint": "hint", "подсказка": "hint", "намек": "hint", "намёк": "hint",
}


@router.message(Command("bizkeyword"))
async def cmd_bizkeyword(message: Message) -> None:
    """Правила по ключевым словам: /bizkeyword template|hint Слово = Текст, /bizkeyword del Слово."""
    owner_user_id = message.from_user.id
    args = message.text.split(maxsplit=1)

    if len(args) == 1:
        rules = await storage.get_business_keyword_rules(owner_user_id)
        await message.answer(
            _render_business_keywords_text(rules), parse_mode="HTML",
            reply_markup=business_keywords_keyboard(rules),
        )
        return

    raw_arg = args[1].strip()

    if raw_arg.lower().startswith(("del ", "delete ", "удалить ")):
        keyword = raw_arg.split(maxsplit=1)[1].strip()
        deleted = await storage.delete_business_keyword_rule(owner_user_id, keyword)
        if deleted:
            await message.answer(f"Правило «{html.escape(keyword)}» удалено ✅", parse_mode="HTML")
        else:
            await message.answer(f"Правило «{html.escape(keyword)}» не найдено ❌", parse_mode="HTML")
        return

    tokens = raw_arg.split(maxsplit=1)
    rule_type = _KEYWORD_TYPE_ALIASES.get(tokens[0].lower()) if tokens else None

    if rule_type is None or len(tokens) < 2 or "=" not in tokens[1]:
        await message.answer(
            "Формат: <code>/bizkeyword template|hint Слово = Текст</code>\n\n"
            "• <b>template</b> — готовый ответ отправляется сразу, без Gemini\n"
            "• <b>hint</b> — подсказка для Gemini, ответ формулируется живьём\n\n"
            "Например:\n"
            "<code>/bizkeyword template цена = Актуальные цены на сайте example.com</code>\n"
            "<code>/bizkeyword hint жалоба = Отвечай с эмпатией, предложи скидку 10%</code>",
            parse_mode="HTML",
        )
        return

    left, content = tokens[1].split("=", 1)
    keyword, content = left.strip(), content.strip()
    if not keyword or not content:
        await message.answer("И ключевое слово, и текст должны быть непустыми.")
        return

    saved = await storage.save_business_keyword_rule(owner_user_id, keyword, rule_type, content)
    if saved:
        badge = "шаблон" if rule_type == "template" else "подсказка"
        await message.answer(f"Правило «<b>{html.escape(keyword)}</b>» ({badge}) сохранено ✅", parse_mode="HTML")
    else:
        await message.answer(
            f"⚠️ Достигнут лимит в {MAX_BUSINESS_KEYWORD_RULES_PER_OWNER} правил. "
            "Удалите что-то ненужное: /bizkeyword del Слово или кнопкой в /business."
        )
