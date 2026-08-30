import html
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import settings
from keyboards import (
    persona_edit_keyboard,
    persona_view_keyboard,
    personas_menu_keyboard,
    prompt_keyboard,
    qprompt_keyboard,
    stand_prompt_edit_keyboard,
    stand_prompt_view_keyboard,
    stand_prompts_menu_keyboard,
)
from handlers.common import (
    _render_personas_menu_text,
    _render_prompt_menu_text,
    _render_qprompt_menu_text,
    _render_stand_prompts_menu_text,
    storage,
)

logger = logging.getLogger(__name__)

router = Router()


# --- Команды управления промптами и личностями ---

@router.message(Command("prompts"))
async def cmd_prompts(message: Message) -> None:
    """Каталог пресетов и сохранённых промптов Stand-режима."""
    user_id = message.from_user.id
    state = await storage.get(user_id, chat_id=message.chat.id)
    presets = await storage.get_all_stand_presets(user_id)
    current_prompt = state.system_prompt or settings.system_prompt or ""
    await message.answer(
        _render_stand_prompts_menu_text(state, presets),
        parse_mode="HTML",
        reply_markup=stand_prompts_menu_keyboard(presets, current_prompt),
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

    raw_arg = args[1].strip()
    if raw_arg.lower() in ("reset", "clear", "default", "сброс", "дефолт"):
        await storage.set_system_prompt(user_id, "")
        await message.answer("Системный промпт Stand сброшен к значению по умолчанию ✅")
        return

    # Создание или редактирование: /prompt edit Имя = Текст или /prompt Имя = Текст
    if "=" in raw_arg:
        left, p_text = raw_arg.split("=", 1)
        p_name = left.strip()
        for prefix in ("add ", "save ", "edit ", "изменить ", "сохранить ", "добавить ", "ред "):
            if p_name.lower().startswith(prefix):
                p_name = p_name[len(prefix):].strip()
                break
        p_text = p_text.strip()
        if p_name and p_text:
            await storage.save_user_prompt(user_id, name=p_name, prompt=p_text, mode="main")
            await storage.set_system_prompt(user_id, p_text)
            await message.answer(f"Stand-промпт <b>«{html.escape(p_name)}»</b> сохранён и активирован! 🥊", parse_mode="HTML")
            return

    # Проверяем удаление: /prompt del Имя или /prompt delete Имя
    if raw_arg.lower().startswith(("del ", "delete ", "rm ", "удалить ")):
        p_name = raw_arg.split(maxsplit=1)[1].strip()
        deleted = await storage.delete_user_prompt_by_name(user_id, name=p_name, mode="main")
        if deleted:
            await message.answer(f"Промпт <b>«{html.escape(p_name)}»</b> удалён ✅", parse_mode="HTML")
        else:
            await message.answer(f"Сохранённый промпт <b>«{html.escape(p_name)}»</b> не найден ❌", parse_mode="HTML")
        return

    # Проверяем переключение по имени пресета
    matched = await storage.find_stand_preset_by_name_or_id(user_id, raw_arg)
    if matched:
        await storage.set_system_prompt(user_id, matched["prompt"])
        title = matched.get("title") or matched["name"]
        await message.answer(f"Активирован Stand-промпт: <b>{html.escape(title)}</b> ✅", parse_mode="HTML")
        return

    # Иначе сохраняем как произвольный промпт
    await storage.set_system_prompt(user_id, raw_arg)
    await message.answer("Индивидуальный system prompt сохранён в базе данных ✅")


@router.message(Command("avatar", "avatars", "persona", "personas", "личность", "личности", "аватар", "аватары"))
async def cmd_avatar(message: Message) -> None:
    """Управление Личностями Аватара (/q)."""
    user_id = message.from_user.id
    state = await storage.get(user_id, chat_id=message.chat.id)
    args = message.text.split(maxsplit=1)

    if len(args) == 1:
        personas = await storage.get_all_personas(user_id)
        pinned_ids = await storage.get_pinned_persona_ids(user_id)
        current_prompt = state.quick_prompt or settings.quick_prompt
        await message.answer(
            _render_personas_menu_text(state, personas),
            parse_mode="HTML",
            reply_markup=personas_menu_keyboard(personas, current_prompt, pinned_ids),
        )
        return

    raw_arg = args[1].strip()
    if raw_arg.lower() in ("reset", "clear", "default", "сброс", "дефолт"):
        await storage.set_quick_prompt(user_id, "")
        await message.answer("Личность Аватара сброшена к дефолтному суфлёру ✅")
        return

    # Создание или редактирование: /avatar edit Имя = Текст или /avatar Имя = Текст
    if "=" in raw_arg:
        left, p_text = raw_arg.split("=", 1)
        p_name = left.strip()
        for prefix in ("add ", "save ", "edit ", "изменить ", "сохранить ", "добавить ", "ред "):
            if p_name.lower().startswith(prefix):
                p_name = p_name[len(prefix):].strip()
                break
        p_text = p_text.strip()
        if p_name and p_text:
            await storage.save_user_prompt(user_id, name=p_name, prompt=p_text, mode="quick")
            await storage.set_quick_prompt(user_id, p_text)
            await message.answer(f"Личность Аватара <b>«{html.escape(p_name)}»</b> сохранена и активирована! 🎭", parse_mode="HTML")
            return

    # Удаление личности: /avatar del Имя
    if raw_arg.lower().startswith(("del ", "delete ", "rm ", "удалить ")):
        p_name = raw_arg.split(maxsplit=1)[1].strip()
        deleted = await storage.delete_user_prompt_by_name(user_id, name=p_name, mode="quick")
        if deleted:
            await message.answer(f"Личность <b>«{html.escape(p_name)}»</b> удалена ✅", parse_mode="HTML")
        else:
            await message.answer(f"Пользовательская личность <b>«{html.escape(p_name)}»</b> не найдена ❌", parse_mode="HTML")
        return

    # Переключение по имени личности
    matched = await storage.find_persona_by_name_or_id(user_id, raw_arg)
    if matched:
        await storage.set_quick_prompt(user_id, matched["prompt"])
        title = matched.get("title") or matched["name"]
        await message.answer(f"Активирована личность: <b>{html.escape(title)}</b> 🎭", parse_mode="HTML")
        return

    await storage.set_quick_prompt(user_id, raw_arg)
    await message.answer("Индивидуальный промпт для Режима Аватара сохранён ✅")


@router.message(Command("qprompt", "prompt_q"))
async def cmd_qprompt(message: Message) -> None:
    user_id = message.from_user.id
    state = await storage.get(user_id, chat_id=message.chat.id)
    args = message.text.split(maxsplit=1)

    if len(args) == 1:
        await message.answer(
            _render_qprompt_menu_text(state),
            parse_mode="HTML",
            reply_markup=qprompt_keyboard(),
        )
        return

    raw_arg = args[1].strip()
    if raw_arg.lower() in ("reset", "clear", "default", "дефолт", "сброс"):
        await storage.set_quick_prompt(user_id, "")
        await message.answer("Системный промпт для режима /q сброшен к значению по умолчанию ✅")
        return

    # Если пользователь ввёл команду вида /qprompt edit Имя = Текст или /qprompt Имя = Текст
    if "=" in raw_arg:
        left, p_text = raw_arg.split("=", 1)
        p_name = left.strip()
        for prefix in ("add ", "save ", "edit ", "изменить ", "сохранить ", "добавить ", "ред "):
            if p_name.lower().startswith(prefix):
                p_name = p_name[len(prefix):].strip()
                break
        p_text = p_text.strip()
        if p_name and p_text:
            await storage.save_user_prompt(user_id, name=p_name, prompt=p_text, mode="quick")
            await storage.set_quick_prompt(user_id, p_text)
            await message.answer(f"Личность <b>«{html.escape(p_name)}»</b> сохранена и активирована! 🎭", parse_mode="HTML")
            return

    matched = await storage.find_persona_by_name_or_id(user_id, raw_arg)
    if matched:
        await storage.set_quick_prompt(user_id, matched["prompt"])
        title = matched.get("title") or matched["name"]
        await message.answer(f"Активирована личность: <b>{html.escape(title)}</b> 🎭", parse_mode="HTML")
        return

    await storage.set_quick_prompt(user_id, raw_arg)
    await message.answer("Индивидуальный промпт для режима /q сохранён в базе данных ✅")


# --- Callback-хэндлеры Stand пресетов и Личностей Аватара ---

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


@router.callback_query(F.data == "menu:qprompt")
async def cb_menu_qprompt(callback: CallbackQuery) -> None:
    chat_id = callback.message.chat.id if callback.message else callback.from_user.id
    state = await storage.get(callback.from_user.id, chat_id=chat_id)
    try:
        await callback.message.edit_text(
            _render_qprompt_menu_text(state),
            parse_mode="HTML",
            reply_markup=qprompt_keyboard(),
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data == "reset_qprompt")
async def cb_reset_qprompt(callback: CallbackQuery) -> None:
    chat_id = callback.message.chat.id if callback.message else callback.from_user.id
    await storage.set_quick_prompt(callback.from_user.id, "")
    state = await storage.get(callback.from_user.id, chat_id=chat_id)
    try:
        await callback.message.edit_text(
            _render_qprompt_menu_text(state),
            parse_mode="HTML",
            reply_markup=qprompt_keyboard(),
        )
    except TelegramBadRequest:
        pass
    await callback.answer("Промпт Аватара сброшен к дефолту ✅")


@router.callback_query(F.data == "menu:personas")
async def cb_menu_personas(callback: CallbackQuery) -> None:
    chat_id = callback.message.chat.id if callback.message else callback.from_user.id
    state = await storage.get(callback.from_user.id, chat_id=chat_id)
    personas = await storage.get_all_personas(callback.from_user.id)
    pinned_ids = await storage.get_pinned_persona_ids(callback.from_user.id)
    current_prompt = state.quick_prompt or settings.quick_prompt
    try:
        await callback.message.edit_text(
            _render_personas_menu_text(state, personas),
            parse_mode="HTML",
            reply_markup=personas_menu_keyboard(personas, current_prompt, pinned_ids),
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("persona_info:"))
async def cb_persona_info(callback: CallbackQuery) -> None:
    persona_id = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id if callback.message else user_id
    state = await storage.get(user_id, chat_id=chat_id)
    persona = await storage.find_persona_by_name_or_id(user_id, persona_id)
    if not persona:
        await callback.answer("Личность не найдена", show_alert=True)
        return

    current_prompt = state.quick_prompt or settings.quick_prompt
    is_active = (persona["prompt"].strip() == current_prompt.strip())
    is_builtin = persona.get("is_builtin", False)
    is_pinned = await storage.is_persona_pinned(user_id, persona["id"])
    title = persona.get("title") or f"🎭 {persona['name']}"

    text = (
        f"<b>{html.escape(title)}</b>\n\n"
        f"Промпт личности:\n<code>{html.escape(persona['prompt'])}</code>\n\n"
        f"Статус: <b>{'✅ Активна' if is_active else '⚪️ Не активна'}</b>\n"
        f"Инлайн-меню: <b>{'⭐ Закреплена в избранном' if is_pinned else '⚪️ Не закреплена'}</b>"
    )
    try:
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=persona_view_keyboard(persona["id"], is_active, is_builtin, is_pinned),
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("persona_pin:"))
async def cb_persona_pin(callback: CallbackQuery) -> None:
    persona_id = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id if callback.message else user_id
    state = await storage.get(user_id, chat_id=chat_id)
    persona = await storage.find_persona_by_name_or_id(user_id, persona_id)
    if not persona:
        await callback.answer("Личность не найдена", show_alert=True)
        return

    is_now_pinned = await storage.toggle_pinned_persona(user_id, persona_id)
    status_msg = "⭐ Личность закреплена в избранном инлайн-меню!" if is_now_pinned else "☆ Личность откреплена от инлайн-меню."
    await callback.answer(status_msg, show_alert=False)

    current_prompt = state.quick_prompt or settings.quick_prompt
    is_active = (persona["prompt"].strip() == current_prompt.strip())
    is_builtin = persona.get("is_builtin", False)
    title = persona.get("title") or f"🎭 {persona['name']}"

    text = (
        f"<b>{html.escape(title)}</b>\n\n"
        f"Промпт личности:\n<code>{html.escape(persona['prompt'])}</code>\n\n"
        f"Статус: <b>{'✅ Активна' if is_active else '⚪️ Не активна'}</b>\n"
        f"Инлайн-меню: <b>{'⭐ Закреплена в избранном' if is_now_pinned else '⚪️ Не закреплена'}</b>"
    )
    try:
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=persona_view_keyboard(persona["id"], is_active, is_builtin, is_now_pinned),
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("persona_set:"))
async def cb_persona_set(callback: CallbackQuery) -> None:
    persona_id = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    persona = await storage.find_persona_by_name_or_id(user_id, persona_id)
    if not persona:
        await callback.answer("Личность не найдена", show_alert=True)
        return

    await storage.set_quick_prompt(user_id, persona["prompt"])
    title = persona.get("title") or persona["name"]
    await callback.answer(f"Личность «{title}» активирована! 🎭", show_alert=True)

    chat_id = callback.message.chat.id if callback.message else user_id
    state = await storage.get(user_id, chat_id=chat_id)
    personas = await storage.get_all_personas(user_id)
    pinned_ids = await storage.get_pinned_persona_ids(user_id)
    try:
        await callback.message.edit_text(
            _render_personas_menu_text(state, personas),
            parse_mode="HTML",
            reply_markup=personas_menu_keyboard(personas, persona["prompt"], pinned_ids),
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("persona_del:"))
async def cb_persona_del(callback: CallbackQuery) -> None:
    persona_id = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    try:
        p_id_int = int(persona_id)
        deleted = await storage.delete_user_prompt(user_id, p_id_int)
    except ValueError:
        deleted = False

    if deleted:
        await callback.answer("Личность удалена ✅")
    else:
        await callback.answer("Не удалось удалить личность ❌", show_alert=True)

    chat_id = callback.message.chat.id if callback.message else user_id
    state = await storage.get(user_id, chat_id=chat_id)
    personas = await storage.get_all_personas(user_id)
    pinned_ids = await storage.get_pinned_persona_ids(user_id)
    current_prompt = state.quick_prompt or settings.quick_prompt
    try:
        await callback.message.edit_text(
            _render_personas_menu_text(state, personas),
            parse_mode="HTML",
            reply_markup=personas_menu_keyboard(personas, current_prompt, pinned_ids),
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("persona_edit:"))
async def cb_persona_edit(callback: CallbackQuery) -> None:
    persona_id = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    persona = await storage.find_persona_by_name_or_id(user_id, persona_id)
    if not persona:
        await callback.answer("Личность не найдена", show_alert=True)
        return

    p_name = persona["name"]
    p_prompt = persona["prompt"]
    text = (
        f"✏️ <b>Редактирование личности «{html.escape(p_name)}»</b>:\n\n"
        "Скопируйте команду ниже (нажмите на неё), измените текст и отправьте боту в этот чат:\n\n"
        f"<code>/avatar edit {html.escape(p_name)} = {html.escape(p_prompt)}</code>\n\n"
        "<i>После отправки команды личность обновится в базе данных и сразу станет активной.</i>"
    )
    try:
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=persona_edit_keyboard(persona["id"]),
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data == "persona:add_hint")
async def cb_persona_add_hint(callback: CallbackQuery) -> None:
    await callback.answer(
        "Чтобы создать/изменить личность, отправьте команду:\n/avatar edit Имя = Описание стиля",
        show_alert=True,
    )


@router.callback_query(F.data == "persona:guide")
async def cb_persona_guide(callback: CallbackQuery) -> None:
    text = (
        "💡 <b>Как правильно составлять Личности Аватара</b>\n\n"
        "Личность Аватара — это <b>манера речи ваших сообщений</b> собеседникам в чатах, а не то, как бот общается лично с вами.\n\n"
        "❌ <b>Как НЕ надо писать:</b>\n"
        "• <i>«Отвечай мне как...»</i>\n"
        "• <i>«Слушайся моих указаний...»</i>\n"
        "• <i>«Подтверждай выполнение задач...»</i>\n"
        "⚠️ <i>Слова «Отвечай мне» заставляют ИИ думать, что вы отдаёте ему поручение, а не пишете черновик для чата.</i>\n\n"
        "✅ <b>Как ПРАВИЛЬНО писать промпт личности:</b>\n"
        "Описывайте сам стиль текста сообщений, тон и оформление:\n"
        "• <i>«Пиши как лучший друг: на 'ты', с юмором, дружелюбно, кратко и без официоза.»</i>\n"
        "• <i>«Общайся строго по делу, в деловом тоне, конструктивно и на 'вы'.»</i>\n"
        "• <i>«Отвечай с тонкой иронией и сарказмом, но без токсичности.»</i>\n\n"
        "📝 <b>Создать или изменить личность:</b>\n"
        "<code>/avatar edit Имя = Описание стиля</code>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Создать личность", callback_data="persona:add_hint")],
            [InlineKeyboardButton(text="◀️ Назад к списку личностей", callback_data="menu:personas")],
        ]
    )
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data == "menu:prompts")
async def cb_menu_prompts(callback: CallbackQuery) -> None:
    chat_id = callback.message.chat.id if callback.message else callback.from_user.id
    state = await storage.get(callback.from_user.id, chat_id=chat_id)
    presets = await storage.get_all_stand_presets(callback.from_user.id)
    current_prompt = state.system_prompt or settings.system_prompt or ""
    try:
        await callback.message.edit_text(
            _render_stand_prompts_menu_text(state, presets),
            parse_mode="HTML",
            reply_markup=stand_prompts_menu_keyboard(presets, current_prompt),
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("stand_info:"))
async def cb_stand_info(callback: CallbackQuery) -> None:
    preset_id = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id if callback.message else user_id
    state = await storage.get(user_id, chat_id=chat_id)
    preset = await storage.find_stand_preset_by_name_or_id(user_id, preset_id)
    if not preset:
        await callback.answer("Промпт не найден", show_alert=True)
        return

    current_prompt = state.system_prompt or settings.system_prompt or ""
    is_active = (preset["prompt"].strip() == current_prompt.strip())
    is_builtin = preset.get("is_builtin", False)
    title = preset.get("title") or f"🥊 {preset['name']}"

    text = (
        f"<b>{html.escape(title)}</b>\n\n"
        f"Системный промпт:\n<code>{html.escape(preset['prompt'])}</code>\n\n"
        f"Статус: <b>{'✅ Активен' if is_active else '⚪️ Не активен'}</b>"
    )
    try:
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=stand_prompt_view_keyboard(preset["id"], is_active, is_builtin),
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("stand_set:"))
async def cb_stand_set(callback: CallbackQuery) -> None:
    preset_id = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    preset = await storage.find_stand_preset_by_name_or_id(user_id, preset_id)
    if not preset:
        await callback.answer("Промпт не найден", show_alert=True)
        return

    await storage.set_system_prompt(user_id, preset["prompt"])
    title = preset.get("title") or preset["name"]
    await callback.answer(f"Промпт «{title}» активирован! 🥊", show_alert=True)

    chat_id = callback.message.chat.id if callback.message else user_id
    state = await storage.get(user_id, chat_id=chat_id)
    presets = await storage.get_all_stand_presets(user_id)
    try:
        await callback.message.edit_text(
            _render_stand_prompts_menu_text(state, presets),
            parse_mode="HTML",
            reply_markup=stand_prompts_menu_keyboard(presets, preset["prompt"]),
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("stand_edit:"))
async def cb_stand_edit(callback: CallbackQuery) -> None:
    preset_id = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    preset = await storage.find_stand_preset_by_name_or_id(user_id, preset_id)
    if not preset:
        await callback.answer("Промпт не найден", show_alert=True)
        return

    p_name = preset["name"]
    p_prompt = preset["prompt"]
    text = (
        f"✏️ <b>Редактирование Stand-промпта «{html.escape(p_name)}»</b>:\n\n"
        "Скопируйте команду ниже (нажмите на неё), измените текст и отправьте боту в этот чат:\n\n"
        f"<code>/prompt edit {html.escape(p_name)} = {html.escape(p_prompt)}</code>\n\n"
        "<i>После отправки команды промпт обновится в базе данных и сразу станет активным.</i>"
    )
    try:
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=stand_prompt_edit_keyboard(preset["id"]),
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("stand_del:"))
async def cb_stand_del(callback: CallbackQuery) -> None:
    preset_id = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    try:
        p_id_int = int(preset_id)
        deleted = await storage.delete_user_prompt(user_id, p_id_int)
    except ValueError:
        deleted = False

    if deleted:
        await callback.answer("Промпт удален ✅")
    else:
        await callback.answer("Не удалось удалить промпт ❌", show_alert=True)

    chat_id = callback.message.chat.id if callback.message else user_id
    state = await storage.get(user_id, chat_id=chat_id)
    presets = await storage.get_all_stand_presets(user_id)
    current_prompt = state.system_prompt or settings.system_prompt or ""
    try:
        await callback.message.edit_text(
            _render_stand_prompts_menu_text(state, presets),
            parse_mode="HTML",
            reply_markup=stand_prompts_menu_keyboard(presets, current_prompt),
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "stand:add_hint")
async def cb_stand_add_hint(callback: CallbackQuery) -> None:
    await callback.answer(
        "Чтобы сохранить свой Stand-промпт, отправьте команду:\n/prompt add Имя = Текст промпта",
        show_alert=True,
    )

