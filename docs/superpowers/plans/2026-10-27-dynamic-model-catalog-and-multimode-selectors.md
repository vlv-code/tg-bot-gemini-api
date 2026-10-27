# Dynamic Model Catalog & Multi-Mode Selectors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Реализовать динамический каталог моделей Gemini с авто-синхронизацией из Google GenAI API, хранением в SQLite (с поддержкой категорий: text, tts, image_gen, embed), раздельными селекторами моделей для каждого режима (Stand, Avatar, Business, STT, TTS), динамическими кнопками в Telegram и добавлением раздела Business в главное меню `/menu`.

**Architecture:**
- В `storage.py` создаётся таблица `discovered_models` и расширяется таблица `users` полями `avatar_model`, `business_model`, `stt_model` с авто-миграцией SQLite.
- В `gemini_client.py` добавляется сервис классификации и синхронизации моделей с Google API (`models.list()`), классифицирующий модели по типам и возможностям.
- В `bot.py` настраивается фоновая периодическая синхронизация (раз в 24ч + при старте) и ручная кнопка в `/admin`.
- В `keyboards.py` и `handlers/menu.py` реализуется динамический диспетчер моделей: главное меню `/model` со статусом всех 5 режимов и динамические клавиатуры выбора модели по категориям.
- В `handlers/chat.py`, `handlers/business.py`, `handlers/inline.py` рантайм переключается на соответствующие модели режимов.
- В `keyboards.py` в `main_menu_keyboard` добавляется кнопка входа в Secretary Mode (`menu:business`).

**Tech Stack:** Python 3.12+, aiogram 3.15+, google-genai>=1.0, aiosqlite, unittest.

---

## Задачи реализации

### Задача 1: Хранилище моделей и миграция БД (`storage.py`)
- [ ] 1.1. В `storage.py` в `_init_db()` добавить создание таблицы `discovered_models`:
  ```sql
  CREATE TABLE IF NOT EXISTS discovered_models (
      model_id TEXT PRIMARY KEY,
      display_name TEXT NOT NULL,
      category TEXT NOT NULL,          -- 'text', 'tts', 'image_gen', 'embed', 'other'
      input_modalities TEXT NOT NULL,   -- 'text,image,audio,pdf'
      output_modalities TEXT NOT NULL,  -- 'text', 'audio', 'image'
      is_enabled INTEGER DEFAULT 1,
      sort_order INTEGER DEFAULT 100,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
  );
  ```
- [ ] 1.2. В `_init_db()` добавить безопасную миграцию колонок `avatar_model`, `business_model`, `stt_model` в таблицу `users`:
  ```python
  for col in ("avatar_model", "business_model", "stt_model"):
      try:
          await db.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT DEFAULT NULL")
      except Exception:
          pass
  ```
- [ ] 1.3. В датакласс `UserState` добавить поля `avatar_model: Optional[str] = None`, `business_model: Optional[str] = None`, `stt_model: Optional[str] = None`.
- [ ] 1.4. В `UserStorage` реализовать методы:
  - `upsert_discovered_models(models: list[dict]) -> None`
  - `get_available_models(category: Optional[str] = None, enabled_only: bool = True) -> list[dict]`
  - `get_available_model_ids(category: str = "text") -> list[str]`
  - `set_user_mode_model(user_id: int, mode_name: str, model_id: str) -> None` (`stand`, `avatar`, `business`, `stt`, `tts`)
  - `toggle_discovered_model(model_id: str, is_enabled: bool) -> None`
- [ ] 1.5. Написать юнит-тесты в `tests/test_discovered_models_storage.py` (сохранение моделей, фильтрация по категориям, раздельные настройки пользователей, миграция колонок).

---

### Задача 2: Авто-синхронизация моделей с Google API (`gemini_client.py`, `bot.py`)
- [ ] 2.1. В `GeminiClient` добавить метод `fetch_and_classify_models() -> list[dict]`:
  - Вызывает `self._client.aio.models.list()`.
  - Извлекает каноническое имя (убирая префикс `models/`).
  - Классифицирует по категориям:
    - `tts`: если в имени есть `tts` или в описании `speech`.
    - `image_gen`: если в имени есть `imagen` или `image`.
    - `embed`: если в имени есть `embedding`.
    - `text`: если метод поддерживает `generateContent` и не попадает в `embed` / `image_gen`.
  - Присваивает иконки и читаемые имена: ⚡️ Flash Lite, ⚖️ Flash, 🧠 Argon/Pro/Thinking.
  - Фильтрует неактуальные датированные чекпоинты (например, с датами вида `-2024` или версиями `-001`).
- [ ] 2.2. В `gemini_client.py` добавить функцию `sync_models_catalog(client: GeminiClient, storage: UserStorage) -> dict`:
  - Вызывает `fetch_and_classify_models()`.
  - Сохраняет через `storage.upsert_discovered_models()`.
  - Возвращает статистику: `{total: int, text: int, tts: int, image_gen: int}`.
- [ ] 2.3. В `bot.py` добавить фоновую задачу `start_models_sync_worker(client, storage)` (запуск при старте бота + периодический опрос каждые 24 часа).
- [ ] 2.4. Написать юнит-тесты в `tests/test_model_catalog_sync.py` (парсинг моделей из mock API, корректная классификация, вызов upsert).

---

### Задача 3: Динамическое меню `/model` и селекторы по режимам (`keyboards.py`, `handlers/menu.py`)
- [ ] 3.1. В `keyboards.py`:
  - Реализовать `models_hub_keyboard(state: UserState) -> InlineKeyboardMarkup`:
    - Кнопки для каждого режима: `[ 🥊 Stand ]`, `[ 🎭 Avatar ]`, `[ 💼 Business ]`, `[ 🎙 Voice-to-Text ]`, `[ 🎧 Озвучка TTS ]`.
    - Кнопка назад `[ 🔙 В главное меню ]`.
  - Реализовать `mode_model_selector_keyboard(mode: str, current_model: str, available_models: list[dict]) -> InlineKeyboardMarkup`:
    - Динамические кнопки для всех доступных моделей категории.
    - Отметка `✅` на текущей выбранной модели.
    - Кнопка `[ 🔙 К режимам ]`.
- [ ] 3.2. В `handlers/menu.py`:
  - Обработчик `menu:model` и `cmd_model`: отображает хаб моделей по 5 режимам с текущими выбранными моделями.
  - Обработчик `pick_mode_model:<mode>`: отображает динамический селектор моделей для выбранного режима.
  - Обработчик `set_mode_model:<mode>:<model>`: сохраняет модель в `storage.set_user_mode_model()`, перерисовывает клавиатуру с `✅` и показывает всплывающее уведомление `await callback.answer(...)`.
- [ ] 3.3. Написать юнит-тесты в `tests/test_model_selectors.py` (генерация клавиатур, переключение моделей для каждого режима, валидация).

---

### Задача 4: Интеграция выбранных моделей в хендлеры (`chat.py`, `business.py`, `inline.py`)
- [ ] 4.1. В `handlers/chat.py`:
  - В `handle_voice` (распознавание аудио/STT): использовать `state.stt_model or state.model`.
  - В `cmd_q`: использовать `state.avatar_model or state.model`.
  - В `handle_text` / обычных реплаях (Stand): использовать `state.model` (Stand-модель).
- [ ] 4.2. В `handlers/inline.py`:
  - Для `fast_article` и `prev_article` (Аватар): использовать `state.avatar_model or state.model`.
  - Для `stand_article`: использовать `state.model`.
  - Для `tts`: использовать `state.tts_model`.
- [ ] 4.3. В `handlers/business.py`:
  - Для генерации черновиков и автоответов использовать `owner_state.business_model or owner_state.model`.
- [ ] 4.4. Написать юнит-тесты в `tests/test_mode_model_runtime.py` (проверка использования соответствующей модели в каждом хендлере).

---

### Задача 5: Добавление Business Mode в `/menu` и синхронизация в `/admin`
- [ ] 5.1. В `keyboards.py`:
  - В `main_menu_keyboard()` добавить кнопку `💼 Бизнес-ассистент (Secretary Mode)` с `callback_data="menu:business"`.
  - В `admin_keyboard()` добавить кнопку `🔄 Обновить модели из Google` с `callback_data="admin:sync_models"`.
- [ ] 5.2. В `handlers/menu.py`:
  - Обработчик `F.data == "menu:business"` направляет в `cb_business_menu(callback)`.
- [ ] 5.3. В `handlers/admin.py`:
  - Обработчик `F.data == "admin:sync_models"`: вызывает `sync_models_catalog()`, выводит результат в Telegram (`alert` или обновление текста: "Синхронизировано моделей: N").
- [ ] 5.4. Написать юнит-тесты в `tests/test_menu_business_and_admin_sync.py`.

---

### Задача 6: Документация, компиляция, тесты и пуш на GitHub
- [ ] 6.1. Обновить `README.md` с описанием хаба моделей по режимам, динамического каталога и кнопки Business Mode в главном меню.
- [ ] 6.2. Прогнать `python -m compileall .`.
- [ ] 6.3. Запустить полный набор тестов (`python -m unittest discover -s tests -p "test_*.py"`).
- [ ] 6.4. Закоммитить изменения и отправить на GitHub через прокси.
