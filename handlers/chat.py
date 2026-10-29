import io
import os
import logging
import re
from typing import Optional, Union

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, ReactionTypeEmoji
from google.genai import types

from handlers.common import (
    URL_REGEX,
    _extract_reply_info,
    _limits_line,
    _parse_caption_voice_flags,
    _process_user_turn,
    limiter,
    storage,
    try_download_image_from_url,
    ForwardedContext,
    get_forwarded_context,
    save_forwarded_context,
)

logger = logging.getLogger(__name__)

router = Router()

MAX_DOCUMENT_SIZE_BYTES = 15 * 1024 * 1024  # 15 МБ
ALLOWED_DOCUMENT_MIME_PREFIXES = ("text/", "image/", "audio/")
ALLOWED_DOCUMENT_MIME_TYPES = {
    "application/pdf",
    "application/json",
    "application/xml",
    "application/javascript",
    "application/x-python",
    "application/csv",
}
ALLOWED_DOCUMENT_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml",
    ".pdf", ".js", ".py", ".html", ".htm", ".css", ".ts",
    ".png", ".jpg", ".jpeg", ".webp", ".mp3", ".ogg", ".wav", ".m4a"
}


class DocumentValidationError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


_bot_username_cache: dict[int, str] = {}


async def _get_bot_username(bot: Bot) -> str:
    bot_id = getattr(bot, "id", None) or 0
    if bot_id in _bot_username_cache:
        return _bot_username_cache[bot_id]
    try:
        me = await bot.get_me()
        if me and me.username:
            _bot_username_cache[bot_id] = me.username.lower()
            return _bot_username_cache[bot_id]
    except Exception:
        pass
    return ""


async def _check_group_message_allowed(message: Message, text_or_caption: str) -> tuple[bool, str]:
    """Проверяет, адресовано ли сообщение боту в группе/супергруппе."""
    if message.chat.type not in ("group", "supergroup"):
        return True, text_or_caption

    bot = message.bot
    bot_id = getattr(bot, "id", None)

    # 1. Reply на сообщение бота
    if message.reply_to_message and message.reply_to_message.from_user:
        if bot_id and message.reply_to_message.from_user.id == bot_id:
            return True, text_or_caption
        if getattr(message.reply_to_message.from_user, "is_bot", False):
            bot_username = await _get_bot_username(bot)
            if bot_username and (message.reply_to_message.from_user.username or "").lower() == bot_username:
                return True, text_or_caption

    # 2. Упоминание бота (@bot_username)
    bot_username = await _get_bot_username(bot)
    if bot_username and text_or_caption:
        mention = f"@{bot_username}"
        if mention in text_or_caption.lower():
            pattern = re.compile(re.escape(mention), re.IGNORECASE)
            cleaned = pattern.sub("", text_or_caption).strip()
            return True, cleaned

    return False, ""


def _validate_document(doc) -> Optional[str]:
    """Проверяет размер и MIME-тип документа до скачивания в оперативную память.
    
    Возвращает строку с описанием ошибки, либо None, если документ валиден.
    """
    if not doc:
        return "❌ Файл не прикреплён."

    file_size = getattr(doc, "file_size", None)
    if file_size is None:
        return "❌ Не удалось определить размер файла."
    if file_size > MAX_DOCUMENT_SIZE_BYTES:
        mb = round(file_size / (1024 * 1024), 1)
        return f"❌ Файл слишком большой ({mb} МБ). Максимальный размер документа: 15 МБ."

    mime_type = (getattr(doc, "mime_type", None) or "").lower().strip()
    file_name = (getattr(doc, "file_name", "") or "").lower().strip()
    _, ext = os.path.splitext(file_name)

    if mime_type:
        if any(mime_type.startswith(prefix) for prefix in ALLOWED_DOCUMENT_MIME_PREFIXES):
            return None
        if mime_type in ALLOWED_DOCUMENT_MIME_TYPES:
            return None
        if mime_type == "application/octet-stream" and ext in ALLOWED_DOCUMENT_EXTENSIONS:
            return None
        return (
            f"❌ Неподдерживаемый тип файла ({mime_type}). "
            "Поддерживаются текстовые файлы, PDF, код, изображения и аудио."
        )

    if ext in ALLOWED_DOCUMENT_EXTENSIONS:
        return None

    return (
        f"❌ Неподдерживаемый тип файла ({ext or 'неизвестный формат'}). "
        "Поддерживаются текстовые файлы, PDF, код, изображения и аудио."
    )



async def _build_reply_payload(
    message: Message,
    replied: Optional[Message],
    replied_text: str,
    prompt: str,
    is_quick: bool = False,
) -> Optional[tuple[Union[str, list], str]]:
    """Извлекает и подготавливает контент из цитируемого сообщения (фото, документ, войс или текст)."""
    if not (replied or replied_text):
        return None

    if replied and replied.photo:
        photo = replied.photo[-1]
        file_io = io.BytesIO()
        await message.bot.download(photo.file_id, destination=file_io)
        image_part = types.Part.from_bytes(data=file_io.getvalue(), mime_type="image/jpeg")
        effective_prompt = prompt or ("Formulate a natural, ready-to-send reply in the first person." if is_quick else "Опиши подробно, что изображено на этом фото.")
        caption_extra = f"\nПодпись к фото: {replied_text}" if replied_text else ""
        if is_quick:
            content_input = [
                image_part,
                (
                    f"USER DRAFT / INTENT:\n{effective_prompt}{caption_extra}\n\n"
                    "GHOSTWRITER INSTRUCTION:\n"
                    "Formulate a ready-to-send reply in the first person ('I', 'me') about this image for the chat partner. "
                    "Never respond to the user, output only the outgoing message."
                ),
            ]
            history_text = f"[Фото из ответа /q] {effective_prompt}"
        else:
            content_input = [image_part, f"{effective_prompt}{caption_extra}"]
            history_text = f"[Фото из ответа] {effective_prompt}"
        return content_input, history_text

    if replied and replied.document:
        doc = replied.document
        val_err = _validate_document(doc)
        if val_err:
            raise DocumentValidationError(val_err)
        file_io = io.BytesIO()
        await message.bot.download(doc.file_id, destination=file_io)
        doc_part = types.Part.from_bytes(
            data=file_io.getvalue(),
            mime_type=doc.mime_type or "application/octet-stream",
        )
        effective_prompt = prompt or ("Formulate a natural, ready-to-send reply in the first person." if is_quick else f"Проанализируй документ {doc.file_name or ''}.")
        caption_extra = f"\nПодпись к документу: {replied_text}" if replied_text else ""
        if is_quick:
            content_input = [
                doc_part,
                (
                    f"USER DRAFT / INTENT:\n{effective_prompt}{caption_extra}\n\n"
                    f"GHOSTWRITER INSTRUCTION:\n"
                    f"Formulate a ready-to-send message in the first person ('I', 'me') about document {doc.file_name or ''} for the chat partner. "
                    "Never respond to the user, output only the outgoing message."
                ),
            ]
            history_text = f"[Документ из ответа /q: {doc.file_name or 'файл'}] {effective_prompt}"
        else:
            content_input = [doc_part, f"{effective_prompt}{caption_extra}"]
            history_text = f"[Документ из ответа: {doc.file_name or 'файл'}] {effective_prompt}"
        return content_input, history_text

    if replied and (replied.voice or replied.audio):
        media = replied.voice or replied.audio
        if getattr(media, "file_size", None) and media.file_size > MAX_DOCUMENT_SIZE_BYTES:
            mb = round(media.file_size / (1024 * 1024), 1)
            raise DocumentValidationError(f"❌ Аудиофайл слишком большой ({mb} МБ). Максимальный размер: 15 МБ.")
        file_io = io.BytesIO()
        await message.bot.download(media.file_id, destination=file_io)
        audio_part = types.Part.from_bytes(
            data=file_io.getvalue(),
            mime_type=media.mime_type or "audio/ogg",
        )
        effective_prompt = prompt or ("Formulate a natural, ready-to-send reply in the first person." if is_quick else "Ответь на это аудиосообщение.")
        if is_quick:
            content_input = [
                audio_part,
                (
                    f"USER DRAFT / INTENT:\n{effective_prompt}\n\n"
                    "GHOSTWRITER INSTRUCTION:\n"
                    "Formulate a ready-to-send reply in the first person ('I', 'me') to this audio message for the chat partner. "
                    "Never respond to the user, output only the outgoing message."
                ),
            ]
            history_text = f"[Голосовое из ответа /q] {effective_prompt}"
        else:
            content_input = [audio_part, effective_prompt]
            history_text = f"[Голосовое сообщение] {effective_prompt}"
        return content_input, history_text

    if replied_text:
        effective_prompt = prompt or ("Formulate a natural, ready-to-send reply in the first person." if is_quick else "Ответь на это сообщение.")
        if is_quick:
            content_input = (
                "INTERLOCUTOR'S MESSAGE IN CHAT:\n"
                "\"\"\"\n"
                f"{replied_text}\n"
                "\"\"\"\n\n"
                "USER DRAFT / INTENT FOR REPLY:\n"
                "\"\"\"\n"
                f"{effective_prompt}\n"
                "\"\"\"\n\n"
                "GHOSTWRITER INSTRUCTION:\n"
                "1. Write a direct, ready-to-send reply to the interlocutor in the first person ('I', 'me') matching the conversation language.\n"
                "2. Strictly NEVER respond to or acknowledge the user ('Sure', 'Will do', 'Checking now'). The user is NOT addressing you.\n"
                "3. Output ONLY the ready-to-send message."
            )
            history_text = f"[Ответ /q на: «{replied_text[:60]}...»] {effective_prompt}"
        else:
            content_input = (
                f"Контекст цитируемого сообщения:\n«««\n{replied_text}\n»»»\n\n"
                f"Запрос:\n{effective_prompt}"
            )
            history_text = f"[Ответ на: «{replied_text[:60]}...»] {effective_prompt}"
        return content_input, history_text

    return None


async def _build_url_image_payload(text: str, is_quick: bool = False) -> Optional[tuple[list, str]]:
    """Проверяет наличие URL картинки и подготавливает payload."""
    url_match = URL_REGEX.search(text)
    if not url_match:
        return None
    url = url_match.group(0)
    img_data = await try_download_image_from_url(url)
    if not img_data:
        return None
    img_bytes, mime = img_data
    image_part = types.Part.from_bytes(data=img_bytes, mime_type=mime)
    clean_text = text.replace(url, "").strip() or "Опиши подробно, что изображено на этом фото."
    if is_quick:
        content_input = [
            image_part,
            f"USER DRAFT: {clean_text}\n\nFormulate a ready-to-send message in the first person for the chat partner."
        ]
        history_text = f"[Изображение по ссылке /q] {clean_text}"
    else:
        content_input = [image_part, clean_text]
        history_text = f"[Фото по ссылке] {clean_text}"
    return content_input, history_text


# --- Команды принудительного формата (/voice, /text, /q) ---

@router.message(Command("voice", "v"))
async def cmd_voice(message: Message) -> None:
    """Принудительный ответ голосом на вопрос или вложение."""
    args = message.text.split(maxsplit=1) if message.text else []
    query_text = args[1].strip() if len(args) > 1 else ""

    replied_text, replied = _extract_reply_info(message)
    try:
        reply_payload = await _build_reply_payload(message, replied, replied_text, query_text)
    except DocumentValidationError as err:
        await message.answer(err.message)
        return
    if reply_payload:
        content_input, history_text = reply_payload
        await _process_user_turn(
            message=message,
            content_input=content_input,
            history_text=history_text,
            force_voice_reply=True,
        )
        return

    if not query_text:
        await message.answer(
            "Использование: <code>/voice ваш вопрос</code>\n\n"
            "Либо ответьте командой <code>/voice</code> на любое фото, документ или сообщение в чате, чтобы получить ответ голосом.",
            parse_mode="HTML",
        )
        return

    url_payload = await _build_url_image_payload(query_text)
    if url_payload:
        content_input, history_text = url_payload
        await _process_user_turn(
            message=message,
            content_input=content_input,
            history_text=history_text,
            force_voice_reply=True,
        )
        return

    await _process_user_turn(
        message=message,
        content_input=query_text,
        history_text=query_text,
        force_voice_reply=True,
    )


@router.message(Command("text", "t"))
async def cmd_text(message: Message) -> None:
    """Принудительный ответ текстом на вопрос или вложение."""
    args = message.text.split(maxsplit=1) if message.text else []
    query_text = args[1].strip() if len(args) > 1 else ""

    replied_text, replied = _extract_reply_info(message)
    try:
        reply_payload = await _build_reply_payload(message, replied, replied_text, query_text)
    except DocumentValidationError as err:
        await message.answer(err.message)
        return
    if reply_payload:
        content_input, history_text = reply_payload
        await _process_user_turn(
            message=message,
            content_input=content_input,
            history_text=history_text,
            force_text_only=True,
        )
        return

    if not query_text:
        await message.answer(
            "Использование: <code>/text ваш вопрос</code>\n\n"
            "Либо ответьте командой <code>/text</code> на любое голосовое, фото или документ, чтобы получить ответ строго текстом.",
            parse_mode="HTML",
        )
        return

    url_payload = await _build_url_image_payload(query_text)
    if url_payload:
        content_input, history_text = url_payload
        await _process_user_turn(
            message=message,
            content_input=content_input,
            history_text=history_text,
            force_text_only=True,
        )
        return

    await _process_user_turn(
        message=message,
        content_input=query_text,
        history_text=query_text,
        force_text_only=True,
    )


@router.message(Command("q", "quick"))
async def cmd_q(message: Message) -> None:
    """Режим Аватара: генерация готового ответа от первого лица ('я', 'мне')."""
    args = message.text.split(maxsplit=1) if message.text else []
    clean_text = args[1].strip() if len(args) > 1 else ""

    replied_text, replied = _extract_reply_info(message)
    try:
        reply_payload = await _build_reply_payload(message, replied, replied_text, clean_text, is_quick=True)
    except DocumentValidationError as err:
        await message.answer(err.message)
        return
    if reply_payload:
        content_input, history_text = reply_payload
        await _process_user_turn(
            message=message,
            content_input=content_input,
            history_text=history_text,
            no_quote=True,
            use_quick_prompt=True,
        )
        return

    if not clean_text:
        await message.answer(
            "🎭 <b>Режим Аватара (/q):</b>\n\n"
            "Использование: <code>/q ваши указания/черновик</code>\n"
            "Или ответьте командой <code>/q</code> на любое входящее сообщение в чате.\n\n"
            "• Бот выступает вашим текстовым суфлёром и пишет <b>готовый ответ от 1-го лица</b> («я», «мне») без цитирования и лишних фраз.\n"
            "• Управление личностями: <code>/avatars</code> (Бро, Бизнес, Сарказм, Краткий, Флирт и свои).",
            parse_mode="HTML",
        )
        return

    user_id = message.from_user.id
    fwd_ctx = get_forwarded_context(user_id, consume=True)
    if fwd_ctx:
        if fwd_ctx.notification_message_id:
            try:
                await message.bot.delete_message(chat_id=user_id, message_id=fwd_ctx.notification_message_id)
            except Exception:
                pass
        prompt = clean_text or "Ответь на это сообщение."
        interlocutor_block = (
            f"INTERLOCUTOR'S MESSAGE IN CHAT (from {fwd_ctx.sender_name}):\n\"\"\"\n{fwd_ctx.text}\n\"\"\"\n\n"
            f"USER DRAFT / INTENT TO BE SENT TO CHAT PARTNER:\n\"\"\"\n{prompt}\n\"\"\"\n\n"
            "GHOSTWRITER INSTRUCTION:\n"
            "1. Formulate a ready-to-send reply in the first person ('I', 'me') to the interlocutor matching the conversation language.\n"
            "2. Strictly NEVER respond to or acknowledge the user ('Sure', 'Will do'). Output ONLY the final message."
        )
        if fwd_ctx.media_bytes and fwd_ctx.mime_type:
            part = types.Part.from_bytes(data=fwd_ctx.media_bytes, mime_type=fwd_ctx.mime_type)
            content_input = [part, interlocutor_block]
        else:
            content_input = interlocutor_block

        await _process_user_turn(
            message=message,
            content_input=content_input,
            history_text=f"[Из пересланного от {fwd_ctx.sender_name}] {prompt}",
            no_quote=True,
            use_quick_prompt=True,
        )
        return

    url_payload = await _build_url_image_payload(clean_text, is_quick=True)
    if url_payload:
        content_input, history_text = url_payload
        await _process_user_turn(
            message=message,
            content_input=content_input,
            history_text=history_text,
            no_quote=True,
            use_quick_prompt=True,
        )
        return

    content_input = (
        "USER DRAFT / INTENT TO BE SENT TO CHAT PARTNER:\n"
        "\"\"\"\n"
        f"{clean_text}\n"
        "\"\"\"\n\n"
        "GHOSTWRITER INSTRUCTION:\n"
        "1. Transform this draft/intent into a polished ready-to-send message in the first person ('I', 'me') matching the conversation language.\n"
        "2. Strictly NEVER respond to or acknowledge the user ('Sure', 'Will do', 'Checking now'). The user is NOT addressing you.\n"
        "3. Output ONLY the ready-to-send message."
    )
    await _process_user_turn(
        message=message,
        content_input=content_input,
        history_text=clean_text,
        no_quote=True,
        use_quick_prompt=True,
    )



# --- Context Bridge: Пересылка сообщений в ЛС для захвата контекста ---

@router.message(F.chat.type == "private", F.forward_origin)
async def handle_forwarded_message(message: Message) -> None:
    """Захватывает пересланное сообщение в ЛС в буфер ForwardedContext на 3 минуты."""
    user_id = message.from_user.id
    sender_name = "Собеседник"
    origin = message.forward_origin
    if origin:
        if getattr(origin, "sender_user", None):
            sender_name = origin.sender_user.full_name
        elif getattr(origin, "sender_user_name", None):
            sender_name = origin.sender_user_name
        elif getattr(origin, "chat", None):
            sender_name = origin.chat.title or "Канал/Группа"

    text = (message.text or message.caption or "").strip()
    media_bytes: Optional[bytes] = None
    mime_type: Optional[str] = None

    if message.photo:
        photo = message.photo[-1]
        file_io = io.BytesIO()
        await message.bot.download(photo.file_id, destination=file_io)
        media_bytes = file_io.getvalue()
        mime_type = "image/jpeg"
    elif message.voice or message.audio:
        aud = message.voice or message.audio
        if getattr(aud, "file_size", None) and aud.file_size > MAX_DOCUMENT_SIZE_BYTES:
            await message.answer("⚠️ Аудиофайл превышает 15 МБ, контекст сохранён без аудио.")
        else:
            file_io = io.BytesIO()
            await message.bot.download(aud.file_id, destination=file_io)
            media_bytes = file_io.getvalue()
            mime_type = aud.mime_type or ("audio/ogg" if message.voice else "audio/mp3")
    elif message.document:
        doc = message.document
        val_err = _validate_document(doc)
        if not val_err:
            file_io = io.BytesIO()
            await message.bot.download(doc.file_id, destination=file_io)
            media_bytes = file_io.getvalue()
            mime_type = doc.mime_type or "application/octet-stream"

    ctx = ForwardedContext(
        text=text,
        sender_name=sender_name,
        media_bytes=media_bytes,
        mime_type=mime_type,
    )
    save_forwarded_context(user_id, ctx)

    try:
        await message.react([ReactionTypeEmoji(emoji="👀")])
    except Exception:
        pass

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🥊 Разобрать сразу в Stand", callback_data="fwd_stand_now")]
        ]
    )
    preview = f"\n<blockquote>{text[:150]}{'…' if len(text) > 150 else ''}</blockquote>" if text else ""
    sent = await message.answer(
        f"👀 <b>Контекст от {sender_name} схвачен на 3 минуты!</b>{preview}\n\n"
        "Теперь вы можете:\n"
        "• Перейти в нужный чат и набрать <code>@bot_username ваш черновик</code> для ответа через Аватар.\n"
        "• Написать свой вопрос прямо сюда следующим сообщением.\n"
        "• Или нажать кнопку ниже, чтобы разобрать это сообщение прямо сейчас.",
        parse_mode="HTML",
        reply_markup=kb,
    )
    ctx.notification_message_id = sent.message_id


@router.callback_query(F.data == "fwd_stand_now")
async def cb_fwd_stand_now(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    ctx = get_forwarded_context(user_id, consume=True)
    if not ctx:
        await callback.answer("Время действия контекста истекло.", show_alert=True)
        return

    try:
        await callback.message.delete()
    except Exception:
        pass

    await callback.answer("Разбираю... 🥊")
    prompt_text = f"Проанализируй пересланное сообщение от {ctx.sender_name}:\n{ctx.text}"
    if ctx.media_bytes and ctx.mime_type:
        part = types.Part.from_bytes(data=ctx.media_bytes, mime_type=ctx.mime_type)
        content_input = [part, prompt_text]
    else:
        content_input = prompt_text

    await _process_user_turn(
        message=callback.message,
        content_input=content_input,
        history_text=f"[Пересланное от {ctx.sender_name}] {ctx.text[:80]}",
        user_id_override=user_id,
    )

# --- Обработчики входящих сообщений (текст, фото, голос, документы) ---

@router.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message) -> None:
    is_allowed, clean_query = await _check_group_message_allowed(message, message.text or "")
    if not is_allowed:
        return

    clean_text, force_voice, force_text = _parse_caption_voice_flags(clean_query)
    clean_text = clean_text or clean_query

    replied_text, replied = _extract_reply_info(message)
    try:
        reply_payload = await _build_reply_payload(message, replied, replied_text, clean_text)
    except DocumentValidationError as err:
        await message.answer(err.message)
        return
    if reply_payload:
        content_input, history_text = reply_payload
        await _process_user_turn(
            message=message,
            content_input=content_input,
            history_text=history_text,
            force_voice_reply=force_voice,
            force_text_only=force_text,
        )
        return

    user_id = message.from_user.id
    fwd_ctx = get_forwarded_context(user_id, consume=True)
    if fwd_ctx:
        if fwd_ctx.notification_message_id:
            try:
                await message.bot.delete_message(chat_id=user_id, message_id=fwd_ctx.notification_message_id)
            except Exception:
                pass
        stand_text = f"Контекст пересланного сообщения от {fwd_ctx.sender_name}:\n«««\n{fwd_ctx.text}\n»»»\n\nВопрос:\n{clean_text}"
        if fwd_ctx.media_bytes and fwd_ctx.mime_type:
            part = types.Part.from_bytes(data=fwd_ctx.media_bytes, mime_type=fwd_ctx.mime_type)
            content_input = [part, stand_text]
        else:
            content_input = stand_text

        await _process_user_turn(
            message=message,
            content_input=content_input,
            history_text=f"[К пересланному от {fwd_ctx.sender_name}] {clean_text}",
            force_voice_reply=force_voice,
            force_text_only=force_text,
        )
        return

    url_payload = await _build_url_image_payload(clean_text)
    if url_payload:
        content_input, history_text = url_payload
        await _process_user_turn(
            message=message,
            content_input=content_input,
            history_text=history_text,
            force_voice_reply=force_voice,
            force_text_only=force_text,
        )
        return

    await _process_user_turn(
        message=message,
        content_input=clean_text,
        history_text=clean_text,
        force_voice_reply=force_voice,
        force_text_only=force_text,
    )


@router.message(F.photo)
async def handle_photo(message: Message) -> None:
    is_allowed, clean_cap = await _check_group_message_allowed(message, message.caption or "")
    if not is_allowed:
        return

    user_id = message.from_user.id
    limit_status = await limiter.check(user_id)
    if not limit_status.allowed:
        wait_seconds = int(limit_status.retry_after) + 1
        await message.answer(
            f"⏳ Лимит запросов исчерпан, попробуй снова через {wait_seconds} сек.\n"
            f"({await _limits_line(user_id)})"
        )
        return

    photo = message.photo[-1]
    clean_cap, force_voice, force_text = _parse_caption_voice_flags(message.caption)
    prompt_text = clean_cap or "Опиши подробно, что изображено на этом фото."

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
        force_voice_reply=force_voice,
        force_text_only=force_text,
    )


@router.message(F.voice | F.audio)
async def handle_voice(message: Message) -> None:
    is_allowed, _ = await _check_group_message_allowed(message, message.caption or "")
    if not is_allowed:
        return

    media = message.voice or message.audio
    if getattr(media, "file_size", None) and media.file_size > MAX_DOCUMENT_SIZE_BYTES:
        mb = round(media.file_size / (1024 * 1024), 1)
        await message.answer(f"❌ Аудиофайл слишком большой ({mb} МБ). Максимальный размер: 15 МБ.")
        return

    user_id = message.from_user.id
    limit_status = await limiter.check(user_id)
    if not limit_status.allowed:
        wait_seconds = int(limit_status.retry_after) + 1
        await message.answer(
            f"⏳ Лимит запросов исчерпан, попробуй снова через {wait_seconds} сек.\n"
            f"({await _limits_line(user_id)})"
        )
        return

    media = message.voice or message.audio
    clean_cap, force_voice, force_text = _parse_caption_voice_flags(message.caption)
    mime_type = media.mime_type or ("audio/ogg" if message.voice else "audio/mp3")
    prompt_text = clean_cap or "Ответь на аудиосообщение."

    file_io = io.BytesIO()
    await message.bot.download(media.file_id, destination=file_io)
    audio_bytes = file_io.getvalue()

    audio_part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)
    content_input = [audio_part, prompt_text]
    history_text = f"[Голосовое сообщение] {prompt_text}"

    user_state = await storage.get_settings(user_id)
    await _process_user_turn(
        message=message,
        content_input=content_input,
        history_text=history_text,
        force_voice_reply=True if not force_text else False,
        force_text_only=force_text,
        model_override=user_state.stt_model or user_state.model,
    )


@router.message(F.document)
async def handle_document(message: Message) -> None:
    is_allowed, _ = await _check_group_message_allowed(message, message.caption or "")
    if not is_allowed:
        return

    doc = message.document
    val_err = _validate_document(doc)
    if val_err:
        await message.answer(val_err)
        return

    user_id = message.from_user.id
    limit_status = await limiter.check(user_id)
    if not limit_status.allowed:
        wait_seconds = int(limit_status.retry_after) + 1
        await message.answer(
            f"⏳ Лимит запросов исчерпан, попробуй снова через {wait_seconds} сек.\n"
            f"({await _limits_line(user_id)})"
        )
        return

    doc = message.document
    clean_cap, force_voice, force_text = _parse_caption_voice_flags(message.caption)
    mime_type = doc.mime_type or "application/octet-stream"
    prompt_text = clean_cap or f"Проанализируй документ {doc.file_name or ''}."

    file_io = io.BytesIO()
    await message.bot.download(doc.file_id, destination=file_io)
    doc_bytes = file_io.getvalue()

    doc_part = types.Part.from_bytes(data=doc_bytes, mime_type=mime_type)
    content_input = [doc_part, prompt_text]
    history_text = f"[Документ: {doc.file_name or 'файл'}] {prompt_text}"

    await _process_user_turn(
        message=message,
        content_input=content_input,
        history_text=history_text,
        force_voice_reply=force_voice,
        force_text_only=force_text,
    )

