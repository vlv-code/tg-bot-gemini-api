# План реализации: Рефакторинг и багфикс tg-bot-gemini-api

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Актуализировать эндпоинты Gemini API, устранить гонки и избыточные блокировки в Business и Inline режимах, предотвратить утечки памяти на документах/сессиях, обеспечить строгую сериализацию транзакций SQLite без deadlock'ов и решить коллизию команд `/avatar` и `/q`.

**Architecture:** 
1. Актуализация конфигурации моделей, эндпоинтов и надежности SDK (`google.genai.types.HttpRetryOptions`).
2. Разрешение диспетчеризации `/avatar` vs `/q` через контекст реплая/наличия личностей.
3. Гранулярные блокировки по ключам `(owner_user_id, chat_id)` в Business Mode.
4. Отмена in-flight фоновых задач в `InlineSession`.
5. Реентрантная сериализация пишущих транзакций (`AsyncRLock`) в `UserStorage`.
6. Предвалидация MIME и размера документов до выделения оперативной памяти в `chat.py`.
7. Пул соединений `aiohttp.ClientSession` для SSRF-безопасных скачиваний.

**Tech Stack:** Python 3.12+, `aiogram 3.x`, `google-genai 2.20.0`, `aiosqlite 0.21.0`, `aiohttp`, `unittest`.

**Spec:** ТЗ на рефакторинг и багфикс `tg-bot-gemini-api` (7 задач + чек-лист проверки).

## Global Constraints
- Сохранять 100% обратную совместимость схемы SQLite (`storage.py`).
- Не допускать deadlock'ов при сериализации транзакций в SQLite (использовать `AsyncRLock`).
- Все тесты должны оставаться зелеными (запуск через `unittest discover`).

---

### Task 1: Актуализация моделей Gemini, TTS-эндпоинтов и ретраев SDK

**Files:**
- Modify: `config.py:22-30`, `config.py:54-65`
- Modify: `.env.example:7-15`
- Modify: `gemini_client.py:25-35`, `gemini_client.py:100-115`, `gemini_client.py:190-205`
- Modify: `storage.py:35-37`, `storage.py:46-48`
- Modify: `tests/test_storage.py:20-25`, `tests/test_admin_handlers.py:20-25`, `tests/test_inline_safety.py`

**Interfaces:**
- Consumes: `google.genai.types.HttpRetryOptions`, `HttpOptions`
- Produces: Обновленные списки моделей `AVAILABLE_MODELS`, `AVAILABLE_TTS_MODELS`, корректные модальности `["AUDIO"]` для всех TTS моделей

- [ ] **Step 1: Написать тест на HttpRetryOptions и модальности TTS в test_gemini_client.py / test_inline_safety.py**
- [ ] **Step 2: Запустить тест и убедиться, что он выявляет текущее поведение**
- [ ] **Step 3: Обновить config.py, .env.example, storage.py и gemini_client.py**
- [ ] **Step 4: Прогнать тесты и убедиться, что они проходят**

---

### Task 2: Устранение коллизии команд /avatar и /q

**Files:**
- Modify: `handlers/prompts.py:112-175`
- Modify: `bot.py:12-30`
- Modify: `README.md:100-120`
- Test: `tests/test_prompts.py` (или `tests/test_handlers_router.py`)

**Interfaces:**
- Consumes: `handlers.chat.cmd_q`, `storage.find_persona_by_name_or_id`
- Produces: Прозрачная маршрутизация: `/avatar` с реплаем или свободным текстом $\rightarrow$ `cmd_q`; `/avatar` с `=` или служебными ключами или именем личности $\rightarrow$ управление; `/avatars` $\rightarrow$ управление.

- [ ] **Step 1: Написать тест на диспетчеризацию /avatar с реплаем и без реплая**
- [ ] **Step 2: Запустить тест и увидеть падение**
- [ ] **Step 3: Реализовать логику диспетчеризации в handlers/prompts.py**
- [ ] **Step 4: Обновить BOT_COMMANDS в bot.py и документацию в README.md**
- [ ] **Step 5: Прогнать тесты и убедиться в успехе**

---

### Task 3: Снятие глобального лока владельца в Business-режиме

**Files:**
- Modify: `locks.py:31-38`
- Modify: `handlers/business.py:220-245`
- Test: `tests/test_locks.py`, `tests/test_business_handlers.py`

**Interfaces:**
- Consumes: `Hashable` ключи в `UserLocks.get(key)`
- Produces: Параллельная обработка разных клиентов одного бизнес-аккаунта под ключом `(owner_user_id, chat_id)`

- [ ] **Step 1: Написать тест в test_locks.py на кортежные ключи (tuple[int, int])**
- [ ] **Step 2: Запустить тест, убедиться в поведении**
- [ ] **Step 3: Обновить UserLocks в locks.py (LockKey = Hashable)**
- [ ] **Step 4: Обновить _generate_secretary_reply в handlers/business.py: короткий лок owner_user_id на проверку лимита, составной лок на генерацию**
- [ ] **Step 5: Прогнать тесты locks и business_handlers**

---

### Task 4: Отмена дублирующихся in-flight задач в Inline-режиме

**Files:**
- Modify: `handlers/common.py:115-125` (InlineSession), `handlers/common.py:624-770` (_execute_inline_generation)
- Modify: `handlers/inline.py:414-470`, `handlers/inline.py:530-555` (cb_inl_del)
- Test: `tests/test_inline_safety.py`

**Interfaces:**
- Consumes: `session.active_task: Optional[asyncio.Task]`
- Produces: Гарантированная отмена предыдущей генерации при кликах `inl_regen`, `inl_style`, `inl_del` без спама в Telegram

- [ ] **Step 1: Написать тест на отмену session.active_task при повторном запросе генерации**
- [ ] **Step 2: Убедиться в падении теста**
- [ ] **Step 3: Добавить active_task в InlineSession и обработку CancelledError в _execute_inline_generation**
- [ ] **Step 4: Добавить вызов cancel() в cb_inl_start, cb_inl_regen, cb_inl_style, cb_inl_del**
- [ ] **Step 5: Прогнать тесты и убедиться, что они зеленые**

---

### Task 5: Реентрантная сериализация пишущих транзакций в storage.py

**Files:**
- Modify: `locks.py` (добавить `AsyncRLock`)
- Modify: `storage.py:40-70`, `storage.py:310-335`, методы записи
- Test: `tests/test_storage.py`

**Interfaces:**
- Consumes: `AsyncRLock` из `locks.py`
- Produces: Потокобезопасные транзакции в `UserStorage` без риска взаимной блокировки (deadlock) при вложенных вызовах

- [ ] **Step 1: Написать тест на реентрантную блокировку и параллельные записи в БД**
- [ ] **Step 2: Реализовать AsyncRLock в locks.py**
- [ ] **Step 3: Интегрировать self._write_lock в UserStorage; оптимизировать get()/get_settings()**
- [ ] **Step 4: Прогнать все тесты test_storage.py и убедиться в отсутствии deadlock**

---

### Task 6: Валидация размера и MIME-типа документов до скачивания в RAM

**Files:**
- Modify: `handlers/chat.py:25-50`, `handlers/chat.py:424-456`
- Test: `tests/test_document_validation.py`

**Interfaces:**
- Consumes: `aiogram.types.Document`
- Produces: `_validate_document(doc: Document) -> Optional[str]` и ранняя отбивка некорректных файлов до `bot.download`

- [ ] **Step 1: Написать unit-тест на _validate_document с превышением размера и запрещенными MIME-типами**
- [ ] **Step 2: Реализовать константы и хелпер _validate_document в handlers/chat.py**
- [ ] **Step 3: Встроить валидацию в handle_document и _build_reply_payload**
- [ ] **Step 4: Запустить тесты валидации документов**

---

### Task 7: Переиспользование aiohttp.ClientSession

**Files:**
- Modify: `handlers/common.py:40-50`, `handlers/common.py:95-130`
- Modify: `bot.py:57-63`
- Test: `tests/test_common_session.py`

**Interfaces:**
- Consumes: `aiohttp.ClientSession`
- Produces: `get_http_session() -> aiohttp.ClientSession` и `close_http_session() -> None`

- [ ] **Step 1: Написать тест на синглтон сессии и её закрытие**
- [ ] **Step 2: Реализовать get_http_session и close_http_session в handlers/common.py**
- [ ] **Step 3: Подключить close_http_session в bot.py:main finally**
- [ ] **Step 4: Прогнать тесты**

---

### Task 8: Финальная сквозная верификация

- [ ] **Step 1: Запустить полный набор unit-тестов проекта (`python -m unittest discover -s tests -p "test_*.py" -v`)**
- [ ] **Step 2: Запустить `compileall` для проверки синтаксиса всех модулей**
- [ ] **Step 3: Проверить `git status` и сформировать итоговый отчет**
