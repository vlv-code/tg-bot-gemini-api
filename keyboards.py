from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import settings


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Главное меню бота со всеми разделами."""
    buttons = [
        [InlineKeyboardButton(text="🤖 Модель Gemini", callback_data="menu:model")],
        [InlineKeyboardButton(text="🎙 Настройки озвучки (TTS)", callback_data="menu:tts")],
        [InlineKeyboardButton(text="⚙️ Параметры чата", callback_data="menu:settings")],
        [InlineKeyboardButton(text="📝 Системный промпт", callback_data="menu:prompt")],
        [
            InlineKeyboardButton(text="📊 Лимиты", callback_data="menu:limits"),
            InlineKeyboardButton(text="🗑 Очистить историю", callback_data="menu:clear_hist"),
        ],
    ]
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


def prompt_keyboard() -> InlineKeyboardMarkup:
    """Меню системного промпта."""
    buttons = [
        [InlineKeyboardButton(text="🔄 Сбросить промпт на дефолтный", callback_data="reset_prompt")],
        [InlineKeyboardButton(text="◀️ Назад в главное меню", callback_data="menu:main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def limits_keyboard() -> InlineKeyboardMarkup:
    """Меню лимитов."""
    buttons = [
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_limits")],
        [InlineKeyboardButton(text="◀️ Назад в главное меню", callback_data="menu:main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


