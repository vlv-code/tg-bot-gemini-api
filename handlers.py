import hashlib
import html
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
)

from config import settings
from formatting import find_utf16_cut, markdown_to_chunks, split_plain_text, utf16_len
from gemini_client import GeminiError, build_gemini_client
from keyboards import models_keyboard, settings_keyboard
from locks import UserLocks
from middlewares import AccessMiddleware
from rate_limiter import RateLimiter
from storage import UserStorage

logger = logging.getLogger(__name__)

router = Router()
router.message.outer_middleware(AccessMiddleware())
router.callback_query.outer_middleware(AccessMiddleware())
router.inline_query.outer_middleware(AccessMiddleware())

storage = UserStorage(default_model=settings.default_model, max_history=settings.max_history_messages)
limiter = RateLimiter(per_minute=settings.rate_limit_per_minute, per_day=settings.rate_limit_per_day)
gemini_client = build_gemini_client(settings.gemini_api_key, settings.system_prompt)
user_locks = UserLocks()


async def _limits_line(user_id: int) -> str:
    status = await limiter.status(user_id)
    return f"📊 {status.used_minute}/{status.limit_minute} в минуту · {status.used_day}/{status.limit_day} сегодня"


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    state = await storage.get(message.from_user.id)
    await message.answer(
        "Привет! Я бот-обёртка над Gemini API.\n\n"
        f"Текущая модель: <b>{html.escape(state.model)}</b>\n\n"
        "Команды:\n"
        "/model — выбрать модель Gemini\n"
        "/settings — rich-режим и очистка истории\n"
        "/limits — сколько запросов осталось\n"
        "/prompt — посмотреть или поменять system prompt\n\n"
        "Просто напиши сообщение, чтобы начать диалог, "
        "или в любом чате набери @username_бота вопрос — сработает через inline mode.",
        parse_mode="HTML",
    )


@router.message(Command("prompt"))
async def cmd_prompt(message: Message) -> None:
    args = message.text.split(maxsplit=1)
    if len(args) == 1:
        current = gemini_client.system_prompt or "(не задан — Gemini отвечает без system instruction)"
        await message.answer(
            f"Текущий system prompt:\n\n{html.escape(current)}\n\n"
            "Чтобы поменять: /prompt текст нового промпта",
            parse_mode="HTML",
        )
        return

    gemini_client.system_prompt = args[1]
    await message.answer(
        "Готово ✅ (действует до рестарта контейнера — дефолт из SYSTEM_PROMPT в .env не менялся)"
    )


@router.message(Command("model"))
async def cmd_model(message: Message) -> None:
    state = await storage.get(message.from_user.id)
    await message.answer("Выбери модель Gemini:", reply_markup=models_keyboard(state.model))


@router.callback_query(F.data.startswith("model:"))
async def cb_model(callback: CallbackQuery) -> None:
    model = callback.data.split(":", 1)[1]
    if model not in settings.available_models:
        await callback.answer("Неизвестная модель", show_alert=True)
        return

    user_id = callback.from_user.id
    await storage.set_model(user_id, model)
    await storage.clear_history(user_id)  # история от старой модели больше не совместима с новой сессией

    try:
        await callback.message.edit_text(
            f"Модель переключена на: <b>{html.escape(model)}</b>\n(история диалога очищена)",
            parse_mode="HTML",
            reply_markup=models_keyboard(model),
        )
    except TelegramBadRequest:
        # сообщение с кнопками старше 48ч (или уже не редактируется) —
        # состояние всё равно переключено, просто не можем обновить старое сообщение
        logger.info("Не удалось отредактировать сообщение с выбором модели для user_id=%s", user_id)

    await callback.answer(f"Модель: {model}")


@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    state = await storage.get(message.from_user.id)
    await message.answer("Настройки:", reply_markup=settings_keyboard(state.rich_mode))


@router.callback_query(F.data == "toggle_rich")
async def cb_toggle_rich(callback: CallbackQuery) -> None:
    rich = await storage.toggle_rich(callback.from_user.id)
    try:
        await callback.message.edit_text("Настройки:", reply_markup=settings_keyboard(rich))
    except TelegramBadRequest:
        logger.info("Не удалось отредактировать сообщение настроек для user_id=%s", callback.from_user.id)
    await callback.answer("Rich-режим: " + ("включен" if rich else "выключен"))


@router.callback_query(F.data == "clear_history")
async def cb_clear_history(callback: CallbackQuery) -> None:
    await storage.clear_history(callback.from_user.id)
    await callback.answer("История диалога очищена ✅")


@router.message(Command("limits"))
async def cmd_limits(message: Message) -> None:
    await message.answer(await _limits_line(message.from_user.id))


@router.message(F.text & ~F.text.startswith("/"))
async def handle_chat(message: Message) -> None:
    user_id = message.from_user.id

    # Весь блок check -> Gemini -> hit — под per-user локом: без этого
    # несколько сообщений подряд от одного юзера могут параллельно пройти
    # check() как allowed=True (hit() пишется только после ответа Gemini),
    # и лимит по факту обходится всплеском сообщений. См. locks.py.
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
        await message.bot.send_chat_action(message.chat.id, "typing")

        try:
            answer = await gemini_client.ask(state.model, state.history, message.text)
        except GeminiError as exc:
            await message.answer(f"⚠️ {exc}")
            return
        except Exception:  # noqa: BLE001 — не роняем бота из-за непредвиденной ошибки SDK
            logger.exception("Unexpected error while calling Gemini API")
            await message.answer("⚠️ Непредвиденная ошибка при обращении к Gemini API.")
            return

        await limiter.hit(user_id)
        await storage.add_turn(user_id, "user", message.text)
        await storage.add_turn(user_id, "model", answer)

    if state.rich_mode:
        for chunk_text, chunk_entities in markdown_to_chunks(answer):
            try:
                await message.answer(chunk_text, entities=chunk_entities)
            except Exception:
                # entities почти никогда не ломаются (в отличие от MarkdownV2/HTML),
                # но на всякий случай подстраховываемся обычным текстом
                logger.warning("Falling back to plain text for a chunk that failed to send with entities")
                await message.answer(chunk_text)
    else:
        for chunk in split_plain_text(answer):
            await message.answer(chunk)

    await message.answer(await _limits_line(user_id))


# --- Inline mode ---------------------------------------------------------
# Работает в ЛЮБОМ чате без добавления бота туда: @botusername запрос в поле
# ввода -> выпадающий список результатов -> тап вставляет ответ как обычное
# сообщение ("via @botusername"). Требует /setinline в BotFather. У этого
# режима нет продолжения диалога в том чате (бот его не видит) и нет
# смысла тащить туда rich-режим на несколько чанков — можно вставить только
# ОДНО сообщение, поэтому длинные ответы обрезаются по UTF-16 (см.
# formatting.utf16_len) с пометкой, а не бьются на чанки как в обычном чате.
#
# Общее с обычным чатом: тот же storage (история/модель), тот же rate
# limiter и тот же per-user lock — это тот же самый юзер, просто из другого
# интерфейса, так что диалог из ЛС и из inline расходует общий лимит и
# продолжает одну и ту же историю.
INLINE_ANSWER_LIMIT = 4000  # с запасом от реального лимита Telegram-сообщения в 4096 UTF-16 юнитов


def _inline_result(result_id: str, title: str, text: str) -> InlineQueryResultArticle:
    return InlineQueryResultArticle(
        id=result_id,
        title=title,
        description=text[:120].replace("\n", " "),
        input_message_content=InputTextMessageContent(message_text=text),
    )


@router.inline_query()
async def handle_inline(query: InlineQuery) -> None:
    user_id = query.from_user.id
    text = query.query.strip()
    result_id = hashlib.sha1(f"{user_id}:{text}".encode()).hexdigest()[:32]

    if not text:
        await query.answer(
            results=[
                _inline_result(
                    "hint",
                    "Введи вопрос для Gemini",
                    f"Например: @{(await query.bot.get_me()).username} как настроить бэкап Zabbix",
                )
            ],
            cache_time=0,
            is_personal=True,
        )
        return

    async with user_locks.get(user_id):
        limit_status = await limiter.check(user_id)
        if not limit_status.allowed:
            wait_seconds = int(limit_status.retry_after) + 1
            await query.answer(
                results=[_inline_result(result_id, "⏳ Лимит запросов исчерпан", f"Попробуй снова через {wait_seconds} сек.")],
                cache_time=0,
                is_personal=True,
            )
            return

        state = await storage.get(user_id)

        try:
            answer = await gemini_client.ask(state.model, state.history, text)
        except GeminiError as exc:
            await query.answer(
                results=[_inline_result(result_id, "⚠️ Ошибка Gemini", str(exc))],
                cache_time=0,
                is_personal=True,
            )
            return
        except Exception:  # noqa: BLE001 — та же логика, что и в handle_chat
            logger.exception("Unexpected error while calling Gemini API (inline)")
            await query.answer(
                results=[_inline_result(result_id, "⚠️ Непредвиденная ошибка", "Ошибка при обращении к Gemini API.")],
                cache_time=0,
                is_personal=True,
            )
            return

        await limiter.hit(user_id)
        await storage.add_turn(user_id, "user", text)
        await storage.add_turn(user_id, "model", answer)

    display_answer = answer
    title = answer.replace("\n", " ")[:80] or "Ответ"
    if utf16_len(answer) > INLINE_ANSWER_LIMIT:
        cut = find_utf16_cut(answer, INLINE_ANSWER_LIMIT)
        display_answer = answer[:cut] + "…\n\n(ответ обрезан — длинные вопросы лучше в личку боту)"

    await query.answer(
        results=[_inline_result(result_id, title, display_answer)],
        cache_time=0,
        is_personal=True,
    )
