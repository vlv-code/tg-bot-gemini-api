import os
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:FAKE_TOKEN_FOR_TESTS")
os.environ.setdefault("GEMINI_API_KEY", "FAKE_KEY_FOR_TESTS")

import handlers.business as biz
from gemini_client import GeminiResponse

OWNER_ID = 1000
CONN_ID = "conn-test"
CLIENT_CHAT_ID = 555555


def _fake_bot():
    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=lambda **kw: MagicMock(**kw))
    return bot


def _fake_connection_event(conn_id=CONN_ID, owner_id=OWNER_ID, can_reply=True, is_enabled=True):
    event = MagicMock()
    event.id = conn_id
    event.user = MagicMock(id=owner_id)
    event.user_chat_id = owner_id
    event.is_enabled = is_enabled
    event.rights = MagicMock(can_reply=can_reply)
    return event


def _fake_business_message(text, from_user_id, conn_id=CONN_ID, chat_id=CLIENT_CHAT_ID, first_name="Клиент"):
    msg = MagicMock()
    msg.business_connection_id = conn_id
    msg.chat = MagicMock(id=chat_id, first_name=first_name, title=None, full_name=first_name)
    msg.from_user = MagicMock(id=from_user_id)
    msg.text = text
    msg.caption = None
    msg.answer = AsyncMock()
    return msg


def _fake_callback(user_id, data, message=None):
    cb = MagicMock()
    cb.from_user = MagicMock(id=user_id)
    cb.data = data
    cb.message = message or MagicMock(edit_text=AsyncMock())
    cb.answer = AsyncMock()
    return cb


class TestSecretaryModeFlow(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp()
        biz.storage._db_path = os.path.join(self.test_dir, "smoke.db")
        biz.storage._db = None
        await biz.storage.init_db()
        biz._pending_drafts.clear()
        # limiter/global_queue — модульные синглтоны в handlers.common, общие для
        # всех тестов файла; сбрасываем состояние OWNER_ID, иначе к концу файла
        # накопленные хиты по одному и тому же user_id исчерпывают per-minute лимит
        biz.limiter._minute_hits.pop(OWNER_ID, None)
        biz.limiter._day_hits.pop(OWNER_ID, None)

    async def asyncTearDown(self):
        await biz.storage.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    async def test_connection_saved_and_owner_notified(self):
        bot = _fake_bot()
        await biz.on_business_connection(_fake_connection_event(), bot)

        conn = await biz.storage.get_business_connection(CONN_ID)
        self.assertIsNotNone(conn)
        self.assertEqual(conn["owner_user_id"], OWNER_ID)
        self.assertTrue(conn["can_reply"])
        bot.send_message.assert_awaited_once()

    async def test_connection_without_reply_rights_warns_owner(self):
        bot = _fake_bot()
        await biz.on_business_connection(_fake_connection_event(can_reply=False), bot)
        conn = await biz.storage.get_business_connection(CONN_ID)
        self.assertFalse(conn["can_reply"])
        # Уведомление всё равно уходит, просто с предупреждением о правах
        bot.send_message.assert_awaited_once()

    async def test_default_mode_creates_draft_not_direct_reply(self):
        await biz.on_business_connection(_fake_connection_event(), _fake_bot())
        biz.gemini_client.ask = AsyncMock(
            return_value=GeminiResponse(text="Отвечу чуть позже.", total_tokens=10)
        )
        bot = _fake_bot()
        incoming = _fake_business_message("Вы работаете по выходным?", from_user_id=222222)
        await biz.on_business_message(incoming, bot)

        incoming.answer.assert_not_awaited()
        bot.send_message.assert_awaited_once()
        kwargs = bot.send_message.await_args.kwargs
        self.assertEqual(kwargs["chat_id"], OWNER_ID)
        self.assertIn("reply_markup", kwargs)
        self.assertEqual(len(biz._pending_drafts), 1)

    async def test_send_draft_goes_to_counterparty_with_business_connection_id(self):
        await biz.on_business_connection(_fake_connection_event(), _fake_bot())
        biz.gemini_client.ask = AsyncMock(return_value=GeminiResponse(text="Черновик", total_tokens=5))
        await biz.on_business_message(
            _fake_business_message("Вопрос", from_user_id=222222), _fake_bot()
        )
        draft_id = next(iter(biz._pending_drafts))

        bot = _fake_bot()
        cb = _fake_callback(OWNER_ID, f"biz_draft:send:{draft_id}")
        await biz.cb_business_draft(cb, bot)

        bot.send_message.assert_awaited_once()
        kwargs = bot.send_message.await_args.kwargs
        self.assertEqual(kwargs["chat_id"], CLIENT_CHAT_ID)
        self.assertEqual(kwargs["business_connection_id"], CONN_ID)
        self.assertNotIn(draft_id, biz._pending_drafts)

    async def test_stranger_cannot_act_on_someone_elses_draft(self):
        await biz.on_business_connection(_fake_connection_event(), _fake_bot())
        biz.gemini_client.ask = AsyncMock(return_value=GeminiResponse(text="Черновик", total_tokens=5))
        await biz.on_business_message(
            _fake_business_message("Вопрос", from_user_id=222222), _fake_bot()
        )
        draft_id = next(iter(biz._pending_drafts))

        bot = _fake_bot()
        intruder_cb = _fake_callback(999999, f"biz_draft:send:{draft_id}")
        await biz.cb_business_draft(intruder_cb, bot)

        intruder_cb.answer.assert_awaited_with("Это не ваш черновик.", show_alert=True)
        bot.send_message.assert_not_awaited()
        self.assertIn(draft_id, biz._pending_drafts)

    async def test_discard_removes_draft_without_sending(self):
        await biz.on_business_connection(_fake_connection_event(), _fake_bot())
        biz.gemini_client.ask = AsyncMock(return_value=GeminiResponse(text="Черновик", total_tokens=5))
        await biz.on_business_message(
            _fake_business_message("Вопрос", from_user_id=222222), _fake_bot()
        )
        draft_id = next(iter(biz._pending_drafts))

        bot = _fake_bot()
        cb = _fake_callback(OWNER_ID, f"biz_draft:discard:{draft_id}")
        await biz.cb_business_draft(cb, bot)

        bot.send_message.assert_not_awaited()
        self.assertNotIn(draft_id, biz._pending_drafts)

    async def test_autoreply_button_enables_flag_and_sends_pending_draft(self):
        await biz.on_business_connection(_fake_connection_event(), _fake_bot())
        biz.gemini_client.ask = AsyncMock(return_value=GeminiResponse(text="Черновик", total_tokens=5))
        await biz.on_business_message(
            _fake_business_message("Вопрос", from_user_id=222222), _fake_bot()
        )
        draft_id = next(iter(biz._pending_drafts))

        bot = _fake_bot()
        cb = _fake_callback(OWNER_ID, f"biz_draft:autoreply:{draft_id}")
        await biz.cb_business_draft(cb, bot)

        self.assertTrue(await biz.storage.is_auto_reply_enabled(CONN_ID, CLIENT_CHAT_ID))
        bot.send_message.assert_awaited_once()  # уже накопленный черновик всё равно уходит

    async def test_auto_reply_enabled_sends_directly_via_message_answer(self):
        await biz.on_business_connection(_fake_connection_event(), _fake_bot())
        await biz.storage.set_auto_reply(CONN_ID, CLIENT_CHAT_ID, True)

        biz.gemini_client.ask = AsyncMock(
            return_value=GeminiResponse(text="Да, работаем по субботам.", total_tokens=8)
        )
        bot = _fake_bot()
        incoming = _fake_business_message("А по субботам?", from_user_id=222222)
        await biz.on_business_message(incoming, bot)

        incoming.answer.assert_awaited()
        bot.send_message.assert_not_awaited()  # владельцу черновик не шлём — авто-ответ уже включён

    async def test_owners_own_message_in_business_chat_is_ignored(self):
        await biz.on_business_connection(_fake_connection_event(), _fake_bot())
        bot = _fake_bot()
        own_message = _fake_business_message("Отвечу сам", from_user_id=OWNER_ID)
        await biz.on_business_message(own_message, bot)

        own_message.answer.assert_not_awaited()
        bot.send_message.assert_not_awaited()
        self.assertEqual(len(biz._pending_drafts), 0)

    async def test_unknown_connection_is_ignored(self):
        bot = _fake_bot()
        incoming = _fake_business_message("Привет", from_user_id=222222, conn_id="never-registered")
        await biz.on_business_message(incoming, bot)
        incoming.answer.assert_not_awaited()
        bot.send_message.assert_not_awaited()

    async def test_owner_removed_from_whitelist_after_connecting_is_blocked(self):
        """Defense-in-depth: business_connection мог быть создан, пока владелец был
        в вайтлисте, а потом его оттуда убрали — Secretary Mode должен это уважать,
        хотя AccessMiddleware для business_message клиента такое не ловит."""
        await biz.on_business_connection(_fake_connection_event(), _fake_bot())
        biz.gemini_client.ask = AsyncMock(side_effect=AssertionError("не должен вызываться"))

        bot = _fake_bot()
        incoming = _fake_business_message("Здравствуйте", from_user_id=222222)
        with patch.object(biz.storage, "is_user_allowed", AsyncMock(return_value=False)):
            await biz.on_business_message(incoming, bot)

        incoming.answer.assert_not_awaited()
        bot.send_message.assert_not_awaited()
        self.assertEqual(len(biz._pending_drafts), 0)

    # --- Ключевые слова и факты о бизнесе ---

    async def test_template_keyword_bypasses_gemini_entirely(self):
        await biz.on_business_connection(_fake_connection_event(), _fake_bot())
        await biz.storage.save_business_keyword_rule(
            OWNER_ID, "цена", "template", "Актуальные цены на сайте example.com"
        )
        biz.gemini_client.ask = AsyncMock(side_effect=AssertionError("Gemini не должен вызываться для шаблона"))

        bot = _fake_bot()
        incoming = _fake_business_message("А какая у вас цена на услугу?", from_user_id=222222)
        await biz.on_business_message(incoming, bot)

        incoming.answer.assert_awaited()
        call_text = incoming.answer.await_args.args[0]
        self.assertIn("example.com", call_text)
        # Шаблон не создаёт черновик — отправляется мгновенно
        self.assertEqual(len(biz._pending_drafts), 0)

    async def test_template_keyword_works_even_when_auto_reply_off(self):
        """Шаблон — это заранее одобренный владельцем текст, поэтому уходит сразу,
        независимо от точечного авто-ответа (тот управляет только Gemini-ответами)."""
        await biz.on_business_connection(_fake_connection_event(), _fake_bot())
        self.assertFalse(await biz.storage.is_auto_reply_enabled(CONN_ID, CLIENT_CHAT_ID))
        await biz.storage.save_business_keyword_rule(OWNER_ID, "стоп", "template", "Шаблонный ответ")
        biz.gemini_client.ask = AsyncMock(side_effect=AssertionError("не должен вызываться"))

        bot = _fake_bot()
        incoming = _fake_business_message("стоп акция", from_user_id=222222)
        await biz.on_business_message(incoming, bot)

        incoming.answer.assert_awaited()
        bot.send_message.assert_not_awaited()  # черновик владельцу не шлём

    async def test_hint_keyword_and_facts_are_injected_into_prompt(self):
        await biz.on_business_connection(_fake_connection_event(), _fake_bot())
        await biz.storage.save_business_keyword_rule(
            OWNER_ID, "жалоба", "hint", "Отвечай с эмпатией, предложи скидку 10%"
        )
        await biz.storage.save_business_fact(OWNER_ID, "Часы работы", "Пн-Пт 9:00-18:00")

        captured_kwargs = {}

        async def fake_ask(**kwargs):
            captured_kwargs.update(kwargs)
            return GeminiResponse(text="Понимаю ваше недовольство...", total_tokens=20)

        biz.gemini_client.ask = AsyncMock(side_effect=fake_ask)

        incoming = _fake_business_message("У меня жалоба на качество", from_user_id=222222)
        await biz.on_business_message(incoming, _fake_bot())

        prompt = captured_kwargs["system_prompt"]
        self.assertIn("Отвечай с эмпатией", prompt)
        self.assertIn("Часы работы", prompt)
        self.assertIn("Пн-Пт 9:00-18:00", prompt)
        # Черновик всё равно создаётся — hint не отменяет обычный флоу подтверждения
        self.assertEqual(len(biz._pending_drafts), 1)

    async def test_no_matching_keyword_still_works_normally(self):
        await biz.on_business_connection(_fake_connection_event(), _fake_bot())
        biz.gemini_client.ask = AsyncMock(return_value=GeminiResponse(text="Обычный ответ", total_tokens=5))
        incoming = _fake_business_message("Просто вопрос без триггеров", from_user_id=222222)
        await biz.on_business_message(incoming, _fake_bot())
        self.assertEqual(len(biz._pending_drafts), 1)

    # --- Бизнес-меню ---

    async def test_bizfact_command_saves_and_lists(self):
        message = MagicMock()
        message.from_user = MagicMock(id=OWNER_ID)
        message.text = "/bizfact Часы работы = Пн-Пт 9:00-18:00"
        message.answer = AsyncMock()
        await biz.cmd_bizfact(message)
        message.answer.assert_awaited_once()
        self.assertIn("сохранён", message.answer.await_args.args[0])

        facts = await biz.storage.get_business_facts(OWNER_ID)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["fact_key"], "Часы работы")

    async def test_bizkeyword_command_invalid_type_shows_help(self):
        message = MagicMock()
        message.from_user = MagicMock(id=OWNER_ID)
        message.text = "/bizkeyword something without proper format"
        message.answer = AsyncMock()
        await biz.cmd_bizkeyword(message)
        help_text = message.answer.await_args.args[0]
        self.assertIn("Формат", help_text)
        self.assertEqual(await biz.storage.get_business_keyword_rules(OWNER_ID), [])

    async def test_bizkeyword_command_saves_template_rule(self):
        message = MagicMock()
        message.from_user = MagicMock(id=OWNER_ID)
        message.text = "/bizkeyword template цена = Смотрите цены на сайте"
        message.answer = AsyncMock()
        await biz.cmd_bizkeyword(message)

        rules = await biz.storage.get_business_keyword_rules(OWNER_ID)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["rule_type"], "template")

    async def test_bizmenu_fact_delete_respects_owner(self):
        await biz.storage.save_business_fact(OWNER_ID, "Факт", "Значение")
        facts = await biz.storage.get_business_facts(OWNER_ID)
        fact_id = facts[0]["id"]

        stranger_cb = _fake_callback(999999, f"biz_fact_del:{fact_id}")
        await biz.cb_business_fact_delete(stranger_cb)
        stranger_cb.answer.assert_awaited_with("Не найдено")
        self.assertEqual(len(await biz.storage.get_business_facts(OWNER_ID)), 1)

        owner_cb = _fake_callback(OWNER_ID, f"biz_fact_del:{fact_id}")
        await biz.cb_business_fact_delete(owner_cb)
        owner_cb.answer.assert_awaited_with("Удалено ✅")
        self.assertEqual(await biz.storage.get_business_facts(OWNER_ID), [])

    async def test_bizmenu_navigation_renders_facts_section(self):
        await biz.storage.save_business_fact(OWNER_ID, "Адрес", "ул. Ленина, 1")
        cb = _fake_callback(OWNER_ID, "bizmenu:facts")
        await biz.cb_business_menu(cb)
        cb.message.edit_text.assert_awaited()
        rendered_text = cb.message.edit_text.await_args.args[0]
        self.assertIn("Адрес", rendered_text)

    # --- Перегенерация и смена стиля черновика ---

    async def test_regenerate_updates_same_draft_with_jitter_hint(self):
        await biz.on_business_connection(_fake_connection_event(), _fake_bot())
        biz.gemini_client.ask = AsyncMock(
            return_value=GeminiResponse(text="Первый вариант", total_tokens=5)
        )
        await biz.on_business_message(
            _fake_business_message("Какой у вас график?", from_user_id=222222), _fake_bot()
        )
        draft_id = next(iter(biz._pending_drafts))
        self.assertEqual(biz._pending_drafts[draft_id].draft_text, "Первый вариант")

        captured = {}

        async def fake_ask(**kwargs):
            captured.update(kwargs)
            return GeminiResponse(text="Второй, другой вариант", total_tokens=6)

        biz.gemini_client.ask = AsyncMock(side_effect=fake_ask)
        bot = _fake_bot()
        fake_msg = MagicMock(edit_text=AsyncMock())
        cb = _fake_callback(OWNER_ID, f"biz_draft:regen:{draft_id}", fake_msg)
        await biz.cb_business_draft(cb, bot)

        # Тот же draft_id, но текст обновился — новый черновик не создаётся
        self.assertIn(draft_id, biz._pending_drafts)
        self.assertEqual(biz._pending_drafts[draft_id].draft_text, "Второй, другой вариант")
        self.assertIn("SYSTEM NOTE", captured["message"])
        fake_msg.edit_text.assert_awaited()

    async def test_style_button_cycles_persona_and_regenerates(self):
        await biz.on_business_connection(_fake_connection_event(), _fake_bot())
        biz.gemini_client.ask = AsyncMock(return_value=GeminiResponse(text="Черновик", total_tokens=5))
        await biz.on_business_message(
            _fake_business_message("Вопрос", from_user_id=222222), _fake_bot()
        )
        draft_id = next(iter(biz._pending_drafts))
        self.assertEqual(biz._pending_drafts[draft_id].persona_name, "")  # изначально без стиля

        captured = {}

        async def fake_ask(**kwargs):
            captured.update(kwargs)
            return GeminiResponse(text="Ответ в стиле", total_tokens=6)

        biz.gemini_client.ask = AsyncMock(side_effect=fake_ask)
        cb = _fake_callback(OWNER_ID, f"biz_draft:style:{draft_id}")
        await biz.cb_business_draft(cb, _fake_bot())

        draft = biz._pending_drafts[draft_id]
        self.assertNotEqual(draft.persona_name, "")
        self.assertEqual(draft.draft_text, "Ответ в стиле")
        # Стиль личности (её prompt) должен попасть в system_prompt, а не инструкция-суфлёр Avatar
        self.assertIn("VOICE/PERSONALITY", captured["system_prompt"])
        self.assertNotIn("GHOSTWRITER", captured["system_prompt"])

    async def test_style_button_cycles_through_different_personas_on_repeat_taps(self):
        await biz.on_business_connection(_fake_connection_event(), _fake_bot())
        biz.gemini_client.ask = AsyncMock(return_value=GeminiResponse(text="Черновик", total_tokens=5))
        await biz.on_business_message(
            _fake_business_message("Вопрос", from_user_id=222222), _fake_bot()
        )
        draft_id = next(iter(biz._pending_drafts))

        biz.gemini_client.ask = AsyncMock(return_value=GeminiResponse(text="Ответ 1", total_tokens=5))
        await biz.cb_business_draft(_fake_callback(OWNER_ID, f"biz_draft:style:{draft_id}"), _fake_bot())
        first_persona = biz._pending_drafts[draft_id].persona_id

        biz.gemini_client.ask = AsyncMock(return_value=GeminiResponse(text="Ответ 2", total_tokens=5))
        await biz.cb_business_draft(_fake_callback(OWNER_ID, f"biz_draft:style:{draft_id}"), _fake_bot())
        second_persona = biz._pending_drafts[draft_id].persona_id

        self.assertNotEqual(first_persona, second_persona)

    async def test_regenerate_and_style_only_owner_allowed(self):
        await biz.on_business_connection(_fake_connection_event(), _fake_bot())
        biz.gemini_client.ask = AsyncMock(return_value=GeminiResponse(text="Черновик", total_tokens=5))
        await biz.on_business_message(
            _fake_business_message("Вопрос", from_user_id=222222), _fake_bot()
        )
        draft_id = next(iter(biz._pending_drafts))
        original_text = biz._pending_drafts[draft_id].draft_text

        stranger_cb = _fake_callback(999999, f"biz_draft:regen:{draft_id}")
        await biz.cb_business_draft(stranger_cb, _fake_bot())
        stranger_cb.answer.assert_awaited_with("Это не ваш черновик.", show_alert=True)
        self.assertEqual(biz._pending_drafts[draft_id].draft_text, original_text)

    # --- История не засоряется отклонёнными/перегенерированными черновиками ---

    async def test_discarded_draft_does_not_pollute_history(self):
        await biz.on_business_connection(_fake_connection_event(), _fake_bot())
        biz.gemini_client.ask = AsyncMock(return_value=GeminiResponse(text="Черновик", total_tokens=5))
        await biz.on_business_message(
            _fake_business_message("Вопрос", from_user_id=222222), _fake_bot()
        )
        draft_id = next(iter(biz._pending_drafts))

        cb = _fake_callback(OWNER_ID, f"biz_draft:discard:{draft_id}")
        await biz.cb_business_draft(cb, _fake_bot())

        state = await biz.storage.get(OWNER_ID, chat_id=CLIENT_CHAT_ID, mode="business")
        roles = [t.role for t in state.history]
        self.assertEqual(roles, ["user"])  # только реплика клиента, ответа модели в истории нет

    async def test_regenerated_then_sent_draft_writes_only_final_text_to_history(self):
        await biz.on_business_connection(_fake_connection_event(), _fake_bot())
        biz.gemini_client.ask = AsyncMock(return_value=GeminiResponse(text="Черновик v1", total_tokens=5))
        await biz.on_business_message(
            _fake_business_message("Вопрос", from_user_id=222222), _fake_bot()
        )
        draft_id = next(iter(biz._pending_drafts))

        biz.gemini_client.ask = AsyncMock(return_value=GeminiResponse(text="Черновик v2 (финальный)", total_tokens=5))
        await biz.cb_business_draft(
            _fake_callback(OWNER_ID, f"biz_draft:regen:{draft_id}"), _fake_bot()
        )

        bot = _fake_bot()
        await biz.cb_business_draft(_fake_callback(OWNER_ID, f"biz_draft:send:{draft_id}"), bot)

        state = await biz.storage.get(OWNER_ID, chat_id=CLIENT_CHAT_ID, mode="business")
        model_turns = [t.text for t in state.history if t.role == "model"]
        # В истории должен быть только финальный (перегенерированный) вариант, не оба
        self.assertEqual(model_turns, ["Черновик v2 (финальный)"])


if __name__ == "__main__":
    unittest.main()
