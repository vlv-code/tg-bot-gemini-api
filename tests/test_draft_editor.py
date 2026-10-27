import time
import unittest
from unittest.mock import patch

from draft_editor import (
    DraftActionType,
    DraftRegistry,
    ParsedReplyAction,
    parse_reply_action,
)


class TestDraftEditorEnumsAndDataclasses(unittest.TestCase):
    def test_draft_action_type_values(self):
        self.assertEqual(DraftActionType.INSTANT_SEND.value, "instant_send")
        self.assertEqual(DraftActionType.REVISE_INSTRUCTION.value, "revise")

    def test_parsed_reply_action_dataclass(self):
        action = ParsedReplyAction(
            action_type=DraftActionType.INSTANT_SEND,
            text="Hello world",
            draft_id="draft_123",
        )
        self.assertEqual(action.action_type, DraftActionType.INSTANT_SEND)
        self.assertEqual(action.text, "Hello world")
        self.assertEqual(action.draft_id, "draft_123")


class TestDraftRegistry(unittest.TestCase):
    def setUp(self):
        self.registry = DraftRegistry()

    def test_register_and_get_card(self):
        self.assertEqual(self.registry.count(), 0)
        self.registry.register_card(card_message_id=1001, draft_id="draft_abc")

        self.assertEqual(self.registry.count(), 1)
        self.assertEqual(self.registry.get_draft_id_by_card(1001), "draft_abc")
        self.assertEqual(self.registry.get_card_by_draft_id("draft_abc"), 1001)

    def test_get_nonexistent(self):
        self.assertIsNone(self.registry.get_draft_id_by_card(9999))
        self.assertIsNone(self.registry.get_card_by_draft_id("nonexistent_draft"))

    def test_remove_draft_cleans_both_directions(self):
        self.registry.register_card(card_message_id=1002, draft_id="draft_xyz")
        self.assertEqual(self.registry.count(), 1)

        self.registry.remove_draft("draft_xyz")

        self.assertEqual(self.registry.count(), 0)
        self.assertIsNone(self.registry.get_draft_id_by_card(1002))
        self.assertIsNone(self.registry.get_card_by_draft_id("draft_xyz"))

    def test_remove_nonexistent_draft_is_safe(self):
        # Should not raise exception
        self.registry.remove_draft("does_not_exist")
        self.assertEqual(self.registry.count(), 0)

    def test_re_registering_updates_mapping(self):
        # Same card_message_id re-registered to a new draft_id
        self.registry.register_card(card_message_id=1003, draft_id="draft_old")
        self.registry.register_card(card_message_id=1003, draft_id="draft_new")

        self.assertEqual(self.registry.get_draft_id_by_card(1003), "draft_new")
        self.assertEqual(self.registry.get_card_by_draft_id("draft_new"), 1003)
        self.assertIsNone(self.registry.get_card_by_draft_id("draft_old"))
        self.assertEqual(self.registry.count(), 1)

    def test_cleanup_expired(self):
        current_time = 1000.0
        with patch("time.time", return_value=current_time):
            self.registry.register_card(card_message_id=1, draft_id="draft_1", ttl_seconds=100.0)
            self.registry.register_card(card_message_id=2, draft_id="draft_2", ttl_seconds=500.0)

        # At time 1050, draft_1 is not expired yet (expires at 1100.0)
        with patch("time.time", return_value=1050.0):
            removed = self.registry.cleanup_expired()
            self.assertEqual(removed, 0)
            self.assertEqual(self.registry.count(), 2)

        # At time 1101, draft_1 has expired, draft_2 (expires at 1500.0) has not
        with patch("time.time", return_value=1101.0):
            removed = self.registry.cleanup_expired()
            self.assertEqual(removed, 1)
            self.assertEqual(self.registry.count(), 1)
            self.assertIsNone(self.registry.get_draft_id_by_card(1))
            self.assertIsNone(self.registry.get_card_by_draft_id("draft_1"))
            self.assertEqual(self.registry.get_draft_id_by_card(2), "draft_2")

        # At time 1501, draft_2 has also expired
        with patch("time.time", return_value=1501.0):
            removed = self.registry.cleanup_expired()
            self.assertEqual(removed, 1)
            self.assertEqual(self.registry.count(), 0)
            self.assertIsNone(self.registry.get_draft_id_by_card(2))
            self.assertIsNone(self.registry.get_card_by_draft_id("draft_2"))


class TestParseReplyAction(unittest.TestCase):
    def test_parse_dot_prefix(self):
        reply = ".Да, доставим в четверг к 14:00"
        action = parse_reply_action(reply, "draft_1")
        self.assertEqual(action.action_type, DraftActionType.INSTANT_SEND)
        self.assertEqual(action.text, "Да, доставим в четверг к 14:00")
        self.assertEqual(action.draft_id, "draft_1")

    def test_parse_dot_prefix_with_spaces(self):
        reply = "   .   Да, доставим в четверг к 14:00   "
        action = parse_reply_action(reply, "draft_1")
        self.assertEqual(action.action_type, DraftActionType.INSTANT_SEND)
        self.assertEqual(action.text, "Да, доставим в четверг к 14:00")
        self.assertEqual(action.draft_id, "draft_1")

    def test_parse_exclamation_prefix(self):
        reply = "!Договорились, спасибо"
        action = parse_reply_action(reply, "draft_2")
        self.assertEqual(action.action_type, DraftActionType.INSTANT_SEND)
        self.assertEqual(action.text, "Договорились, спасибо")
        self.assertEqual(action.draft_id, "draft_2")

    def test_parse_exclamation_prefix_with_spaces(self):
        reply = "   !   Договорились, спасибо   "
        action = parse_reply_action(reply, "draft_2")
        self.assertEqual(action.action_type, DraftActionType.INSTANT_SEND)
        self.assertEqual(action.text, "Договорились, спасибо")
        self.assertEqual(action.draft_id, "draft_2")

    def test_parse_revise_instruction_without_prefix(self):
        reply = "сделай ответ более дружелюбным и спроси ИНН"
        action = parse_reply_action(reply, "draft_3")
        self.assertEqual(action.action_type, DraftActionType.REVISE_INSTRUCTION)
        self.assertEqual(action.text, "сделай ответ более дружелюбным и спроси ИНН")
        self.assertEqual(action.draft_id, "draft_3")

    def test_parse_revise_instruction_with_leading_trailing_spaces(self):
        reply = "   сделай ответ более дружелюбным и спроси ИНН   "
        action = parse_reply_action(reply, "draft_3")
        self.assertEqual(action.action_type, DraftActionType.REVISE_INSTRUCTION)
        self.assertEqual(action.text, "сделай ответ более дружелюбным и спроси ИНН")
        self.assertEqual(action.draft_id, "draft_3")

    def test_edge_case_empty_string(self):
        reply = ""
        action = parse_reply_action(reply, "draft_4")
        self.assertEqual(action.action_type, DraftActionType.REVISE_INSTRUCTION)
        self.assertEqual(action.text, "")
        self.assertEqual(action.draft_id, "draft_4")

    def test_edge_case_whitespace_string(self):
        reply = "    \n\t  "
        action = parse_reply_action(reply, "draft_5")
        self.assertEqual(action.action_type, DraftActionType.REVISE_INSTRUCTION)
        self.assertEqual(action.text, "")
        self.assertEqual(action.draft_id, "draft_5")

    def test_edge_case_only_prefix_dot(self):
        reply = "."
        action = parse_reply_action(reply, "draft_6")
        self.assertEqual(action.action_type, DraftActionType.INSTANT_SEND)
        self.assertEqual(action.text, "")
        self.assertEqual(action.draft_id, "draft_6")

    def test_edge_case_only_prefix_exclamation(self):
        reply = "!"
        action = parse_reply_action(reply, "draft_7")
        self.assertEqual(action.action_type, DraftActionType.INSTANT_SEND)
        self.assertEqual(action.text, "")
        self.assertEqual(action.draft_id, "draft_7")


if __name__ == "__main__":
    unittest.main()
