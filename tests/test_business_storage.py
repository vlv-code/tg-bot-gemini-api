import os
import shutil
import tempfile
import unittest

# Устанавливаем фейковые токены для тестов до импорта модулей
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:FAKE_TOKEN_FOR_TESTS")
os.environ.setdefault("GEMINI_API_KEY", "FAKE_KEY_FOR_TESTS")

from storage import UserStorage


class TestBusinessStorage(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_bot.db")
        self.storage = UserStorage(
            db_path=self.db_path,
            default_model="gemini-3.5-flash-lite",
            max_history=5,
        )
        await self.storage.init_db()

    async def asyncTearDown(self):
        await self.storage.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    async def test_unknown_connection_returns_none(self):
        conn = await self.storage.get_business_connection("does-not-exist")
        self.assertIsNone(conn)

    async def test_upsert_and_get_business_connection(self):
        await self.storage.upsert_business_connection(
            business_connection_id="conn-1",
            owner_user_id=111,
            user_chat_id=111,
            can_reply=True,
            is_enabled=True,
        )
        conn = await self.storage.get_business_connection("conn-1")
        self.assertIsNotNone(conn)
        self.assertEqual(conn["owner_user_id"], 111)
        self.assertEqual(conn["user_chat_id"], 111)
        self.assertTrue(conn["can_reply"])
        self.assertTrue(conn["is_enabled"])

    async def test_upsert_updates_existing_connection_rights(self):
        await self.storage.upsert_business_connection(
            "conn-2", owner_user_id=222, user_chat_id=222, can_reply=True, is_enabled=True
        )
        # Владелец отозвал право отвечать — Telegram присылает новый апдейт по тому же id
        await self.storage.upsert_business_connection(
            "conn-2", owner_user_id=222, user_chat_id=222, can_reply=False, is_enabled=True
        )
        conn = await self.storage.get_business_connection("conn-2")
        self.assertFalse(conn["can_reply"])

    async def test_auto_reply_defaults_to_disabled(self):
        enabled = await self.storage.is_auto_reply_enabled("conn-3", chat_id=555)
        self.assertFalse(enabled)

    async def test_set_auto_reply_toggle(self):
        await self.storage.set_auto_reply("conn-4", chat_id=777, enabled=True)
        self.assertTrue(await self.storage.is_auto_reply_enabled("conn-4", chat_id=777))

        await self.storage.set_auto_reply("conn-4", chat_id=777, enabled=False)
        self.assertFalse(await self.storage.is_auto_reply_enabled("conn-4", chat_id=777))

    async def test_auto_reply_is_scoped_per_chat(self):
        await self.storage.set_auto_reply("conn-5", chat_id=1, enabled=True)
        self.assertTrue(await self.storage.is_auto_reply_enabled("conn-5", chat_id=1))
        # Другой чат в рамках того же подключения — не должен затронуться
        self.assertFalse(await self.storage.is_auto_reply_enabled("conn-5", chat_id=2))

    async def test_track_business_chat_does_not_reset_auto_reply(self):
        await self.storage.set_auto_reply("conn-6", chat_id=9, enabled=True)
        # Новое сообщение в том же чате обновляет заголовок, но не должно сбрасывать флаг
        await self.storage.track_business_chat("conn-6", chat_id=9, chat_title="Иван Иванов")
        self.assertTrue(await self.storage.is_auto_reply_enabled("conn-6", chat_id=9))

        chats = await self.storage.get_auto_reply_chats("conn-6")
        self.assertEqual(len(chats), 1)
        self.assertEqual(chats[0]["chat_id"], 9)
        self.assertEqual(chats[0]["chat_title"], "Иван Иванов")

    async def test_get_auto_reply_chats_excludes_draft_mode_chats(self):
        await self.storage.set_auto_reply("conn-7", chat_id=1, enabled=True)
        await self.storage.track_business_chat("conn-7", chat_id=2, chat_title="Черновик-режим чат")
        # chat_id=2 никогда не включал авто-ответ — не должен попасть в список
        chats = await self.storage.get_auto_reply_chats("conn-7")
        chat_ids = [c["chat_id"] for c in chats]
        self.assertIn(1, chat_ids)
        self.assertNotIn(2, chat_ids)

    async def test_get_business_connections_for_owner(self):
        await self.storage.upsert_business_connection(
            "conn-8a", owner_user_id=888, user_chat_id=888, can_reply=True, is_enabled=True
        )
        await self.storage.upsert_business_connection(
            "conn-8b", owner_user_id=888, user_chat_id=888, can_reply=True, is_enabled=True
        )
        await self.storage.upsert_business_connection(
            "conn-other", owner_user_id=999, user_chat_id=999, can_reply=True, is_enabled=True
        )

        connections = await self.storage.get_business_connections_for_owner(888)
        self.assertEqual(len(connections), 2)
        ids = {c["business_connection_id"] for c in connections}
        self.assertEqual(ids, {"conn-8a", "conn-8b"})

    async def test_business_history_isolated_from_main_and_quick_history(self):
        user_id = 333
        chat_id = 4242
        await self.storage.add_turn(user_id, "user", "Привет из личного чата", mode="main")
        await self.storage.add_turn(user_id, "user", "Черновик для Аватара", mode="quick")
        await self.storage.add_turn(user_id, "user", "Сообщение от клиента", chat_id=chat_id, mode="business")

        business_state = await self.storage.get(user_id, chat_id=chat_id, mode="business")
        self.assertEqual(len(business_state.history), 1)
        self.assertEqual(business_state.history[0].text, "Сообщение от клиента")

        main_state = await self.storage.get(user_id, mode="main")
        self.assertEqual(len(main_state.history), 1)
        self.assertEqual(main_state.history[0].text, "Привет из личного чата")

    async def test_business_history_scoped_per_counterparty_chat(self):
        owner_id = 444
        await self.storage.add_turn(owner_id, "user", "От Алисы", chat_id=1001, mode="business")
        await self.storage.add_turn(owner_id, "user", "От Бориса", chat_id=1002, mode="business")

        alice_state = await self.storage.get(owner_id, chat_id=1001, mode="business")
        boris_state = await self.storage.get(owner_id, chat_id=1002, mode="business")

        self.assertEqual([t.text for t in alice_state.history], ["От Алисы"])
        self.assertEqual([t.text for t in boris_state.history], ["От Бориса"])

    # --- Факты о бизнесе ---

    async def test_save_and_get_business_facts(self):
        owner_id = 5001
        ok = await self.storage.save_business_fact(owner_id, "Часы работы", "Пн-Пт 9:00-18:00")
        self.assertTrue(ok)
        facts = await self.storage.get_business_facts(owner_id)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["fact_key"], "Часы работы")
        self.assertEqual(facts[0]["fact_value"], "Пн-Пт 9:00-18:00")

    async def test_save_business_fact_updates_existing_key(self):
        owner_id = 5002
        await self.storage.save_business_fact(owner_id, "Адрес", "ул. Первая, 1")
        await self.storage.save_business_fact(owner_id, "Адрес", "ул. Вторая, 2")
        facts = await self.storage.get_business_facts(owner_id)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["fact_value"], "ул. Вторая, 2")

    async def test_delete_business_fact_by_key(self):
        owner_id = 5003
        await self.storage.save_business_fact(owner_id, "Телефон", "+7 900 000-00-00")
        deleted = await self.storage.delete_business_fact(owner_id, "Телефон")
        self.assertTrue(deleted)
        self.assertEqual(await self.storage.get_business_facts(owner_id), [])

    async def test_delete_business_fact_by_id_respects_owner(self):
        owner_id, stranger_id = 5004, 9999
        await self.storage.save_business_fact(owner_id, "Факт", "Значение")
        facts = await self.storage.get_business_facts(owner_id)
        fact_id = facts[0]["id"]

        # Чужой владелец не может удалить факт по id, даже зная его
        deleted_by_stranger = await self.storage.delete_business_fact_by_id(fact_id, stranger_id)
        self.assertFalse(deleted_by_stranger)
        self.assertEqual(len(await self.storage.get_business_facts(owner_id)), 1)

        deleted_by_owner = await self.storage.delete_business_fact_by_id(fact_id, owner_id)
        self.assertTrue(deleted_by_owner)
        self.assertEqual(await self.storage.get_business_facts(owner_id), [])

    async def test_business_facts_limit_enforced(self):
        from storage import MAX_BUSINESS_FACTS_PER_OWNER

        owner_id = 5005
        for i in range(MAX_BUSINESS_FACTS_PER_OWNER):
            ok = await self.storage.save_business_fact(owner_id, f"Факт {i}", f"Значение {i}")
            self.assertTrue(ok)

        over_limit = await self.storage.save_business_fact(owner_id, "Ещё один", "Значение")
        self.assertFalse(over_limit)
        self.assertEqual(len(await self.storage.get_business_facts(owner_id)), MAX_BUSINESS_FACTS_PER_OWNER)

    async def test_business_facts_limit_does_not_block_updates(self):
        from storage import MAX_BUSINESS_FACTS_PER_OWNER

        owner_id = 5006
        for i in range(MAX_BUSINESS_FACTS_PER_OWNER):
            await self.storage.save_business_fact(owner_id, f"Факт {i}", f"Значение {i}")

        # Обновление уже существующего ключа не должно упираться в лимит
        ok = await self.storage.save_business_fact(owner_id, "Факт 0", "Новое значение")
        self.assertTrue(ok)
        facts = {f["fact_key"]: f["fact_value"] for f in await self.storage.get_business_facts(owner_id)}
        self.assertEqual(facts["Факт 0"], "Новое значение")

    async def test_business_facts_isolated_between_owners(self):
        await self.storage.save_business_fact(1111, "Общий ключ", "Значение владельца A")
        await self.storage.save_business_fact(2222, "Общий ключ", "Значение владельца B")

        facts_a = await self.storage.get_business_facts(1111)
        facts_b = await self.storage.get_business_facts(2222)
        self.assertEqual(facts_a[0]["fact_value"], "Значение владельца A")
        self.assertEqual(facts_b[0]["fact_value"], "Значение владельца B")

    # --- Правила по ключевым словам ---

    async def test_save_and_get_keyword_rule(self):
        owner_id = 6001
        ok = await self.storage.save_business_keyword_rule(
            owner_id, "цена", "template", "Актуальные цены на сайте example.com"
        )
        self.assertTrue(ok)
        rules = await self.storage.get_business_keyword_rules(owner_id)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["rule_type"], "template")

    async def test_save_keyword_rule_rejects_invalid_type(self):
        with self.assertRaises(ValueError):
            await self.storage.save_business_keyword_rule(6002, "слово", "не_такой_тип", "текст")

    async def test_keyword_rule_update_changes_type(self):
        owner_id = 6003
        await self.storage.save_business_keyword_rule(owner_id, "жалоба", "hint", "Будь эмпатичен")
        await self.storage.save_business_keyword_rule(owner_id, "жалоба", "template", "Извините за неудобства!")
        rules = await self.storage.get_business_keyword_rules(owner_id)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["rule_type"], "template")
        self.assertEqual(rules[0]["content"], "Извините за неудобства!")

    async def test_delete_keyword_rule_by_id_respects_owner(self):
        owner_id, stranger_id = 6004, 9999
        await self.storage.save_business_keyword_rule(owner_id, "слово", "hint", "текст")
        rules = await self.storage.get_business_keyword_rules(owner_id)
        rule_id = rules[0]["id"]

        self.assertFalse(await self.storage.delete_business_keyword_rule_by_id(rule_id, stranger_id))
        self.assertTrue(await self.storage.delete_business_keyword_rule_by_id(rule_id, owner_id))
        self.assertEqual(await self.storage.get_business_keyword_rules(owner_id), [])

    async def test_keyword_rules_limit_enforced(self):
        from storage import MAX_BUSINESS_KEYWORD_RULES_PER_OWNER

        owner_id = 6005
        for i in range(MAX_BUSINESS_KEYWORD_RULES_PER_OWNER):
            ok = await self.storage.save_business_keyword_rule(owner_id, f"слово{i}", "hint", "текст")
            self.assertTrue(ok)

        over_limit = await self.storage.save_business_keyword_rule(owner_id, "ещё", "hint", "текст")
        self.assertFalse(over_limit)


if __name__ == "__main__":
    unittest.main()
