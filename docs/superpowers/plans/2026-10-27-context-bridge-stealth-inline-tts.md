# План реализации: Context Bridge, Стелс-инлайн, Лечение TTS и Concurrency

> **Дата составления:** 2026-10-27  
> **Цель:** Реализация функционала Context Bridge (пересылка сообщений в ЛС для использования в инлайне и чате), лечение инлайн-TTS (`TTS_DUMP_CHAT_ID`, кнопка активации, парсинг префиксов), стелс-режима в группах с автоочисткой кнопок через 30 секунд, защиты от дабл-клика, умной маршрутизации `/avatar` без реплая и устранения race conditions в `RateLimiter`.

---

## Архитектура и затронутые файлы

```
tg-bot-gemini-api/
├── config.py                     # tts_dump_chat_id, inline_buttons_ttl_seconds
├── .env.example                  # Документация новых параметров окружения
├── rate_limiter.py               # self._lock = asyncio.Lock() в check() и hit()
├── middlewares.py                # Гашение спиннера для неавторизованных callback_query
├── handlers/
│   ├── common.py                 # ForwardedContext, хелперы контекста, DNS-pinning,
│   │                             # стелс-статус, автоудаление кнопок, help-текст
│   ├── chat.py                   # Перехват форвардов F.forward_origin, cb_fwd_stand_now,
│   │                             # лимиты audio <= 15MB, потребление контекста в handle_text/cmd_q
│   ├── inline.py                 # _extract_tts_inline_text, TTS dump chat, fallback button,
│   │                             # пометка "С контекстом", нейтральные заглушки, защита дабл-клика
│   ├── prompts.py                # Умная маршрутизация /avatar в cmd_q без реплая
│   └── menu.py                   # Обработка /start init_tts
└── tests/
    ├── test_context_bridge.py    # Новые тесты ForwardedContext и хендлеров форвардов
    ├── test_inline_safety.py     # Тесты префиксов TTS, автоснятия кнопок, отмены тасок
    ├── test_document_validation.py # Тесты лимитов на аудиофайлы
    ├── test_rate_limiter.py      # Тест concurrency RateLimiter
    └── test_prompts.py           # Тест /avatar без реплая
```

---

## Задачи пошаговой реализации

### Задача 1. Concurrency, Middleware Spinner & Media Limits
1. **`rate_limiter.py`**:
   - Добавить `self._lock = asyncio.Lock()` в `__init__`.
   - В `check()` и `hit()` обернуть операции с деками `_minute_hits` и `_day_hits` в `async with self._lock:`.
2. **`middlewares.py`**:
   - В `AccessMiddleware`: если `not allowed` и `isinstance(event, Update) and event.callback_query`:
     вызвать `await event.callback_query.answer("⛔️ Доступ к боту ограничен.", show_alert=False)` в блоке `try/except`.
3. **`handlers/chat.py`**:
   - В `handle_voice` и `_build_reply_payload` (ветка `replied.voice or replied.audio`):
     проверять `if media.file_size and media.file_size > MAX_DOCUMENT_SIZE_BYTES:` и возвращать сообщение об ошибке размера до вызова `bot.download`.
4. **`handlers/common.py`**:
   - В `_render_info_text`: заменить упоминание `Gemini 3.7 Flash` на актуальные модели `Gemini 3.8 Flash, 4 Argon`.
   - В `try_download_image_from_url`: использовать проверенный IP хоста для запроса, предотвращая DNS Rebinding (TOCTOU).

### Задача 2. Умная маршрутизация `/avatar` без реплая
1. **`handlers/prompts.py`**:
   - В `cmd_avatar`: если вызвана команда `/avatar` или `/аватар` с аргументом `raw_arg`, и `raw_arg` не содержит `=`, не начинается с `edit `, `add `, `save `, `del `, `delete `, `rm `, `reset`, `default`, `сброс`, `дефолт`, и не совпадает с существующей личностью в `storage.find_persona_by_name_or_id`, делегировать в `await cmd_q(message)`.
2. **Тесты**:
   - Дополнить `tests/test_prompts.py` тестом вызова `/avatar <текст>` без цитирования.

### Задача 3. Лечение инлайн-TTS: `TTS_DUMP_CHAT_ID`, префиксы и кнопка активации
1. **`config.py` и `.env.example`**:
   - Добавить `tts_dump_chat_id: int = int(os.getenv("TTS_DUMP_CHAT_ID", "0") or "0")`.
2. **`handlers/inline.py`**:
   - Реализовать `_extract_tts_inline_text(raw_query: str) -> Optional[str]`, корректно распознающий:
     - `~текст` (даже слитно без пробела) и `~ текст`
     - `/tts текст` и `tts текст` (только с пробелом или слэшем)
     - `/гс текст` и `гс текст` (только с пробелом или слэшем)
   - В логике отправки войса:
     - Если `settings.tts_dump_chat_id != 0`: пробовать отправить `send_voice` туда с `disable_notification=True` и сразу удалить сообщение.
     - Если `tts_dump_chat_id == 0` или отправка не удалась: пробовать отправить `user_id`.
     - Если отправка в `user_id` вызвала ошибку (юзер не запускал бота в ЛС): **не отправлять суперадмину**, а вызвать `query.answer` с `InlineQueryResultsButton(text="🎙 Нажмите Start, чтобы включить озвучку", start_parameter="init_tts")`.
3. **`handlers/menu.py`**:
   - В `cmd_menu` (`@router.message(CommandStart())`): если `message.text` содержит `init_tts`, отправить предварительное сообщение:
     `🎙 <b>Озвучка для инлайн-режима успешно активирована!</b>\nТеперь можете вернуться в чат и использовать <code>@bot_username /tts</code>.`

### Задача 4. Мост контекста (Context Bridge: Forward в ЛС $\rightarrow$ Инлайн / ЛС)
1. **`handlers/common.py`**:
   - Константа `FORWARD_CONTEXT_TTL = 180.0`.
   - Датакласс `ForwardedContext`:
     - `text: str`
     - `sender_name: str = "Собеседник"`
     - `media_bytes: Optional[bytes] = None`
     - `mime_type: Optional[str] = None`
     - `notification_message_id: Optional[int] = None`
     - `created_at: float = field(default_factory=time.time)`
   - Хранилище: `_forwarded_contexts: dict[int, ForwardedContext] = {}`.
   - Функции `save_forwarded_context(user_id: int, ctx: ForwardedContext)` и `get_forwarded_context(user_id: int, consume: bool = False) -> Optional[ForwardedContext]`.
   - В `_process_user_turn`: добавить опциональный параметр `user_id_override: Optional[int] = None` (по умолчанию `message.from_user.id`).
   - В `_execute_inline_generation`:
     - Если у сессии нет `session.forwarded_context`, забирать через `get_forwarded_context(user_id, consume=True)`.
     - Если у контекста есть `notification_message_id`, фоново удалить техническую плашку из ЛС через `bot.delete_message(chat_id=user_id, message_id=ctx.notification_message_id)`.
     - При формировании `content_input`:
       - Для Avatar (`is_quick=True`): передавать контекст в блок `INTERLOCUTOR'S MESSAGE IN CHAT` (включая `types.Part.from_bytes` при наличии `media_bytes`).
       - Для Stand (`is_quick=False`): формировать блок `Контекст пересланного сообщения от {sender_name}:\n«««\n{text}\n»»»\n\nЗапрос:\n{raw_query}`.
2. **`handlers/chat.py`**:
   - Зарегистрировать хендлер пересылок в ЛС:
     `@router.message(F.chat.type == "private", F.forward_origin)`:
     - Проверять размер медиа ($\le 15$ МБ).
     - Извлекать имя автора из `forward_origin` (User, Chat, Hidden User).
     - Сохранять `ForwardedContext`.
     - Ставить реакцию `👀` через `message.react([ReactionTypeEmoji(emoji="👀")])` (в блоке try/except).
     - Отправлять сервисное сообщение с кнопкой `🥊 Разобрать сразу в Stand` (`callback_data="fwd_stand_now"`).
     - Сохранять `sent_msg.message_id` в `ctx.notification_message_id`.
   - Callback `cb_fwd_stand_now` (`F.data == "fwd_stand_now"`):
     - Забирать `ctx = get_forwarded_context(callback.from_user.id, consume=True)`.
     - Удалять сервисное сообщение.
     - Запускать `_process_user_turn` с `user_id_override=callback.from_user.id`.
   - В `handle_text` и `cmd_q`:
     - Если нет реплая, проверять `get_forwarded_context(user_id, consume=True)`. Если есть — использовать его как контекст ответа и удалять сервисное уведомление из ЛС.
3. **`handlers/inline.py`**:
   - В карточках `InlineQueryResultArticle`: если `get_forwarded_context(user_id, consume=False)` не пуст, добавлять префикс `📎 С контекстом • ` в `description`.

### Задача 5. Стелс инлайна, автоснятие кнопок и защита от дабл-клика
1. **`config.py` и `.env.example`**:
   - Добавить `inline_buttons_ttl_seconds: int = int(os.getenv("INLINE_BUTTONS_TTL_SECONDS", "30"))`.
2. **`handlers/common.py`**:
   - В `InlineSession` добавить поле `cleanup_task: Optional[asyncio.Task] = field(default=None, repr=False)`.
   - В `_execute_inline_generation`:
     - Для `is_quick=True` статус: `✍️ <i>...</i>`.
     - Для неинтерактивного режима `status_markup`: кнопка с `callback_data="inl_noop"`.
     - Реализовать функцию `_auto_remove_inline_buttons(bot_instance: Bot, im_id: str, sid: str, delay: int)`.
     - Планировать `session.cleanup_task` **до** блоков `return` при успешном выводе ответа.
3. **`handlers/inline.py`**:
   - Добавить dummy callback-хендлер `cb_inl_noop` (`F.data == "inl_noop"`): отвечать `await callback.answer("⏳ Генерация уже идёт...", show_alert=False)`.
   - В `cb_inl_start`:
     - Если `session.active_task and not session.active_task.done()`: отвечать `await callback.answer("⏳ Уже генерируется, подождите...", show_alert=False)` и делать `return` (не отменять и не перезапускать!).
   - В `cb_inl_fix` и `cb_inl_del`:
     - Отменять `session.cleanup_task` при наличии и удалять сессию из `_inline_sessions`.
   - В `fast_article`, `prev_article`, `pinned_articles`: нейтральный начальный текст `✍️ <i>...</i>`.

### Задача 6. Тестирование и верификация
1. Создать `tests/test_context_bridge.py` (тесты сохранения, чтения, consume, истечения TTL 180с, удаление плашки).
2. Обновить `tests/test_inline_safety.py` (тесты парсинга префиксов TTS, кнопки активации TTS, защиты от дабл-клика, автоснятия кнопок).
3. Дополнить `tests/test_document_validation.py` тестами на ограничение размера аудиофайлов.
4. Прогнать `python -m compileall .`.
5. Прогнать полный сьют тестов `python -m unittest discover -s tests -p "test_*.py"`.
