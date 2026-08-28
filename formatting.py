"""Rich-режим на базе telegramify-markdown.

Идея: не парсить наш собственный HTML/MarkdownV2 руками, а взять
markdown от Gemini как есть и прогнать через convert() — он возвращает
(plain_text, entities). Entities — это ровно тот механизм, которым сам
Telegram размечает текст внутри себя, поэтому:
  - parse_mode не нужен вообще;
  - никакого экранирования спецсимволов MarkdownV2 не требуется —
    сырой текст остаётся как есть, разметка идёт отдельным списком.

split_entities() режет длинный ответ на чанки под лимит Telegram (4096
UTF-16 юнитов), корректно разрезая entities на границах чанков.
"""

from aiogram.types import MessageEntity as AiogramEntity
from telegramify_markdown import convert, split_entities


def markdown_to_chunks(markdown_text: str, max_len: int = 3900) -> list[tuple[str, list[AiogramEntity]]]:
    """Конвертирует markdown Gemini в список (текст, entities) чанков,
    готовых к message.answer(text, entities=entities)."""
    text, entities = convert(markdown_text)
    return [
        (chunk_text, _to_aiogram_entities(chunk_entities))
        for chunk_text, chunk_entities in split_entities(text, entities, max_utf16_len=max_len)
    ]


def _to_aiogram_entities(entities) -> list[AiogramEntity]:
    # namedtuple/dataclass telegramify_markdown.MessageEntity -> aiogram.types.MessageEntity.
    # Поля user/unix_time/date_time_format отбрасываем: они нужны только для
    # text_mention и Rich Message (richify()), а обычный convert() их не производит.
    return [
        AiogramEntity(
            type=e.type,
            offset=e.offset,
            length=e.length,
            url=e.url,
            language=e.language,
            custom_emoji_id=e.custom_emoji_id,
        )
        for e in entities
    ]


def utf16_len(text: str) -> int:
    """Длина строки в UTF-16 code units — так же, как считает лимиты Telegram.
    len(text) в питоне считает кодовые точки: для символов вне BMP (суррогатные
    пары — большинство эмодзи, часть иероглифов) это 1 вместо реальных 2."""
    return len(text.encode("utf-16-le")) // 2


def find_utf16_cut(text: str, limit: int) -> int:
    """Бинарным поиском находит максимальный индекс среза (в питоновских
    символах), при котором UTF-16-длина text[:idx] не превышает limit."""
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if utf16_len(text[:mid]) <= limit:
            lo = mid
        else:
            hi = mid - 1
    return lo


def split_plain_text(text: str, limit: int = 3900) -> list[str]:
    """Для plain-режима (rich выключен) — режем на чанки под лимит Telegram
    (в UTF-16 юнитах, не в питоновских символах — см. utf16_len), стараясь
    рвать по границам строк."""
    if not text:
        return [""]
    if utf16_len(text) <= limit:
        return [text]

    chunks: list[str] = []
    while text:
        if utf16_len(text) <= limit:
            chunks.append(text)
            break
        cut = find_utf16_cut(text, limit)
        split_at = text.rfind("\n", 0, cut)
        if split_at <= 0:
            split_at = cut
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks
