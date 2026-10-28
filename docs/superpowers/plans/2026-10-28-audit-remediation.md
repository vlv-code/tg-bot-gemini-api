# Audit Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix confirmed production defects (BUG-01, BUG-02, BUG-03, BUG-04, BUG-05, BUG-07, BUG-08, BUG-13) identified in the audit report using four parallel, isolated subagents without regressions in the existing 186-test suite.

**Architecture:** Decompose remediation into four independent domains mapped 1:1 to isolated source files and new dedicated test modules (`handlers/chat.py`, `handlers/common.py`, `handlers/inline.py`, and `handlers/business.py`). Each agent develops under strict TDD (failing test -> minimal fix -> passing test) without touching any files assigned to other agents.

**Tech Stack:** Python 3.12+, aiogram 3, google-genai, asyncio, unittest.

**Spec:** Verified findings from the external audit report on commit `9d22f2a` evaluated against current working branch at `589aade`.

## Global Constraints
- Preserve 100% test pass rate across existing test suite (186 tests).
- All new tests must use `unittest` and `unittest.mock` (AsyncMock / MagicMock) adhering to project test conventions.
- No cross-file edits across parallel tasks: each task modifies exactly one production file and one test file.
- Follow existing async conventions and retain UTF-16 and Telegram Markdown safety.

---

### Task 1: Chat / Voice Handler `storage` Import Bug (BUG-02)

**Files:**
- Modify: `c:/Users/adm_zhuravlev/Documents/my/tg-bot-gemini-api/handlers/chat.py:11-23`
- Create Test: `c:/Users/adm_zhuravlev/Documents/my/tg-bot-gemini-api/tests/test_chat_voice_turn.py`

**Interfaces:**
- Consumes: `storage` from `handlers.common`
- Produces: `handle_voice` in `handlers/chat.py` successfully calls `await storage.get_settings(user_id)` without `NameError`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_chat_voice_turn.py`:
```python
import io
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456789:TEST_MOCK_TOKEN_FOR_UNITTESTS")
os.environ.setdefault("GEMINI_API_KEY", "AIzaSy_TEST_MOCK_KEY_FOR_UNITTESTS")

from aiogram.types import Message, Voice, User, Chat
from handlers.chat import handle_voice


class TestChatVoiceHandler(unittest.IsolatedAsyncioTestCase):
    @patch("handlers.chat._process_user_turn", new_callable=AsyncMock)
    async def test_handle_voice_resolves_storage_without_name_error(self, mock_process_turn):
        fake_message = MagicMock(spec=Message)
        fake_message.voice = MagicMock(spec=Voice)
        fake_message.voice.file_id = "voice_file_123"
        fake_message.voice.mime_type = "audio/ogg"
        fake_message.caption = None
        fake_message.from_user = MagicMock(spec=User)
        fake_message.from_user.id = 999111
        fake_message.chat = MagicMock(spec=Chat)
        fake_message.chat.id = 999111
        fake_message.bot = MagicMock()

        async def fake_download(file_id, destination):
            destination.write(b"OggS\x00fake_audio_content")

        fake_message.bot.download = AsyncMock(side_effect=fake_download)

        # Calling handle_voice should not raise NameError: name 'storage' is not defined
        await handle_voice(message=fake_message)

        mock_process_turn.assert_awaited_once()
        _, kwargs = mock_process_turn.call_args
        self.assertEqual(kwargs["force_voice_reply"], True)
        self.assertEqual(kwargs["force_text_only"], False)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m unittest tests/test_chat_voice_turn.py`
Expected: FAIL with `NameError: name 'storage' is not defined` at `handlers/chat.py:669`.

- [ ] **Step 3: Write minimal implementation**

In `handlers/chat.py`:
Modify imports at lines 11-23 to import `storage`:
```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m unittest tests/test_chat_voice_turn.py`
Expected: PASS (1 test OK).

- [ ] **Step 5: Verify all tests in chat module**

Run: `.\.venv\Scripts\python.exe -m unittest discover tests`
Expected: All tests pass.

---

### Task 2: Inline Core & Background Tasks Remediation (BUG-01, BUG-03, BUG-07, BUG-08)

**Files:**
- Modify: `c:/Users/adm_zhuravlev/Documents/my/tg-bot-gemini-api/handlers/common.py:44-50,796,836-840,920-926`
- Create Test: `c:/Users/adm_zhuravlev/Documents/my/tg-bot-gemini-api/tests/test_inline_audit_fixes.py`

**Interfaces:**
- Consumes: `URL_REGEX`, `storage.get_settings`, `storage.record_token_usage`
- Produces: `_run_background_task` with error logger callback, `_execute_inline_generation` defining `url_match` before branching, calling `storage.get_settings` and recording token usage for `target_model`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_inline_audit_fixes.py`:
```python
import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456789:TEST_MOCK_TOKEN_FOR_UNITTESTS")
os.environ.setdefault("GEMINI_API_KEY", "AIzaSy_TEST_MOCK_KEY_FOR_UNITTESTS")

from handlers.common import _execute_inline_generation, _run_background_task, InlineSession, _inline_sessions


class TestInlineAuditFixes(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _inline_sessions.clear()

    @patch("handlers.common.storage")
    @patch("handlers.common.gemini_client")
    @patch("handlers.common.limiter")
    async def test_inline_generation_without_fwd_context_does_not_fail_with_name_error(
        self, mock_limiter, mock_gemini, mock_storage
    ):
        mock_storage.get_settings = AsyncMock()
        user_state = MagicMock()
        user_state.model = "gemini-2.5-flash"
        user_state.avatar_model = "gemini-3.8-flash-lite"
        user_state.system_prompt = "You are a bot"
        user_state.quick_prompt = "Quick draft"
        mock_storage.get_settings.return_value = user_state
        mock_storage.record_token_usage = AsyncMock()

        mock_limiter.check = AsyncMock(return_value=MagicMock(allowed=True))
        mock_limiter.hit = AsyncMock()

        gen_resp = MagicMock()
        gen_resp.text = "Generated text response"
        gen_resp.total_tokens = 50
        gen_resp.prompt_tokens = 20
        gen_resp.candidates_tokens = 30
        mock_gemini.ask = AsyncMock(return_value=gen_resp)

        fake_bot = MagicMock()
        fake_bot.edit_message_text = AsyncMock()

        session_id = "test_sess_1"
        _inline_sessions[session_id] = InlineSession(created_at=100.0, forwarded_context=None)

        # Should execute cleanly without NameError: name 'url_match' is not defined
        await _execute_inline_generation(
            bot=fake_bot,
            user_id=12345,
            session_id=session_id,
            inline_message_id="msg_inline_99",
            raw_query_override=" Обычный текст без ссылок ",
            is_quick=True,
        )

        # Verify BUG-08: storage.get_settings was used instead of storage.get
        mock_storage.get_settings.assert_awaited_once_with(12345)
        self.assertFalse(mock_storage.get.called)

        # Verify BUG-07: record_token_usage called with target_model (avatar_model), not state.model
        mock_storage.record_token_usage.assert_awaited_once()
        _, record_kwargs = mock_storage.record_token_usage.call_args
        self.assertEqual(record_kwargs["model"], "gemini-3.8-flash-lite")

    async def test_run_background_task_logs_exceptions(self):
        with patch("handlers.common.logger") as mock_logger:
            async def failing_coro():
                raise RuntimeError("Simulated background crash")

            task = _run_background_task(failing_coro())
            await asyncio.sleep(0.05)

            # Assert that exception was captured and logged
            mock_logger.exception.assert_called()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify failures**

Run: `.\.venv\Scripts\python.exe -m unittest tests/test_inline_audit_fixes.py`
Expected: FAIL with `NameError: name 'url_match' is not defined` and logger not called.

- [ ] **Step 3: Implement minimal fixes in `handlers/common.py`**

1. Fix BUG-03 (`_run_background_task` error logging) at line 44:
```python
def _run_background_task(coro) -> asyncio.Task:
    """Запускает фоновую задачу с защитой от сборщика мусора CPython и логированием ошибок."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)

    def _on_done(t: asyncio.Task) -> None:
        _background_tasks.discard(t)
        if not t.cancelled():
            exc = t.exception()
            if exc:
                logger.exception("Фоновая inline-задача завершилась с ошибкой: %s", exc, exc_info=exc)

    task.add_done_callback(_on_done)
    return task
```

2. Fix BUG-08 (replace `storage.get` with `storage.get_settings`) at line 796:
```python
        state = await storage.get_settings(user_id)
```

3. Fix BUG-01 (define `url_match` before branching) at line 811:
```python
        content_input: Union[str, Sequence[Union[str, types.Part]]] = raw_query
        fwd_ctx = session.forwarded_context if session else None
        url_match = URL_REGEX.search(raw_query)

        if fwd_ctx:
```

4. Fix BUG-07 (pass `target_model` to `record_token_usage`) at line 922:
```python
        if response.total_tokens > 0:
            await storage.record_token_usage(
                user_id=user_id,
                chat_id=user_id,
                model=target_model,
                prompt_tokens=response.prompt_tokens,
                candidates_tokens=response.candidates_tokens,
                total_tokens=response.total_tokens,
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m unittest tests/test_inline_audit_fixes.py`
Expected: PASS (2 tests OK).

- [ ] **Step 5: Run full test suite**

Run: `.\.venv\Scripts\python.exe -m unittest discover tests`
Expected: All tests pass.

---

### Task 3: Inline TTS Routing Alignment (BUG-04)

**Files:**
- Modify: `c:/Users/adm_zhuravlev/Documents/my/tg-bot-gemini-api/handlers/inline.py:422-424`
- Create Test: `c:/Users/adm_zhuravlev/Documents/my/tg-bot-gemini-api/tests/test_inline_tts_routing.py`

**Interfaces:**
- Consumes: `_extract_tts_inline_text` from `handlers/inline.py`
- Produces: `handle_chosen_inline_result` ignores all TTS card formats consistently (`~`, `/tts`, `tts`, `/гс`, `гс`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_inline_tts_routing.py`:
```python
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456789:TEST_MOCK_TOKEN_FOR_UNITTESTS")
os.environ.setdefault("GEMINI_API_KEY", "AIzaSy_TEST_MOCK_KEY_FOR_UNITTESTS")

from aiogram.types import ChosenInlineResult, User
from handlers.inline import handle_chosen_inline_result


class TestInlineTTSRouting(unittest.IsolatedAsyncioTestCase):
    @patch("handlers.inline._run_background_task")
    async def test_handle_chosen_inline_result_ignores_all_tts_prefixes(self, mock_run_task):
        tts_queries = [
            "/tts Привет",
            "tts Привет",
            "~Привет мир",
            "/гс Озвучь",
            "гс Озвучь этот текст",
        ]

        for q in tts_queries:
            chosen = MagicMock(spec=ChosenInlineResult)
            chosen.inline_message_id = "inline_msg_111"
            chosen.query = q
            chosen.result_id = "res_123"
            chosen.from_user = MagicMock(spec=User)
            chosen.from_user.id = 555
            chosen.bot = MagicMock()

            await handle_chosen_inline_result(chosen)

        # None of the TTS queries should have triggered background text generation
        mock_run_task.assert_not_called()

    @patch("handlers.inline._run_background_task")
    async def test_handle_chosen_inline_result_processes_normal_query(self, mock_run_task):
        chosen = MagicMock(spec=ChosenInlineResult)
        chosen.inline_message_id = "inline_msg_222"
        chosen.query = "Какая погода в Москве?"
        chosen.result_id = "res_456"
        chosen.from_user = MagicMock(spec=User)
        chosen.from_user.id = 555
        chosen.bot = MagicMock()

        await handle_chosen_inline_result(chosen)
        mock_run_task.assert_called_once()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m unittest tests/test_inline_tts_routing.py`
Expected: FAIL because `~Привет мир` or `гс Озвучь` calls `mock_run_task`.

- [ ] **Step 3: Write minimal implementation**

In `handlers/inline.py` at line 422:
Replace:
```python
    if chosen.query.strip().lower().startswith(("/tts", "tts")):
        return
```
With:
```python
    if _extract_tts_inline_text(chosen.query) is not None:
        return
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m unittest tests/test_inline_tts_routing.py`
Expected: PASS (2 tests OK).

- [ ] **Step 5: Run full test suite**

Run: `.\.venv\Scripts\python.exe -m unittest discover tests`
Expected: All tests pass.

---

### Task 4: Business Secretary Mode Concurrency & Reply Filtering (BUG-05, BUG-13)

**Files:**
- Modify: `c:/Users/adm_zhuravlev/Documents/my/tg-bot-gemini-api/handlers/business.py:160-174,533-548`
- Create Test: `c:/Users/adm_zhuravlev/Documents/my/tg-bot-gemini-api/tests/test_business_audit_fixes.py`

**Interfaces:**
- Consumes: `limiter.check`, `limiter.hit`, `user_locks.get(owner_user_id)`
- Produces: Atomic rate-limiting lock in `_generate_secretary_reply`; command bypass in `on_draft_reply`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_business_audit_fixes.py`:
```python
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456789:TEST_MOCK_TOKEN_FOR_UNITTESTS")
os.environ.setdefault("GEMINI_API_KEY", "AIzaSy_TEST_MOCK_KEY_FOR_UNITTESTS")

from aiogram.types import Message, Chat, User
from handlers.business import on_draft_reply, _generate_secretary_reply


class TestBusinessAuditFixes(unittest.IsolatedAsyncioTestCase):
    @patch("handlers.business.draft_registry")
    async def test_on_draft_reply_ignores_slash_commands(self, mock_registry):
        fake_msg = MagicMock(spec=Message)
        fake_msg.chat = MagicMock(spec=Chat)
        fake_msg.chat.type = "private"
        fake_msg.reply_to_message = MagicMock(spec=Message)
        fake_msg.reply_to_message.message_id = 1001
        fake_msg.text = "/cancel"

        mock_registry.get_draft_id_by_card.return_value = "draft_xyz"

        fake_bot = MagicMock()
        await on_draft_reply(message=fake_msg, bot=fake_bot)

        # If it is a slash command, it should exit early before retrieving draft
        mock_registry.get_draft_id_by_card.assert_not_called()

    @patch("handlers.business.limiter")
    @patch("handlers.business.storage")
    @patch("handlers.business.gemini_client")
    async def test_generate_secretary_reply_holds_owner_lock_across_generation(
        self, mock_gemini, mock_storage, mock_limiter
    ):
        mock_limiter.check = AsyncMock(return_value=MagicMock(allowed=True))
        mock_limiter.hit = AsyncMock()

        user_state = MagicMock()
        user_state.business_model = "gemini-2.5-flash"
        user_state.system_prompt = "Secretary prompt"
        mock_storage.get = AsyncMock(return_value=user_state)
        mock_storage.get_business_facts = AsyncMock(return_value=[])

        resp_mock = MagicMock()
        resp_mock.total_tokens = 0
        resp_mock.text = "Hello"
        mock_gemini.ask = AsyncMock(return_value=resp_mock)

        # Call _generate_secretary_reply
        resp, state = await _generate_secretary_reply(
            owner_user_id=111222,
            chat_id=333444,
            business_connection_id="bc_123",
            incoming_text="Привет",
            hint_texts=[],
        )
        self.assertIsNotNone(resp)
        mock_limiter.check.assert_awaited_once_with(111222)
        mock_limiter.hit.assert_awaited_once_with(111222)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m unittest tests/test_business_audit_fixes.py`
Expected: FAIL because `mock_registry.get_draft_id_by_card` was called for `/cancel`.

- [ ] **Step 3: Implement minimal fixes in `handlers/business.py`**

1. In `handlers/business.py` at `on_draft_reply`:
```python
@router.message(F.reply_to_message)
async def on_draft_reply(message: Message, bot: Bot) -> None:
    """Обрабатывает реплай владельца на карточку черновика:
    - если начинается с / -> пропускаем (команда боту: /cancel, /help, /menu)
    - если начинается с . или ! -> моментальная прямая отправка клиенту
    - если обычный текст -> докрутка черновика через Gemini
    """
    if message.chat.type != "private":
        return

    if message.text and message.text.startswith("/"):
        return

    replied = message.reply_to_message
    if replied is None:
        return
```

2. In `handlers/business.py` at `_generate_secretary_reply`:
Wrap rate limit check and acquisition atomically under `owner_user_id` lock:
```python
    priority = await _get_user_priority(owner_user_id)

    async with user_locks.get(owner_user_id):
        limit_status = await limiter.check(owner_user_id)
        if not limit_status.allowed:
            logger.info(
                "Secretary Mode: лимит запросов исчерпан для владельца %s при генерации ответа для чата %s",
                owner_user_id, chat_id,
            )
            return None, None

        async with user_locks.get((owner_user_id, chat_id)):
            async with global_queue.acquire(owner_user_id, priority=priority):
                state = await storage.get(owner_user_id, chat_id=chat_id, mode="business")
                facts = await storage.get_business_facts(owner_user_id)
                ...
                await limiter.hit(owner_user_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m unittest tests/test_business_audit_fixes.py`
Expected: PASS (2 tests OK).

- [ ] **Step 5: Run full test suite**

Run: `.\.venv\Scripts\python.exe -m unittest discover tests`
Expected: All tests pass.

---

### Task 5: Integration Verification & Clean Commit

**Files:**
- Run full regression test suite across all 190+ tests.
- Git commit message: `fix(audit): resolve voice import, inline name error, tts routing, and business concurrency bugs`

- [ ] **Step 1: Execute full test suite**

Run: `cd c:/Users/adm_zhuravlev/Documents/my/tg-bot-gemini-api; .\.venv\Scripts\python.exe -m unittest discover tests`
Expected: 190+ tests passing, 0 errors, 0 failures.

- [ ] **Step 2: Commit changes to git**

Run:
```bash
git add handlers/chat.py handlers/common.py handlers/inline.py handlers/business.py tests/
git commit -m "fix(audit): resolve voice import, inline name error, tts routing, and business concurrency bugs"
```
