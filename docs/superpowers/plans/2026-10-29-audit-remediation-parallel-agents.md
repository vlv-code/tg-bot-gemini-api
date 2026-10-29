# Audit Remediation (AUD-01...AUD-20) Parallel Subagents Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:dispatching-parallel-agents` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement all confirmed and accepted fixes from the 2026-10-29 audit report across five completely isolated, concurrent subagents without regression in the existing 202-test suite.

**Architecture:** Decompose all 19 accepted remediation items into five disjoint functional streams mapped strictly 1:1 to isolated production files and dedicated test modules. Each subagent operates in its own file space under strict TDD (Red -> Green -> Refactor), avoiding git merge conflicts or shared-state races.

**Tech Stack:** Python 3.12+, aiogram 3.31+, google-genai 2.20+, aiosqlite, aiohttp, unittest / pytest.

**Spec:** Final decisions on audit findings AUD-01…AUD-25 agreed between auditor and implementer on commit `19725b2`.

## Global Constraints
- Preserve 100% test pass rate across existing 202 tests.
- Zero file overlap between parallel subagents. Each subagent touches only its designated files.
- All new tests must use `unittest` / `unittest.mock` (`AsyncMock`, `MagicMock`) conforming to project style (offline, no external network calls).
- Keep `callback_data` length $\le 64$ bytes on all buttons.
- Do not mutate frozen aiogram/pydantic models.

---

## Streams Overview (Subagent Allocation)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       ORCHESTRATOR / LEAD AGENT                             │
└──────┬──────────────┬──────────────┬──────────────┬──────────────┬──────────┘
       │              │              │              │              │
       ▼              ▼              ▼              ▼              ▼
┌──────────────┐┌──────────────┐┌──────────────┐┌──────────────┐┌──────────────┐
│  Subagent 1  ││  Subagent 2  ││  Subagent 3  ││  Subagent 4  ││  Subagent 5  │
│ Core Client  ││Chat Security ││Menu & Prompts││   Business   ││   Inline &   │
│  & Storage   ││   Router     ││   Guardian   ││ Media Engine ││    Infra     │
│              ││              ││              ││              ││              │
│ AUD-01,09,   ││ AUD-02,05,   ││ AUD-03,04,   ││ AUD-06,07,   ││ AUD-08,14,   │
│ 16, 18       ││ 09b, 20      ││ 11, 12a, 19  ││ 10, 15a      ││ 15b, 17      │
└──────┬───────┘└──────┬───────┘└──────┬───────┘└──────┬───────┘└──────┬───────┘
       │              │              │              │              │
       └──────────────┴──────────────┼──────────────┴──────────────┘
                                     ▼
                     RUN TEST SUITE (202+ tests) & PUSH
```

| Subagent | Role | Assigned Files | Addressed Items |
| :--- | :--- | :--- | :--- |
| **Subagent 1** | `Core Engine Engineer` | `gemini_client.py`, `storage.py`, `requirements.txt`, `tests/test_core_client_audit.py` | AUD-01, AUD-09 (Core), AUD-16, AUD-18 |
| **Subagent 2** | `Chat Security Engineer` | `handlers/chat.py`, `handlers/common.py`, `tests/test_chat_security_audit.py` | AUD-02, AUD-05, AUD-09 (Callers), AUD-20, AUD-12 (common) |
| **Subagent 3** | `Menu & Prompts Engineer` | `handlers/prompts.py`, `handlers/menu.py`, `keyboards.py`, `tests/test_prompts_and_menu_audit.py` | AUD-03, AUD-04, AUD-11, AUD-12 (menu/prompts), AUD-19 |
| **Subagent 4** | `Business Media Engineer` | `handlers/business.py`, `media_batch.py`, `tests/test_business_audit_fixes.py` | AUD-06, AUD-07, AUD-10, AUD-15 (media limit) |
| **Subagent 5** | `Inline & Infra Engineer` | `handlers/inline.py`, `middlewares.py`, `.dockerignore`, `README.md`, `tests/test_inline_audit_fixes.py` | AUD-08, AUD-14, AUD-15 (README), AUD-17, AUD-12 (inline) |

---

## Detailed Subagent Specifications

### Subagent 1: Core Client & Storage Engine
**Scope:** `gemini_client.py`, `storage.py`, `requirements.txt`  
**Test File:** `tests/test_core_client_audit.py`

- [ ] **Step 1: Write failing tests in `tests/test_core_client_audit.py`:**
  - Test AUD-01: `types.Model(supported_actions=["generateContent"], name="models/gemma-3-27b-it")` is categorized as `"text"`.
  - Test AUD-09: `GeminiClient.ask()` returns `GeminiResponse` containing `used_model` equal to the actual model that answered.
  - Test AUD-16: `upsert_discovered_models` does not disable models if baseline models are missing from listing (completeness guard); soft-disables (`is_enabled=0`) unpinned models missing from complete fresh list; preserves `is_pinned=1` models.
  - Test AUD-18: `generate_speech` passes `response_mime_type="audio/L16;codec=pcm;rate=24000"` in `GenerateContentConfig`.
- [ ] **Step 2: Run test suite to verify RED state.**
- [ ] **Step 3: Implement minimal changes:**
  - Update `requirements.txt`: `google-genai>=2.0` (AUD-01).
  - In `gemini_client.py`:
    - In `fetch_and_classify_models`:
      ```python
      methods = list(getattr(m, "supported_actions", None) or getattr(m, "supported_generation_methods", None) or [])
      ```
    - In `generate_speech`:
      Pass `response_mime_type="audio/L16;codec=pcm;rate=24000"` in `GenerateContentConfig`.
    - In `GeminiResponse`: add `used_model: str = ""`.
    - In `GeminiClient.ask`: populate `used_model=current_model` when returning `GeminiResponse`.
  - In `storage.py`:
    - Ensure `discovered_models` table migration has `is_pinned INTEGER DEFAULT 0`.
    - In `upsert_discovered_models`: check if `set(settings.available_models).issubset({m["model_id"] for m in models})`. If complete, run soft-disable:
      `UPDATE discovered_models SET is_enabled = 0, updated_at = ? WHERE is_pinned = 0 AND model_id NOT IN (...)`.
    - In `set_model_enabled(model_id, is_enabled)`: set `is_pinned = 1`.
- [ ] **Step 4: Verify tests turn GREEN.**

---

### Subagent 2: Chat & Security Router
**Scope:** `handlers/chat.py`, `handlers/common.py`  
**Test File:** `tests/test_chat_security_audit.py`

- [ ] **Step 1: Write failing tests in `tests/test_chat_security_audit.py`:**
  - Test AUD-02: Message in `chat.type in ("group", "supergroup")` without bot mention or bot reply is ignored.
  - Test AUD-02: Message in group with `@bot_username` mention or reply to bot is processed, and `@bot_username` is stripped from incoming prompt text.
  - Test AUD-05: When `fwd_stand_now` runs, the response's "🎙 Озвучить ответ" button receives `speak_response:{user_id}`, NOT the bot's ID.
  - Test AUD-09: Token usage in `_process_user_turn` records `response.used_model` instead of requested model.
  - Test AUD-20: Fallback inline generation for expired session strips persona prefix (`/bro ` etc.) via `_parse_inline_query_intent`.
- [ ] **Step 2: Run test suite to verify RED state.**
- [ ] **Step 3: Implement minimal changes:**
  - In `handlers/chat.py`:
    - Add helper `_should_process_group_message(message: Message, bot_username: str) -> tuple[bool, str]`:
      If `message.chat.type in ("group", "supergroup")`:
        - If `message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.id == message.bot.id`: allow.
        - If mention entity points to `@bot_username`: allow, and strip `@bot_username` from text/caption.
        - Else: return `(False, "")`.
      If private: return `(True, text)`.
    - Apply this gate at the start of `handle_text`, `handle_photo`, `handle_voice`, `handle_document`.
    - In `cb_fwd_stand_now`: pass `user_id_override=user_id` into `_process_user_turn`.
  - In `handlers/common.py`:
    - In `_send_response`: add parameter `author_id: Optional[int] = None`. Use `author_id` (falling back to `message.from_user.id`) when building `speak_response:{author_id}`.
    - In `_process_user_turn`: pass `author_id = user_id_override or (message.from_user.id if message.from_user else 0)`.
    - In `_process_user_turn`: pass `model = response.used_model or target_model` to `storage.record_token_usage`.
    - In `_execute_inline_generation`: in fallback branch when session expired, re-parse query intent using `_parse_inline_query_intent(raw_query_override)` to eliminate persona prefix.
    - Remove unused `import hashlib`, `import io` (AUD-12).
- [ ] **Step 4: Verify tests turn GREEN.**

---

### Subagent 3: Prompts & Menu Guardian
**Scope:** `handlers/prompts.py`, `handlers/menu.py`, `keyboards.py`  
**Test File:** `tests/test_prompts_and_menu_audit.py`

- [ ] **Step 1: Write failing tests in `tests/test_prompts_and_menu_audit.py`:**
  - Test AUD-03: `/prompt Как решить x=y в Python?` sets the prompt without creating a corrupted preset named `Как решить x`.
  - Test AUD-03: `/prompt save Кодер = Ты опытный программист` successfully creates and activates preset `Кодер`.
  - Test AUD-03: Similar protection works for `/avatar` and `/qprompt`.
  - Test AUD-04: Callback `set_tts_model:invalid-model` and `set_tts_voice:invalid-voice` are rejected with alert and do not mutate DB.
  - Test AUD-04: Dead route `set_model:` no longer exists.
  - Test AUD-19: Long model IDs in `mode_models_keyboard` remain unambiguous and parse cleanly on click.
- [ ] **Step 2: Run test suite to verify RED state.**
- [ ] **Step 3: Implement minimal changes:**
  - In `handlers/prompts.py`:
    - Remove UTF-8 BOM (`\xef\xbb\xbf`) from top of file (AUD-11).
    - Add helper:
      ```python
      def _parse_prompt_preset_assignment(raw_arg: str) -> Optional[tuple[str, str]]:
          if "=" not in raw_arg:
              return None
          left, p_text = raw_arg.split("=", 1)
          left = left.strip()
          p_text = p_text.strip()
          has_explicit_prefix = False
          for prefix in ("add ", "save ", "edit ", "изменить ", "сохранить ", "добавить ", "ред "):
              if left.lower().startswith(prefix):
                  left = left[len(prefix):].strip()
                  has_explicit_prefix = True
                  break
          if has_explicit_prefix and left and p_text:
              return left, p_text
          if left and p_text and len(left) <= 32 and not any(ch in left for ch in ("?", "\n")):
              return left, p_text
          return None
      ```
    - Use this helper in `cmd_prompt`, `cmd_avatar`, `cmd_qprompt`.
    - Remove dead `chat_id = ...` unused variable assignments (AUD-12).
  - In `keyboards.py`:
    - Remove dead function `models_keyboard` (AUD-04, AUD-23).
    - In `mode_models_keyboard`: ensure clean hash truncation format for model IDs (AUD-19).
  - In `handlers/menu.py`:
    - Delete dead callback route `@router.callback_query(F.data.startswith(("set_model:", "model:")))` and `cb_set_model` (AUD-04).
    - In `cb_set_tts_model`: validate `model in settings.available_tts_models` or `await storage.is_model_enabled(model)` (AUD-04).
    - In `cb_set_tts_voice`: validate `voice in settings.available_voices` (AUD-04).
    - Remove unused `models_keyboard` import (AUD-12).
- [ ] **Step 4: Verify tests turn GREEN.**

---

### Subagent 4: Business & Media Pipeline
**Scope:** `handlers/business.py`, `media_batch.py`  
**Test File:** `tests/test_business_audit_fixes.py`

- [ ] **Step 1: Write failing tests in `tests/test_business_audit_fixes.py`:**
  - Test AUD-07: In Secretary Mode, incoming document (PDF/DOCX $\le 15$ MB) is downloaded and converted to a `Part` in `build_media_batch`.
  - Test AUD-07: Message with document passes entry filter in `on_business_message`.
  - Test AUD-10: When a message arrives in Secretary Mode, user turn is NOT added to history until the draft is sent or auto-reply fires. If draft is rejected, history remains empty.
  - Test AUD-06: Onboarding message and business texts document that keyword templates send instantly without Gemini, while Gemini replies require owner tap.
  - Test AUD-15: Document > 15 MB is skipped with warning badge.
- [ ] **Step 2: Run test suite to verify RED state.**
- [ ] **Step 3: Implement minimal changes:**
  - In `media_batch.py`:
    - Change `MAX_MEDIA_BYTES = 15 * 1024 * 1024` (15 MB) (AUD-15).
    - Add document processing block in `build_media_batch`:
      ```python
      doc = getattr(msg, "document", None)
      if doc is not None:
          file_id = getattr(doc, "file_id", None)
          file_size = getattr(doc, "file_size", 0) or 0
          if file_size > MAX_MEDIA_BYTES:
              payload.media_badges.append("⚠️ Документ > 15 МБ пропущен")
          elif isinstance(file_id, str) and file_id:
              raw_bytes = await _download_file_bytes(bot, file_id)
              if raw_bytes is not None:
                  mime = getattr(doc, "mime_type", None) or "application/octet-stream"
                  part = types.Part.from_bytes(data=raw_bytes, mime_type=mime)
                  payload.gemini_parts.append(part)
                  payload.media_badges.append("📄 Документ")
                  payload.has_media = True
      ```
  - In `handlers/business.py`:
    - In `on_business_message`: include `has_document = bool(message.document and getattr(message.document, "file_id", None))` in entry gate (AUD-07).
    - In `_process_batched_business_messages`:
      Remove line 418: `await storage.add_turn(owner_user_id, "user", turn_text, chat_id=chat_id, mode="business")` (AUD-10).
      In auto-reply branch (`if auto_reply:`):
        ```python
        await storage.add_turn(owner_user_id, "user", turn_text, chat_id=chat_id, mode="business")
        await storage.add_turn(owner_user_id, "model", response.text, chat_id=chat_id, mode="business")
        ```
      In draft card creation (`PendingDraft`):
        Store `turn_text: str = turn_text` in `PendingDraft`.
      In `cb_draft_send`:
        Before sending or after sending to client:
        `await storage.add_turn(owner_user_id, "user", draft.turn_text or "[Запрос]", chat_id=draft.chat_id, mode="business")`
        `await storage.add_turn(owner_user_id, "model", final_text, chat_id=draft.chat_id, mode="business")`.
    - In `_generate_secretary_reply`: record token usage with `model=response.used_model or business_model` (AUD-09).
    - Update onboarding text in `handlers/business.py:360-365` (AUD-06).
- [ ] **Step 4: Verify tests turn GREEN.**

---

### Subagent 5: Inline, Middleware & Docs
**Scope:** `handlers/inline.py`, `middlewares.py`, `.dockerignore`, `README.md`  
**Test File:** `tests/test_inline_audit_fixes.py`

- [ ] **Step 1: Write failing tests in `tests/test_inline_audit_fixes.py`:**
  - Test AUD-17: Unauthorized user calling inline query receives `query.answer([], cache_time=0)`.
  - Test AUD-08: Inline TTS generates speech with single model (`models_limit=1`) and timeout 9s.
  - Test AUD-08: When inline TTS times out at 9s, user gets placeholder article "Аудио готовится, повторите запрос...", while background task completes and saves result to `tts_cache`.
- [ ] **Step 2: Run test suite to verify RED state.**
- [ ] **Step 3: Implement minimal changes:**
  - In `middlewares.py`:
    - In `AccessMiddleware`: if user not allowed and `event.inline_query`:
      `await event.inline_query.answer([], cache_time=0)` (AUD-17).
  - In `handlers/inline.py`:
    - Limit inline TTS model chain to 1 model and timeout budget 9s (AUD-08).
    - If generation takes > 9s: return placeholder article; spawn background worker to finish and write `save_cached_tts_voice` (AUD-08).
    - If user limit is exhausted, return inline article explaining limits instead of silent return (AUD-08).
    - Remove unused `from typing import Union` (AUD-12).
  - In `.dockerignore`:
    - Add `data/`, `tests/`, `docs/`, `.pytest_cache/`, `conftest.py`, `*.pyc` (AUD-14).
  - In `README.md`:
    - Document all env variables (`DEFAULT_AVATAR_MODEL`, `DEFAULT_BUSINESS_MODEL`, `DEFAULT_STT_MODEL`, `MODELS_SYNC_INTERVAL_HOURS`, `GEMINI_TIMEOUT_SECONDS`, `BUSINESS_DEBOUNCE_SECONDS`) (AUD-15).
    - Update media size mentions to 15 MB (AUD-15).
    - Update business secretary copy regarding instant keyword templates (AUD-06).
- [ ] **Step 4: Verify tests turn GREEN.**

---

## Orchestrator Verification & Integration

- [ ] Run full test suite: `python -m unittest discover tests` (expected: 220+ tests passed).
- [ ] Run compileall: `python -m compileall .`
- [ ] Verify git status and diff across all 5 streams.
- [ ] Commit and push to `origin/main`.
- [ ] Sync to `tg-bot-ai-pro`.
