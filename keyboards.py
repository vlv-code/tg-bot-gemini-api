import hashlib
from typing import Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import settings
from storage import UserState

MODE_TO_CODE = {
    "stand": "s",
    "avatar": "a",
    "business": "b",
    "stt": "v",
    "tts": "t",
}

CODE_TO_MODE = {
    "s": "stand",
    "a": "avatar",
    "b": "business",
    "v": "stt",
    "t": "tts",
}


def main_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    """Главное меню бота со всеми разделами."""
    buttons = [
        [InlineKeyboardButton(text="🤖 Модели Gemini (5 режимов)", callback_data="menu:model")],
        [InlineKeyboardButton(text="🎭 Личности Аватара", callback_data="menu:personas")],
        [InlineKeyboardButton(text="🥊 Промпты Stand-режима", callback_data="menu:prompts")],
        [InlineKeyboardButton(text="💼 Бизнес-ассистент (Secretary)", callback_data="menu:business")],
        [InlineKeyboardButton(text="🎙 Настройки озвучки (TTS)", callback_data="menu:tts")],
        [InlineKeyboardButton(text="⚙️ Параметры чата и история", callback_data="menu:settings")],
        [InlineKeyboardButton(text="📊 Лимиты запросов", callback_data="menu:limits")],
        [InlineKeyboardButton(text="ℹ️ О боте и руководство", callback_data="menu:info")],
    ]
    if is_admin:
        buttons.append([InlineKeyboardButton(text="👑 Панель администратора", callback_data="menu:admin")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def info_menu_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура раздела 'О боте и руководство'."""
    buttons = [
        [
            InlineKeyboardButton(text="🎭 Личности Аватара", callback_data="menu:personas"),
            InlineKeyboardButton(text="🥊 Промпты Stand", callback_data="menu:prompts"),
        ],
        [
            InlineKeyboardButton(text="🤖 Выбрать модель", callback_data="menu:model"),
            InlineKeyboardButton(text="🎙 Настройки TTS", callback_data="menu:tts"),
        ],
        [InlineKeyboardButton(text="◀️ В главное меню", callback_data="menu:main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def models_hub_keyboard(state: UserState) -> InlineKeyboardMarkup:
    """Главный хаб управления моделями по 5 режимам."""
    stand = state.stand_model
    avatar = state.avatar_model or state.model
    business = state.business_model or state.model
    stt = state.stt_model or state.model
    tts = state.tts_model

    buttons = [
        [InlineKeyboardButton(text=f"🥊 Stand (чат): {stand}", callback_data="pick_mode_model:stand")],
        [InlineKeyboardButton(text=f"🎭 Avatar (/q): {avatar}", callback_data="pick_mode_model:avatar")],
        [InlineKeyboardButton(text=f"💼 Business (Secretary): {business}", callback_data="pick_mode_model:business")],
        [InlineKeyboardButton(text=f"🎙 Voice-to-Text (STT): {stt}", callback_data="pick_mode_model:stt")],
        [InlineKeyboardButton(text=f"🎧 Озвучка TTS: {tts}", callback_data="pick_mode_model:tts")],
        [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="menu:main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def mode_model_selector_keyboard(
    mode: str,
    current_model: str,
    available_models: list[dict],
    page: int = 1,
    page_size: int = 6,
) -> InlineKeyboardMarkup:
    """Клавиатура выбора модели для конкретного режима с защитой от лимита callback_data (64 байта) и пагинацией."""
    mode_code = MODE_TO_CODE.get(mode, mode)
    total = len(available_models)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    page_models = available_models[start_idx:end_idx]

    buttons = []
    for m in page_models:
        mid = m["model_id"]
        disp = m.get("display_name", mid)
        is_current = (mid == current_model)
        label = f"✅ {disp}" if is_current else disp

        cb = f"smm:{mode_code}:{mid}:p{page}"
        # Telegram Bot API строго ограничивает callback_data 64 байтами (UTF-8)
        if len(cb.encode("utf-8")) > 64:
            prefix = f"smm:{mode_code}:"
            suffix = f":p{page}"
            max_mid_bytes = 64 - len(prefix.encode("utf-8")) - len(suffix.encode("utf-8"))
            h = hashlib.md5(mid.encode("utf-8")).hexdigest()[:6]
            cut_len = max_mid_bytes - 7
            mid_trimmed = mid[:cut_len] + "_" + h
            cb = f"{prefix}{mid_trimmed}{suffix}"

        buttons.append([InlineKeyboardButton(text=label, callback_data=cb)])

    # Строка навигации пагинации (если моделей больше одной страницы)
    if total_pages > 1:
        prev_page = page - 1 if page > 1 else total_pages
        next_page = page + 1 if page < total_pages else 1
        nav_row = [
            InlineKeyboardButton(text="◀️ Пред.", callback_data=f"smm_page:{mode_code}:{prev_page}"),
            InlineKeyboardButton(text=f"Стр. {page}/{total_pages}", callback_data=f"smm_noop:{mode_code}:{page}"),
            InlineKeyboardButton(text="След. ▶️", callback_data=f"smm_page:{mode_code}:{next_page}"),
        ]
        buttons.append(nav_row)

    buttons.append([InlineKeyboardButton(text="◀️ Назад к режимам", callback_data="menu:model")])
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
        [InlineKeyboardButton(text="📚 Каталог промптов Stand", callback_data="menu:prompts")],
        [InlineKeyboardButton(text="🎭 Личности Аватара", callback_data="menu:personas")],
        [InlineKeyboardButton(text="🔄 Сбросить промпт на дефолтный", callback_data="reset_prompt")],
        [InlineKeyboardButton(text="◀️ Назад в главное меню", callback_data="menu:main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def qprompt_keyboard() -> InlineKeyboardMarkup:
    """Меню системного промпта Режима Аватара."""
    buttons = [
        [InlineKeyboardButton(text="🎭 Выбрать Личность Аватара", callback_data="menu:personas")],
        [InlineKeyboardButton(text="🔄 Сбросить промпт на дефолтный", callback_data="reset_qprompt")],
        [InlineKeyboardButton(text="🥊 Промпт Stand-режима", callback_data="menu:prompt")],
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def personas_menu_keyboard(
    personas: list[dict], current_prompt: str, pinned_ids: Optional[set[str]] = None
) -> InlineKeyboardMarkup:
    """Меню выбора Личностей Аватара."""
    pinned = pinned_ids or set()
    buttons = []
    # Отображаем личности кнопками
    for p in personas:
        p_name = p["name"]
        p_title = p.get("title") or f"🎭 {p_name}"
        is_active = (current_prompt.strip() == p["prompt"].strip())
        is_pinned = str(p["id"]) in pinned
        mark = "✅ " if is_active else ""
        pin_mark = "⭐ " if is_pinned else ""
        btn_text = f"{mark}{pin_mark}{p_title}"
        if len(btn_text) > 32:
            btn_text = btn_text[:31] + "…"
        buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"persona_info:{p['id']}")])

    buttons.append([
        InlineKeyboardButton(text="➕ Создать личность", callback_data="persona:add_hint"),
        InlineKeyboardButton(text="ℹ️ Памятка по стилям", callback_data="persona:guide"),
    ])
    buttons.append([
        InlineKeyboardButton(text="🔄 Сброс к дефолту", callback_data="reset_qprompt"),
        InlineKeyboardButton(text="◀️ Назад в меню", callback_data="menu:main"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def persona_view_keyboard(
    persona_id: str | int, is_active: bool, is_builtin: bool, is_pinned: bool = False
) -> InlineKeyboardMarkup:
    """Клавиатура просмотра и управления конкретной личностью."""
    buttons = []
    if not is_active:
        buttons.append([InlineKeyboardButton(text="✅ Активировать эту личность", callback_data=f"persona_set:{persona_id}")])
    
    pin_btn_text = "⭐️ В избранном инлайна (открепить)" if is_pinned else "⭐ Закрепить в инлайн-меню"
    buttons.append([InlineKeyboardButton(text=pin_btn_text, callback_data=f"persona_pin:{persona_id}")])
    buttons.append([InlineKeyboardButton(text="✏️ Изменить / Редактировать", callback_data=f"persona_edit:{persona_id}")])
    if not is_builtin:
        buttons.append([InlineKeyboardButton(text="❌ Удалить личность", callback_data=f"persona_del:{persona_id}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад к личностям", callback_data="menu:personas")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def persona_edit_keyboard(persona_id: str | int) -> InlineKeyboardMarkup:
    """Клавиатура возврата из режима редактирования личности."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад к описанию личности", callback_data=f"persona_info:{persona_id}")],
            [InlineKeyboardButton(text="🎭 К списку личностей", callback_data="menu:personas")],
        ]
    )


def stand_prompts_menu_keyboard(presets: list[dict], current_prompt: str) -> InlineKeyboardMarkup:
    """Меню пресетов и системных промптов Stand-режима."""
    buttons = []
    for p in presets:
        p_name = p["name"]
        p_title = p.get("title") or f"🥊 {p_name}"
        is_active = (current_prompt.strip() == p["prompt"].strip())
        mark = "✅ " if is_active else ""
        btn_text = f"{mark}{p_title}"
        if len(btn_text) > 32:
            btn_text = btn_text[:31] + "…"
        buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"stand_info:{p['id']}")])

    buttons.append([
        InlineKeyboardButton(text="➕ Создать промпт", callback_data="stand:add_hint"),
        InlineKeyboardButton(text="🔄 Дефолт", callback_data="reset_prompt"),
    ])
    buttons.append([InlineKeyboardButton(text="◀️ Назад в главное меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def stand_prompt_view_keyboard(preset_id: str | int, is_active: bool, is_builtin: bool) -> InlineKeyboardMarkup:
    """Клавиатура просмотра и управления Stand-пресетом."""
    buttons = []
    if not is_active:
        buttons.append([InlineKeyboardButton(text="✅ Активировать этот промпт", callback_data=f"stand_set:{preset_id}")])
    buttons.append([InlineKeyboardButton(text="✏️ Изменить / Редактировать", callback_data=f"stand_edit:{preset_id}")])
    if not is_builtin:
        buttons.append([InlineKeyboardButton(text="❌ Удалить промпт", callback_data=f"stand_del:{preset_id}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад к списку промптов", callback_data="menu:prompts")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def stand_prompt_edit_keyboard(preset_id: str | int) -> InlineKeyboardMarkup:
    """Клавиатура возврата из режима редактирования Stand-промпта."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад к описанию промпта", callback_data=f"stand_info:{preset_id}")],
            [InlineKeyboardButton(text="🥊 К каталогу промптов", callback_data="menu:prompts")],
        ]
    )


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
        [InlineKeyboardButton(text="🔄 Синхронизировать модели Google", callback_data="admin:sync_models")],
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


def business_menu_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад в бизнес-меню", callback_data="bizmenu:main")]]
    )


def business_menu_keyboard(facts_count: int, rules_count: int, autoreply_count: int) -> InlineKeyboardMarkup:
    """Корневое меню Secretary Mode — отдельное от главного /menu, командой /business."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"📋 Факты о бизнесе ({facts_count})", callback_data="bizmenu:facts")],
            [InlineKeyboardButton(text=f"🔑 Ключевые слова ({rules_count})", callback_data="bizmenu:keywords")],
            [InlineKeyboardButton(text=f"🔁 Авто-ответ по чатам ({autoreply_count})", callback_data="bizmenu:chats")],
            [InlineKeyboardButton(text="🔌 Статус подключений", callback_data="bizmenu:status")],
            [InlineKeyboardButton(text="◀️ В главное меню", callback_data="menu:main")],
        ]
    )


def business_facts_keyboard(facts: list[dict]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=f"❌ {f['fact_key'][:35]}", callback_data=f"biz_fact_del:{f['id']}")]
        for f in facts
    ]
    buttons.append([InlineKeyboardButton(text="◀️ Назад в бизнес-меню", callback_data="bizmenu:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def business_keywords_keyboard(rules: list[dict]) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=f"❌ [{'📄' if r['rule_type'] == 'template' else '💡'}] {r['keyword'][:30]}",
                callback_data=f"biz_kw_del:{r['id']}",
            )
        ]
        for r in rules
    ]
    buttons.append([InlineKeyboardButton(text="◀️ Назад в бизнес-меню", callback_data="bizmenu:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def business_draft_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    """Клавиатура подтверждения черновика Secretary Mode — уходит владельцу в личку с ботом."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Отправить", callback_data=f"biz_draft:send:{draft_id}"),
            ],
            [
                InlineKeyboardButton(text="🔄 Ещё вариант", callback_data=f"biz_draft:regen:{draft_id}"),
                InlineKeyboardButton(text="🎭 Сменить стиль", callback_data=f"biz_draft:style:{draft_id}"),
            ],
            [
                InlineKeyboardButton(text="🗑 Отклонить", callback_data=f"biz_draft:discard:{draft_id}"),
            ],
            [
                InlineKeyboardButton(
                    text="🔁 Включить авто-ответ для этого чата",
                    callback_data=f"biz_draft:autoreply:{draft_id}",
                )
            ],
        ]
    )


def business_autoreply_chats_keyboard(chats: list[dict]) -> InlineKeyboardMarkup:
    """Список чатов с включённым авто-ответом Secretary Mode — с кнопкой выключить у каждого."""
    buttons = [
        [
            InlineKeyboardButton(
                text=f"🔕 Выключить авто: {c['chat_title'][:30]}",
                callback_data=f"biz_autooff:{c['chat_id']}",
            )
        ]
        for c in chats
    ]
    buttons.append([InlineKeyboardButton(text="◀️ Назад в бизнес-меню", callback_data="bizmenu:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def inline_control_keyboard(session_id: str, author_id: Optional[int] = None) -> InlineKeyboardMarkup:
    """Интерактивная панель управления для инлайн-сообщений (перегенерация, смена стиля, фиксация)."""
    suffix = f":{author_id}" if author_id is not None else ""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Еще вариант", callback_data=f"inl_regen:{session_id}{suffix}"),
                InlineKeyboardButton(text="🎭 Сменить стиль", callback_data=f"inl_style:{session_id}{suffix}"),
            ],
            [
                InlineKeyboardButton(text="✅ Зафиксировать", callback_data=f"inl_fix:{session_id}{suffix}"),
                InlineKeyboardButton(text="❌ Удалить", callback_data=f"inl_del:{session_id}{suffix}"),
            ],
        ]
    )


