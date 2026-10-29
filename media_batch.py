"""Multimodal media batching and draft summary builder for Secretary Mode 2.0.

Collects text, voice, photo, and audio messages into a single MediaBatchPayload
ready for Google Gemini API consumption and Telegram draft cards.
"""

from dataclasses import dataclass, field
import io
import logging
from typing import Any

from aiogram import Bot
from aiogram.types import Message
from google.genai import types

logger = logging.getLogger(__name__)

# 15 MB safety limit for downloaded media
MAX_MEDIA_BYTES = 15 * 1024 * 1024


@dataclass
class MediaBatchPayload:
    """Aggregated payload of batched messages for Gemini API and UI draft card."""
    combined_text: str = ""
    gemini_parts: list[Any] = field(default_factory=list)  # list[google.genai.types.Part]
    media_badges: list[str] = field(default_factory=list)  # e.g. ["🎙 Голосовое (0:15)", "📷 Фото"]
    has_media: bool = False


async def _download_file_bytes(bot: Bot, file_id: str) -> bytes | None:
    """Safely downloads a Telegram file into bytes."""
    try:
        downloaded = await bot.download(file_id)
    except Exception as exc:
        logger.warning("Secretary Mode: failed to download media file %s: %s", file_id, exc)
        return None

    if downloaded is None:
        return None
    if isinstance(downloaded, io.BytesIO):
        return downloaded.getvalue()
    if hasattr(downloaded, "getvalue"):
        return downloaded.getvalue()
    if hasattr(downloaded, "read"):
        if hasattr(downloaded, "seek"):
            downloaded.seek(0)
        return downloaded.read()
    if isinstance(downloaded, bytes):
        return downloaded
    return None


def _format_voice_badge(voice: Any) -> str:
    """Formats the voice message badge with duration if available."""
    duration = getattr(voice, "duration", None)
    if duration:
        if isinstance(duration, int):
            return f"🎙 Голосовое ({duration}с)"
        return f"🎙 Голосовое ({duration})"
    return "🎙 Голосовое"


async def build_media_batch(bot: Bot, messages: list[Message]) -> MediaBatchPayload:
    """Builds a MediaBatchPayload from an ordered sequence of Telegram messages.

    Iterates over messages in order:
    - Combines text and media captions separated by newlines.
    - Downloads voice notes, creates Part(audio/ogg), adds duration badge.
    - Downloads largest photo size, creates Part(image/jpeg), adds photo badge.
    - Downloads audio files, creates Part(audio/mime), adds audio badge.
    - Skips media files exceeding 20 MB with a warning badge.
    """
    payload = MediaBatchPayload()
    text_parts: list[str] = []

    for msg in messages:
        # 1. Text
        msg_text = getattr(msg, "text", None)
        if isinstance(msg_text, str) and msg_text:
            text_parts.append(msg_text)

        # 2. Voice
        voice = getattr(msg, "voice", None)
        if voice is not None:
            file_id = getattr(voice, "file_id", None)
            if isinstance(file_id, str) and file_id:
                raw_bytes = await _download_file_bytes(bot, file_id)
                if raw_bytes is not None:
                    if len(raw_bytes) > MAX_MEDIA_BYTES:
                        payload.media_badges.append("⚠️ Медиа > 15 МБ пропущено")
                    else:
                        part = types.Part.from_bytes(data=raw_bytes, mime_type="audio/ogg")
                        payload.gemini_parts.append(part)
                        payload.media_badges.append(_format_voice_badge(voice))
                        payload.has_media = True

        # 3. Photo
        photos = getattr(msg, "photo", None)
        if photos and isinstance(photos, (list, tuple)) and len(photos) > 0:
            largest = photos[-1]
            file_id = getattr(largest, "file_id", None)
            if isinstance(file_id, str) and file_id:
                raw_bytes = await _download_file_bytes(bot, file_id)
                if raw_bytes is not None:
                    if len(raw_bytes) > MAX_MEDIA_BYTES:
                        payload.media_badges.append("⚠️ Медиа > 15 МБ пропущено")
                    else:
                        part = types.Part.from_bytes(data=raw_bytes, mime_type="image/jpeg")
                        payload.gemini_parts.append(part)
                        payload.media_badges.append("📷 Фото")
                        payload.has_media = True

        # 4. Audio
        audio = getattr(msg, "audio", None)
        if audio is not None:
            file_id = getattr(audio, "file_id", None)
            if isinstance(file_id, str) and file_id:
                raw_bytes = await _download_file_bytes(bot, file_id)
                if raw_bytes is not None:
                    if len(raw_bytes) > MAX_MEDIA_BYTES:
                        payload.media_badges.append("⚠️ Медиа > 15 МБ пропущено")
                    else:
                        mime_type = getattr(audio, "mime_type", None) or "audio/mpeg"
                        part = types.Part.from_bytes(data=raw_bytes, mime_type=mime_type)
                        payload.gemini_parts.append(part)
                        payload.media_badges.append("🎵 Аудио")
                        payload.has_media = True

        # 5. Document
        doc = getattr(msg, "document", None)
        if doc is not None:
            file_id = getattr(doc, "file_id", None)
            if isinstance(file_id, str) and file_id:
                raw_size = getattr(doc, "file_size", 0)
                file_size = raw_size if isinstance(raw_size, (int, float)) else 0
                if file_size > MAX_MEDIA_BYTES:
                    payload.media_badges.append("⚠️ Документ > 15 МБ пропущен")
                else:
                    raw_bytes = await _download_file_bytes(bot, file_id)
                    if raw_bytes is not None:
                        mime_type = getattr(doc, "mime_type", None) or "application/octet-stream"
                        if not isinstance(mime_type, str):
                            mime_type = "application/octet-stream"
                        part = types.Part.from_bytes(data=raw_bytes, mime_type=mime_type)
                        payload.gemini_parts.append(part)
                        payload.media_badges.append("📄 Документ")
                        payload.has_media = True

        # 6. Caption (for photos, audio, documents)
        caption = getattr(msg, "caption", None)
        if isinstance(caption, str) and caption:
            text_parts.append(caption)

    payload.combined_text = "\n".join(text_parts)
    return payload


def format_incoming_summary(payload: MediaBatchPayload, max_len: int = 400) -> str:
    """Formats a clean, readable snippet for the owner's draft card in Telegram.

    If badges exist, formats:
    - [🎙 Голосовое (0:15)]\\nТекст реплики...
    - [📷 Фото] Подпись к фото...
    If only text: returns the text truncated cleanly to max_len with '…' if longer.
    """
    badges = [
        f"[{b}]" if not (b.startswith("[") and b.endswith("]")) else b
        for b in payload.media_badges
    ]
    badges_str = " ".join(badges)

    text = (payload.combined_text or "").strip()

    if not badges_str and not text:
        return ""

    if not badges_str:
        if len(text) > max_len:
            cutoff = max(0, max_len - 1)
            return text[:cutoff].rstrip() + "…"
        return text

    if not text:
        if len(badges_str) > max_len:
            cutoff = max(0, max_len - 1)
            return badges_str[:cutoff].rstrip() + "…"
        return badges_str

    if len(payload.media_badges) == 1 and payload.media_badges[0].startswith("📷") and "\n" not in text:
        combined = f"{badges_str} {text}"
    else:
        combined = f"{badges_str}\n{text}"

    if len(combined) > max_len:
        cutoff = max(0, max_len - 1)
        return combined[:cutoff].rstrip() + "…"
    return combined
