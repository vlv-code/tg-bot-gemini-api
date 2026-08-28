from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import settings


def models_keyboard(current_model: str) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=f"✅ {model}" if model == current_model else model,
                callback_data=f"model:{model}",
            )
        ]
        for model in settings.available_models
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def settings_keyboard(rich_mode: bool, voice_mode: bool = False) -> InlineKeyboardMarkup:
    rich_label = "🎨 Rich-режим: вкл" if rich_mode else "🎨 Rich-режим: выкл"
    voice_label = "🎙 Голосовые ответы: вкл" if voice_mode else "🎙 Голосовые ответы: выкл"
    buttons = [
        [InlineKeyboardButton(text=rich_label, callback_data="toggle_rich")],
        [InlineKeyboardButton(text=voice_label, callback_data="toggle_voice")],
        [InlineKeyboardButton(text="🗑 Очистить историю диалога", callback_data="clear_history")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

