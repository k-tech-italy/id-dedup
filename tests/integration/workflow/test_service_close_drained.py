import pytest

from id_dedup.workflow.models import Conversation, Trigger
from id_dedup.workflow.service import close_conversation_if_drained


@pytest.mark.django_db
class TestCloseIfDrained:
    def test_no_drained_ids_closes_when_empty(self):
        conv = Conversation.objects.create(trigger=Trigger.UPLOAD, summary={"pending_image_ids": []})
        assert close_conversation_if_drained(conv) is True
        conv.refresh_from_db()
        assert conv.ended_at is not None

    def test_no_drained_ids_stays_open_when_non_empty(self):
        conv = Conversation.objects.create(trigger=Trigger.UPLOAD, summary={"pending_image_ids": ["a"]})
        assert close_conversation_if_drained(conv) is False
        assert conv.ended_at is None

    def test_drained_ids_remove_and_close(self):
        conv = Conversation.objects.create(trigger=Trigger.UPLOAD, summary={"pending_image_ids": ["a"]})
        assert close_conversation_if_drained(conv, drained_ids=["a"]) is True
        conv.refresh_from_db()
        assert conv.ended_at is not None
        assert conv.summary["pending_image_ids"] == []

    def test_drained_ids_partial(self):
        conv = Conversation.objects.create(trigger=Trigger.UPLOAD, summary={"pending_image_ids": ["a", "b"]})
        assert close_conversation_if_drained(conv, drained_ids=["a"]) is False
        conv.refresh_from_db()
        assert conv.summary["pending_image_ids"] == ["b"]

    def test_second_call_after_close_returns_false(self):
        conv = Conversation.objects.create(trigger=Trigger.UPLOAD, summary={"pending_image_ids": []})
        assert close_conversation_if_drained(conv) is True
        assert close_conversation_if_drained(conv) is False
