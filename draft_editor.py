from dataclasses import dataclass
from enum import Enum
import time
from typing import Dict, Optional


class DraftActionType(Enum):
    INSTANT_SEND = "instant_send"       # starts with . or ! (send directly to counterparty)
    REVISE_INSTRUCTION = "revise"       # normal text prompt to adjust current draft with Gemini


@dataclass
class ParsedReplyAction:
    action_type: DraftActionType
    text: str
    draft_id: str


class DraftRegistry:
    """
    In-memory registry maintaining bidirectional mapping between
    card_message_id and draft_id with TTL support.
    """

    def __init__(self) -> None:
        self._card_to_draft: Dict[int, str] = {}
        self._draft_to_card: Dict[str, int] = {}
        self._draft_expiry: Dict[str, float] = {}

    def register_card(
        self, card_message_id: int, draft_id: str, ttl_seconds: float = 3600.0
    ) -> None:
        """
        Registers a card_message_id <-> draft_id bidirectional mapping with TTL.
        """
        # Clean up any previous mappings for the given card_message_id
        old_draft_id = self._card_to_draft.get(card_message_id)
        if old_draft_id is not None and old_draft_id != draft_id:
            self.remove_draft(old_draft_id)

        # Clean up any previous mappings for the given draft_id
        old_card_id = self._draft_to_card.get(draft_id)
        if old_card_id is not None and old_card_id != card_message_id:
            self._card_to_draft.pop(old_card_id, None)

        self._card_to_draft[card_message_id] = draft_id
        self._draft_to_card[draft_id] = card_message_id
        self._draft_expiry[draft_id] = time.time() + ttl_seconds

    def get_draft_id_by_card(self, card_message_id: int) -> Optional[str]:
        """Returns draft_id associated with card_message_id, or None."""
        return self._card_to_draft.get(card_message_id)

    def get_card_by_draft_id(self, draft_id: str) -> Optional[int]:
        """Returns card_message_id associated with draft_id, or None."""
        return self._draft_to_card.get(draft_id)

    def remove_draft(self, draft_id: str) -> None:
        """Removes the draft and associated card_message_id from registry."""
        card_id = self._draft_to_card.pop(draft_id, None)
        if card_id is not None:
            self._card_to_draft.pop(card_id, None)
        self._draft_expiry.pop(draft_id, None)

    def cleanup_expired(self) -> int:
        """
        Cleans up entries older than ttl_seconds.
        Returns the number of removed drafts.
        """
        now = time.time()
        expired_drafts = [
            draft_id
            for draft_id, expiry in self._draft_expiry.items()
            if now >= expiry
        ]
        for draft_id in expired_drafts:
            self.remove_draft(draft_id)
        return len(expired_drafts)

    def count(self) -> int:
        """Returns total active draft mappings in registry."""
        return len(self._card_to_draft)


def parse_reply_action(reply_text: str, draft_id: str) -> ParsedReplyAction:
    """
    Parses user reply text when replying to a draft card.

    If reply_text starts with '.' or '!':
      action_type is INSTANT_SEND, text has prefix stripped.
    Else:
      action_type is REVISE_INSTRUCTION, text is stripped of outer whitespace.
    """
    stripped = reply_text.strip()
    if stripped.startswith(".") or stripped.startswith("!"):
        action_type = DraftActionType.INSTANT_SEND
        text = stripped[1:].strip()
    else:
        action_type = DraftActionType.REVISE_INSTRUCTION
        text = stripped

    return ParsedReplyAction(action_type=action_type, text=text, draft_id=draft_id)
