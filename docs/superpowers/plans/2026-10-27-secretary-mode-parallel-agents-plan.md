# Secretary Mode 2.0 (Debounce, Multimodal, Reply-to-Edit) Implementation Plan for Parallel Agents

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:dispatching-parallel-agents or superpowers:subagent-driven-development to implement tasks across parallel worker agents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Реализовать анти-спам склейку сообщений клиентов (Debounce 3.5с), обработку голосовых и фото с саммари, а также докрутку черновиков реплаем (Reply-to-Edit) через изолированные модули, разрабатываемые параллельными агентами.

**Architecture:** Архитектура разделена на 3 независимых модуля без общего состояния (Zero Shared State), что позволяет 3 субагентам работать одновременно без конфликтов слияния:
1. `debounce.py` — автономный In-Memory менеджер задержки и агрегации очередей сообщений.
2. `media_batch.py` — загрузчик и упаковщик мультимодальных медиа (аудио, фото) в `types.Part` и генератор `<blockquote>`-выжимок.
3. `draft_editor.py` — селектор и парсер реплаев владельца (`.` / `!` для мгновенной отправки, обычный текст для корректуры).
4. `handlers/business.py` — интеграционный слой (координируется главным агентом после завершения параллельных треков).

**Tech Stack:** Python 3.12+, aiogram 3.x, google-genai SDK, aiosqlite, asyncio, unittest/pytest.

---

## Global Constraints

- Все новые модули должны быть строго типизированы и изолированы в отдельных файлах.
- Обратная совместимость: текущие тесты (132/132) должны оставаться зелеными.
- Никаких блокирующих операций в event loop (все I/O асинхронные, таймеры через `asyncio.call_later` или `asyncio.sleep`).
- Никаких внешних тяжелых зависимостей: используем встроенный `asyncio`, существующий `aiogram` и `google-genai`.

---

## Parallel Execution Matrix

| Агент | Ответственность | Создаваемые файлы | Тесты | Зависимости |
|---|---|---|---|---|
| **Agent 1** (Debounce) | In-Memory буфер очередей сообщений | `debounce.py` | `tests/test_debounce.py` | Нет (автономен) |
| **Agent 2** (Media Batch) | Загрузка voice/photo, упаковка в Part, саммари | `media_batch.py` | `tests/test_media_batch.py` | Нет (автономен) |
| **Agent 3** (Draft Editor) | Связка карточек черновиков, парсинг Reply-to-Edit | `draft_editor.py` | `tests/test_draft_editor.py` | Нет (автономен) |
| **Lead / Integrator** | Подключение модулей в пайплайн Secretary Mode | `handlers/business.py`, `config.py` | `tests/test_business_pipeline.py` | Ждет агентов 1, 2, 3 |

---

### Task 1: [Agent 1] Debounce Buffer Manager

**Files:**
- Create: `debounce.py`
- Test: `tests/test_debounce.py`

**Interfaces:**
- Produces:
  ```python
  class AccumulatedMessage:
      text: str = ""
      has_media: bool = False
      voice_bytes: Optional[bytes] = None
      photo_bytes: Optional[bytes] = None
      created_at: float

  class DebounceManager:
      def __init__(self, delay_seconds: float = 3.5, on_flush: Callable[[tuple, list[AccumulatedMessage]], Awaitable[None]]): ...
      async def push(self, key: tuple, item: AccumulatedMessage) -> None: ...
      async def flush(self, key: tuple) -> Optional[list[AccumulatedMessage]]: ...
      async def cancel(self, key: tuple) -> None: ...
      def active_keys_count(self) -> int: ...
  ```

- [ ] **Step 1: Write the failing test**
  Создать `tests/test_debounce.py`, проверяющий:
  1. Сброс таймера при последовательном поступлении сообщений быстрее delay.
  2. Срабатывание колбэка `on_flush` ровно через `delay_seconds` тишины со всеми склеенными сообщениями.
  3. Корректную отмену таймера при ручном `flush` или `cancel`.

- [ ] **Step 2: Run test to verify it fails**
  `python -m unittest tests/test_debounce.py`
  Ожидается: FAIL (ImportError / ModuleNotFoundError `debounce`).

- [ ] **Step 3: Write minimal implementation in `debounce.py`**
  Реализовать `DebounceManager` на базе `asyncio.get_running_loop().call_later` с безопасным управлением словарем очередей и замками `asyncio.Lock`.

- [ ] **Step 4: Run test to verify it passes**
  `python -m unittest tests/test_debounce.py`
  Ожидается: PASS (все тесты зеленые).

- [ ] **Step 5: Commit**
  `git add debounce.py tests/test_debounce.py`
  `git commit -m "feat(debounce): add in-memory debounce manager for message batching"`

---

### Task 2: [Agent 2] Multimodal Media Batch & Summary Builder

**Files:**
- Create: `media_batch.py`
- Test: `tests/test_media_batch.py`

**Interfaces:**
- Produces:
  ```python
  class MediaBatchPayload:
      combined_text: str
      gemini_parts: list[Any]  # google.genai.types.Part
      media_badges: list[str]  # ["🎙 Голосовое (0:24)", "📷 Фото"]

  async def build_media_batch(
      bot: Bot,
      messages: list[Message],
  ) -> MediaBatchPayload: ...

  def format_incoming_summary(payload: MediaBatchPayload, max_len: int = 400) -> str: ...
  ```

- [ ] **Step 1: Write the failing test**
  Создать `tests/test_media_batch.py`, мокающий `bot.download()`:
  1. Тест сбора текстовых реплик: склеивание через `\n`.
  2. Тест упаковки голосового: скачивание файла, создание `Part.from_bytes(data, mime_type="audio/ogg")`, добавление бейджа `🎙 Голосовое`.
  3. Тест упаковки фото: выбор самого крупного `photo[-1]`, `Part.from_bytes(data, mime_type="image/jpeg")`, бейдж `📷 Фото`.
  4. Форматирование блока `<blockquote>` с иконками и текстом.

- [ ] **Step 2: Run test to verify it fails**
  `python -m unittest tests/test_media_batch.py`
  Ожидается: FAIL (ImportError).

- [ ] **Step 3: Write minimal implementation in `media_batch.py`**
  Реализовать функции безопасного скачивания с лимитами размера (макс 20 МБ) и формированием payload для Gemini.

- [ ] **Step 4: Run test to verify it passes**
  `python -m unittest tests/test_media_batch.py`
  Ожидается: PASS.

- [ ] **Step 5: Commit**
  `git add media_batch.py tests/test_media_batch.py`
  `git commit -m "feat(media): add media batching and summary formatting helpers"`

---

### Task 3: [Agent 3] Draft Registry & Reply-to-Edit Parser

**Files:**
- Create: `draft_editor.py`
- Test: `tests/test_draft_editor.py`

**Interfaces:**
- Produces:
  ```python
  class DraftActionType(Enum):
      INSTANT_SEND = "instant_send"  # starts with . or !
      REVISE_INSTRUCTION = "revise"  # regular text prompt for Gemini

  @dataclass
  class ParsedReplyAction:
      action_type: DraftActionType
      text: str
      draft_id: str

  class DraftRegistry:
      def register_card(self, card_message_id: int, draft_id: str) -> None: ...
      def get_draft_id_by_card(self, card_message_id: int) -> Optional[str]: ...
      def remove_draft(self, draft_id: str) -> None: ...

  def parse_reply_action(reply_text: str, draft_id: str) -> ParsedReplyAction: ...
  ```

- [ ] **Step 1: Write the failing test**
  Создать `tests/test_draft_editor.py`:
  1. Тест регистрации сообщения карточки черновика `register_card(msg_id, draft_id)`.
  2. Тест парсинга мгновенной отправки: `.Привет, доставим в четверг` -> `INSTANT_SEND`, текст `Привет, доставим в четверг`.
  3. Тест парсинга с восклицательным знаком: `!Хорошо, согласен` -> `INSTANT_SEND`.
  4. Тест обычной правки: `сделай тон вежливее и добавь скидку 5%` -> `REVISE_INSTRUCTION`.

- [ ] **Step 2: Run test to verify it fails**
  `python -m unittest tests/test_draft_editor.py`
  Ожидается: FAIL.

- [ ] **Step 3: Write minimal implementation in `draft_editor.py`**
  Реализовать класс `DraftRegistry` и парсер `parse_reply_action`.

- [ ] **Step 4: Run test to verify it passes**
  `python -m unittest tests/test_draft_editor.py`
  Ожидается: PASS.

- [ ] **Step 5: Commit**
  `git add draft_editor.py tests/test_draft_editor.py`
  `git commit -m "feat(drafts): add draft registry and reply-to-edit action parser"`

---

### Task 4: [Lead / Integrator] End-to-End Integration in `handlers/business.py`

**Files:**
- Modify: `config.py` (добавить `business_debounce_seconds: float = 3.5`)
- Modify: `handlers/business.py` (интегрировать Debounce, MediaBatch и Reply-to-Edit хендлер)
- Test: `tests/test_business_pipeline.py`

- [ ] **Step 1: Write integration tests**
  Проверить полный цикл:
  - Пачка из 3 текстовых сообщений клиента попадает в `DebounceManager` и сливается в 1 вызов.
  - Входящее фото/голос передаются в `GeminiClient.ask()` с `parts`.
  - Владелец реплаит на карточку черновика с `.текст` — ответ улетает в `business_connection_id`.
  - Владелец реплаит обычным текстом — карточка черновика обновляется новым вариантом от Gemini.

- [ ] **Step 2: Connect modules in `handlers/business.py`**
  - Подключить `DebounceManager` в `on_business_message`.
  - Обрабатывать сработавший батч через `build_media_batch`.
  - Добавить хендлер реплаев в личке бота: `@router.message(F.reply_to_message)`.

- [ ] **Step 3: Run full project test suite**
  `python -m unittest discover tests`
  Ожидается: 132 существующих + все новые тесты PASS.

- [ ] **Step 4: Commit and finalize**
  `git add config.py handlers/business.py tests/test_business_pipeline.py`
  `git commit -m "feat(business): integrate debounce batching, multimodal drafts, and reply-to-edit"`
