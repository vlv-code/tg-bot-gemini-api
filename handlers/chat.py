import io
import logging
import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
from google.genai import types

from handlers.common import (
    URL_REGEX,
    _extract_reply_info,
    _parse_caption_voice_flags,
    _process_user_turn,
    try_download_image_from_url,
)

logger = logging.getLogger(__name__)

router = Router()


# --- Команды принудительного формата (/voice, /text, /q) ---

@router.message(Command("voice", "v"))
async def cmd_voice(message: Message) -> None:
    """Принудительный ответ голосом на вопрос или вложение."""
    args = message.text.split(maxsplit=1) if message.text else []
    query_text = args[1].strip() if len(args) > 1 else ""

    replied_text, replied = _extract_reply_info(message)
    if replied or replied_text:
        if replied and replied.photo:
            photo = replied.photo[-1]
            file_io = io.BytesIO()
            await message.bot.download(photo.file_id, destination=file_io)
            image_part = types.Part.from_bytes(data=file_io.getvalue(), mime_type="image/jpeg")
            prompt = query_text or "Опиши подробно голосом, что изображено на этом фото."
            caption_extra = f"\nПодпись к фото: {replied_text}" if replied_text else ""
            content_input = [image_part, f"{prompt}{caption_extra}"]
            history_text = f"[Фото из ответа] {prompt}"
            await _process_user_turn(
                message=message,
                content_input=content_input,
                history_text=history_text,
                force_voice_reply=True,
            )
            return

        if replied and replied.document:
            doc = replied.document
            file_io = io.BytesIO()
            await message.bot.download(doc.file_id, destination=file_io)
            doc_part = types.Part.from_bytes(
                data=file_io.getvalue(),
                mime_type=doc.mime_type or "application/octet-stream",
            )
            prompt = query_text or f"Проанализируй документ {doc.file_name or ''} и ответь голосом."
            caption_extra = f"\nПодпись к документу: {replied_text}" if replied_text else ""
            content_input = [doc_part, f"{prompt}{caption_extra}"]
            history_text = f"[Документ из ответа: {doc.file_name or 'файл'}] {prompt}"
            await _process_user_turn(
                message=message,
                content_input=content_input,
                history_text=history_text,
                force_voice_reply=True,
            )
            return

        if replied and (replied.voice or replied.audio):
            media = replied.voice or replied.audio
            file_io = io.BytesIO()
            await message.bot.download(media.file_id, destination=file_io)
            audio_part = types.Part.from_bytes(
                data=file_io.getvalue(),
                mime_type=media.mime_type or "audio/ogg",
            )
            prompt = query_text or "Ответь голосом на это аудиосообщение."
            content_input = [audio_part, prompt]
            history_text = f"[Голосовое сообщение] {prompt}"
            await _process_user_turn(
                message=message,
                content_input=content_input,
                history_text=history_text,
                force_voice_reply=True,
            )
            return

        if replied_text:
            prompt = query_text or "Ответь голосом на это сообщение."
            content_input = (
                f"Контекст цитируемого сообщения:\n«««\n{replied_text}\n»»»\n\n"
                f"Запрос:\n{prompt}"
            )
            history_text = f"[Ответ на: «{replied_text[:60]}...»] {prompt}"
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

    url_match = URL_REGEX.search(query_text)
    if url_match:
        url = url_match.group(0)
        img_data = await try_download_image_from_url(url)
        if img_data:
            img_bytes, mime = img_data
            image_part = types.Part.from_bytes(data=img_bytes, mime_type=mime)
            clean_text = query_text.replace(url, "").strip() or "Опиши подробно голосом, что на фото."
            await _process_user_turn(
                message=message,
                content_input=[image_part, clean_text],
                history_text=f"[Фото по ссылке] {clean_text}",
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
    if replied or replied_text:
        if replied and replied.photo:
            photo = replied.photo[-1]
            file_io = io.BytesIO()
            await message.bot.download(photo.file_id, destination=file_io)
            image_part = types.Part.from_bytes(data=file_io.getvalue(), mime_type="image/jpeg")
            prompt = query_text or "Опиши подробно текстом, что изображено на этом фото."
            caption_extra = f"\nПодпись к фото: {replied_text}" if replied_text else ""
            content_input = [image_part, f"{prompt}{caption_extra}"]
            history_text = f"[Фото из ответа] {prompt}"
            await _process_user_turn(
                message=message,
                content_input=content_input,
                history_text=history_text,
                force_text_only=True,
            )
            return

        if replied and replied.document:
            doc = replied.document
            file_io = io.BytesIO()
            await message.bot.download(doc.file_id, destination=file_io)
            doc_part = types.Part.from_bytes(
                data=file_io.getvalue(),
                mime_type=doc.mime_type or "application/octet-stream",
            )
            prompt = query_text or f"Проанализируй документ {doc.file_name or ''}."
            caption_extra = f"\nПодпись к документу: {replied_text}" if replied_text else ""
            content_input = [doc_part, f"{prompt}{caption_extra}"]
            history_text = f"[Документ из ответа: {doc.file_name or 'файл'}] {prompt}"
            await _process_user_turn(
                message=message,
                content_input=content_input,
                history_text=history_text,
                force_text_only=True,
            )
            return

        if replied and (replied.voice or replied.audio):
            media = replied.voice or replied.audio
            file_io = io.BytesIO()
            await message.bot.download(media.file_id, destination=file_io)
            audio_part = types.Part.from_bytes(
                data=file_io.getvalue(),
                mime_type=media.mime_type or "audio/ogg",
            )
            prompt = query_text or "Ответь текстом на аудиосообщение."
            content_input = [audio_part, prompt]
            history_text = f"[Голосовое сообщение] {prompt}"
            await _process_user_turn(
                message=message,
                content_input=content_input,
                history_text=history_text,
                force_text_only=True,
            )
            return

        if replied_text:
            prompt = query_text or "Ответь текстом на это сообщение."
            content_input = (
                f"Контекст цитируемого сообщения:\n«««\n{replied_text}\n»»»\n\n"
                f"Запрос:\n{prompt}"
            )
            history_text = f"[Ответ на: «{replied_text[:60]}...»] {prompt}"
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

    url_match = URL_REGEX.search(query_text)
    if url_match:
        url = url_match.group(0)
        img_data = await try_download_image_from_url(url)
        if img_data:
            img_bytes, mime = img_data
            image_part = types.Part.from_bytes(data=img_bytes, mime_type=mime)
            clean_text = query_text.replace(url, "").strip() or "Опиши подробно, что на фото."
            await _process_user_turn(
                message=message,
                content_input=[image_part, clean_text],
                history_text=f"[Фото по ссылке] {clean_text}",
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

    # 1. Если пользователь ответил командой /q на другое сообщение (reply)
    replied_text, replied = _extract_reply_info(message)
    if replied or replied_text:
        user_intent = clean_text or "Formulate a natural, ready-to-send reply in the first person."

        if replied and replied.photo:
            photo = replied.photo[-1]
            file_io = io.BytesIO()
            await message.bot.download(photo.file_id, destination=file_io)
            image_bytes = file_io.getvalue()
            image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
            caption_extra = f"\nImage caption: {replied_text}" if replied_text else ""
            content_input = [
                image_part,
                (
                    f"USER DRAFT / INTENT:\n{user_intent}{caption_extra}\n\n"
                    "GHOSTWRITER INSTRUCTION:\n"
                    "Formulate a ready-to-send reply in the first person ('I', 'me') about this image for the chat partner. "
                    "Never respond to the user, output only the outgoing message."
                ),
            ]
            history_text = f"[Фото из ответа /q] {user_intent}"
            await _process_user_turn(
                message=message,
                content_input=content_input,
                history_text=history_text,
                no_quote=True,
                use_quick_prompt=True,
            )
            return

        if replied and replied.document:
            doc = replied.document
            file_io = io.BytesIO()
            await message.bot.download(doc.file_id, destination=file_io)
            doc_bytes = file_io.getvalue()
            doc_part = types.Part.from_bytes(data=doc_bytes, mime_type=doc.mime_type or "application/octet-stream")
            caption_extra = f"\nDocument caption: {replied_text}" if replied_text else ""
            content_input = [
                doc_part,
                (
                    f"USER DRAFT / INTENT:\n{user_intent}{caption_extra}\n\n"
                    f"GHOSTWRITER INSTRUCTION:\n"
                    f"Formulate a ready-to-send message in the first person ('I', 'me') about document {doc.file_name or ''} for the chat partner. "
                    "Never respond to the user, output only the outgoing message."
                ),
            ]
            history_text = f"[Документ из ответа /q: {doc.file_name or 'файл'}] {user_intent}"
            await _process_user_turn(
                message=message,
                content_input=content_input,
                history_text=history_text,
                no_quote=True,
                use_quick_prompt=True,
            )
            return

        if replied and (replied.voice or replied.audio):
            media = replied.voice or replied.audio
            file_io = io.BytesIO()
            await message.bot.download(media.file_id, destination=file_io)
            audio_part = types.Part.from_bytes(
                data=file_io.getvalue(),
                mime_type=media.mime_type or "audio/ogg",
            )
            content_input = [
                audio_part,
                (
                    f"USER DRAFT / INTENT:\n{user_intent}\n\n"
                    "GHOSTWRITER INSTRUCTION:\n"
                    "Formulate a ready-to-send reply in the first person ('I', 'me') to this audio message for the chat partner. "
                    "Never respond to the user, output only the outgoing message."
                ),
            ]
            history_text = f"[Голосовое из ответа /q] {user_intent}"
            await _process_user_turn(
                message=message,
                content_input=content_input,
                history_text=history_text,
                no_quote=True,
                use_quick_prompt=True,
            )
            return

        if replied_text:
            content_input = (
                "INTERLOCUTOR'S MESSAGE IN CHAT:\n"
                "\"\"\"\n"
                f"{replied_text}\n"
                "\"\"\"\n\n"
                "USER DRAFT / INTENT FOR REPLY:\n"
                "\"\"\"\n"
                f"{user_intent}\n"
                "\"\"\"\n\n"
                "GHOSTWRITER INSTRUCTION:\n"
                "1. Write a direct, ready-to-send reply to the interlocutor in the first person ('I', 'me') matching the conversation language.\n"
                "2. Strictly NEVER respond to or acknowledge the user ('Sure', 'Will do', 'Checking now'). The user is NOT addressing you.\n"
                "3. Output ONLY the ready-to-send message."
            )
            history_text = f"[Ответ /q на: «{replied_text[:60]}...»] {user_intent}"
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

    url_match = URL_REGEX.search(clean_text)
    if url_match:
        img_url = url_match.group(0)
        img_data = await try_download_image_from_url(img_url)
        if img_data:
            img_bytes, mime = img_data
            image_part = types.Part.from_bytes(data=img_bytes, mime_type=mime)
            pure_text = clean_text.replace(img_url, "").strip() or "Describe in detail what is depicted in this image."
            content_input = [
                image_part,
                f"USER DRAFT: {pure_text}\n\nFormulate a ready-to-send message in the first person for the chat partner."
            ]
            history_text = f"[Изображение по ссылке /q] {pure_text}"
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


# --- Обработчики входящих сообщений (текст, фото, голос, документы) ---

@router.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message) -> None:
    clean_text, force_voice, force_text = _parse_caption_voice_flags(message.text)
    clean_text = clean_text or message.text

    replied_text, replied = _extract_reply_info(message)
    if replied or replied_text:
        if replied and replied.photo:
            photo = replied.photo[-1]
            file_io = io.BytesIO()
            await message.bot.download(photo.file_id, destination=file_io)
            image_bytes = file_io.getvalue()
            image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
            prompt = clean_text or "Опиши подробно, что изображено на этом фото."
            caption_extra = f"\nПодпись к фото: {replied_text}" if replied_text else ""
            content_input = [image_part, f"{prompt}{caption_extra}"]
            history_text = f"[Фото из ответа] {prompt}"
            await _process_user_turn(
                message=message,
                content_input=content_input,
                history_text=history_text,
                force_voice_reply=force_voice,
                force_text_only=force_text,
            )
            return

        if replied and replied.document:
            doc = replied.document
            file_io = io.BytesIO()
            await message.bot.download(doc.file_id, destination=file_io)
            doc_bytes = file_io.getvalue()
            doc_part = types.Part.from_bytes(data=doc_bytes, mime_type=doc.mime_type or "application/octet-stream")
            prompt = clean_text or f"Проанализируй документ {doc.file_name or ''}."
            caption_extra = f"\nПодпись к документу: {replied_text}" if replied_text else ""
            content_input = [doc_part, f"{prompt}{caption_extra}"]
            history_text = f"[Документ из ответа: {doc.file_name or 'файл'}] {prompt}"
            await _process_user_turn(
                message=message,
                content_input=content_input,
                history_text=history_text,
                force_voice_reply=force_voice,
                force_text_only=force_text,
            )
            return

        if replied and (replied.voice or replied.audio):
            media = replied.voice or replied.audio
            file_io = io.BytesIO()
            await message.bot.download(media.file_id, destination=file_io)
            audio_part = types.Part.from_bytes(
                data=file_io.getvalue(),
                mime_type=media.mime_type or "audio/ogg",
            )
            prompt = clean_text or "Ответь на это аудиосообщение."
            content_input = [audio_part, prompt]
            history_text = f"[Голосовое сообщение] {prompt}"
            await _process_user_turn(
                message=message,
                content_input=content_input,
                history_text=history_text,
                force_voice_reply=force_voice,
                force_text_only=force_text,
            )
            return

        if replied_text:
            prompt = clean_text or "Ответь на это сообщение."
            content_input = (
                f"Контекст цитируемого сообщения:\n«««\n{replied_text}\n»»»\n\n"
                f"Запрос:\n{prompt}"
            )
            history_text = f"[Ответ на: «{replied_text[:60]}...»] {prompt}"
            await _process_user_turn(
                message=message,
                content_input=content_input,
                history_text=history_text,
                force_voice_reply=force_voice,
                force_text_only=force_text,
            )
            return

    url_match = URL_REGEX.search(clean_text)
    if url_match:
        url = url_match.group(0)
        img_data = await try_download_image_from_url(url)
        if img_data:
            img_bytes, mime = img_data
            image_part = types.Part.from_bytes(data=img_bytes, mime_type=mime)
            prompt_without_url = clean_text.replace(url, "").strip() or "Опиши подробно, что изображено на этом фото."
            content_input = [image_part, prompt_without_url]
            history_text = f"[Фото по ссылке] {prompt_without_url}"
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

    await _process_user_turn(
        message=message,
        content_input=content_input,
        history_text=history_text,
        force_voice_reply=True if not force_text else False,
        force_text_only=force_text,
    )


@router.message(F.document)
async def handle_document(message: Message) -> None:
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

