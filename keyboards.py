from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import settings


def main_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    """Главное меню бота со всеми разделами."""
    buttons = [
        [InlineKeyboardButton(text="🤖 Модель Gemini", callback_data="menu:model")],
        [InlineKeyboardButton(text="🎙 Настройки озвучки (TTS)", callback_data="menu:tts")],
        [InlineKeyboardButton(text="⚙️ Параметры чата и история", callback_data="menu:settings")],
        [InlineKeyboardButton(text="📝 Системный промпт", callback_data="menu:prompt")],
        [InlineKeyboardButton(text="📊 Лимиты запросов", callback_data="menu:limits")],
    ]
    if is_admin:
        buttons.append([InlineKeyboardButton(text="👑 Панель администратора", callback_data="menu:admin")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def models_keyboard(current_model: str) -> InlineKeyboardMarkup:
    """Меню выбора основной модели генерации текста/диалога."""
    buttons = [
        [
            InlineKeyboardButton(
                text=f"✅ {model}" if model == current_model else model,
                callback_data=f"set_model:{model}",
            )
        ]
        for model in settings.available_models
    ]
    buttons.append([InlineKeyboardButton(text="◀️ Назад в меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def tts_menu_keyboard(
    current_tts_model: str, current_voice: str, voice_mode: bool
) -> InlineKeyboardMarkup:
    """Меню настроек TTS и голоса."""
    voice_label = "🎙 Голосовые ответы: вкл" if voice_mode else "🎙 Голосовые ответы: выкл"
    buttons = [
        [InlineKeyboardButton(text=f"Модель TTS: {current_tts_model}", callback_data="menu:tts_models")],
        [InlineKeyboardButton(text=f"Голос: {current_voice}", callback_data="menu:tts_voices")],
        [InlineKeyboardButton(text=voice_label, callback_data="toggle_voice_tts")],
        [InlineKeyboardButton(text="◀️ Назад в главное меню", callback_data="menu:main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def tts_models_keyboard(current_tts_model: str) -> InlineKeyboardMarkup:
    """Меню выбора TTS модели."""
    buttons = [
        [
            InlineKeyboardButton(
                text=f"✅ {model}" if model == current_tts_model else model,
                callback_data=f"set_tts_model:{model}",
            )
        ]
        for model in settings.available_tts_models
    ]
    buttons.append([InlineKeyboardButton(text="◀️ Назад к озвучке", callback_data="menu:tts")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def tts_voices_keyboard(current_voice: str) -> InlineKeyboardMarkup:
    """Меню выбора голоса озвучки."""
    buttons = []
    row = []
    for voice in settings.available_voices:
        label = f"✅ {voice}" if voice == current_voice else voice
        row.append(InlineKeyboardButton(text=label, callback_data=f"set_tts_voice:{voice}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="◀️ Назад к озвучке", callback_data="menu:tts")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def settings_keyboard(rich_mode: bool, voice_mode: bool = False) -> InlineKeyboardMarkup:
    """Параметры чата: rich-режим, голосовые ответы, очистка."""
    rich_label = "🎨 Rich-режим: вкл" if rich_mode else "🎨 Rich-режим: выкл"
    voice_label = "🎙 Голосовые ответы: вкл" if voice_mode else "🎙 Голосовые ответы: выкл"
    buttons = [
        [InlineKeyboardButton(text=rich_label, callback_data="toggle_rich")],
        [InlineKeyboardButton(text=voice_label, callback_data="toggle_voice")],
        [InlineKeyboardButton(text="🗑 Очистить историю диалога", callback_data="clear_history")],
        [InlineKeyboardButton(text="◀️ Назад в главное меню", callback_data="menu:main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def clear_history_chats_keyboard(user_chats: list[dict]) -> InlineKeyboardMarkup:
    """Клавиатура со списком чатов, где есть сохранённая история."""
    buttons = []
    total_messages = sum(c.get("message_count", 0) for c in user_chats)

    for chat in user_chats:
        chat_id = chat["chat_id"]
        title = chat.get("chat_title", f"Чат {chat_id}")
        count = chat.get("message_count", 0)
        chat_type = chat.get("chat_type", "private")
        icon = "💬" if chat_type == "private" else "👥"

        title_short = (title[:24] + "…") if len(title) > 25 else title
        btn_text = f"🗑 {icon} {title_short} ({count} репл.)"
        buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"clear_chat:{chat_id}")])

    if total_messages > 0:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"💥 Очистить во ВСЕХ чатах ({total_messages} репл.)",
                    callback_data="clear_chat:all",
                )
            ]
        )

    buttons.append([InlineKeyboardButton(text="◀️ Назад к параметрам", callback_data="menu:settings")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def prompt_keyboard() -> InlineKeyboardMarkup:
    """Меню системного промпта Stand-режима."""
    buttons = [
        [InlineKeyboardButton(text="🎭 Промпт Режима Аватара (/q)", callback_data="menu:qprompt")],
        [InlineKeyboardButton(text="🔄 Сбросить промпт на дефолтный", callback_data="reset_prompt")],
        [InlineKeyboardButton(text="◀️ Назад в главное меню", callback_data="menu:main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def qprompt_keyboard() -> InlineKeyboardMarkup:
    """Меню системного промпта Режима Аватара (/q)."""
    buttons = [
        [InlineKeyboardButton(text="🔄 Сбросить /q промпт на дефолтный", callback_data="reset_qprompt")],
        [InlineKeyboardButton(text="🥊 Промпт Stand-режима", callback_data="menu:prompt")],
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def limits_keyboard() -> InlineKeyboardMarkup:
    """Меню лимитов."""
    buttons = [
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_limits")],
        [InlineKeyboardButton(text="◀️ Назад в главное меню", callback_data="menu:main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_panel_keyboard(whitelist_enabled: bool) -> InlineKeyboardMarkup:
    """Главная панель администратора."""
    wl_label = "🛡 Белый список: ВКЛ ✅" if whitelist_enabled else "🛡 Белый список: ВЫКЛ ❌"
    buttons = [
        [InlineKeyboardButton(text="👥 Список разрешённых юзеров", callback_data="admin:users")],
        [InlineKeyboardButton(text="➕ Добавить пользователя", callback_data="admin:add_user_hint")],
        [InlineKeyboardButton(text=wl_label, callback_data="admin:toggle_whitelist")],
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_users_keyboard(users: list[dict]) -> InlineKeyboardMarkup:
    """Клавиатура со списком разрешённых пользователей."""
    buttons = []
    for u in users:
        uid = u["user_id"]
        name = u.get("username") or f"ID {uid}"
        role_icon = "👑 " if u.get("is_admin") else "👤 "
        display_name = f"{role_icon}{name} ({uid})"
        if len(display_name) > 28:
            display_name = display_name[:27] + "…"

        buttons.append(
            [
                InlineKeyboardButton(text=display_name, callback_data=f"admin:user_info:{uid}"),
                InlineKeyboardButton(text="❌ Удалить", callback_data=f"admin:del_user:{uid}"),
            ]
        )

    buttons.append([InlineKeyboardButton(text="◀️ Назад в админку", callback_data="menu:admin")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


