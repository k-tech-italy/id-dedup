import pytest
from model_bakery import baker

from id_dedup.workflow.models import Conversation, Trigger


def _make_conversation(summary: dict | None = None, **kwargs) -> Conversation:
    return baker.make(Conversation, trigger=Trigger.UPLOAD, summary=summary or {}, **kwargs)


@pytest.mark.django_db
class TestConversationIsDrained:
    def test_empty_pending_set(self):
        conv = _make_conversation(summary={"pending_image_ids": []})
        assert conv.is_drained() is True

    def test_non_empty_pending_set(self):
        conv = _make_conversation(summary={"pending_image_ids": ["a"]})
        assert conv.is_drained() is False

    def test_missing_key_is_drained(self):
        conv = _make_conversation()
        assert conv.is_drained() is True


@pytest.mark.django_db
class TestConversationClose:
    def test_close_returns_true(self):
        conv = _make_conversation()
        assert conv.close() is True
        conv.refresh_from_db()
        assert conv.ended_at is not None

    def test_close_returns_false_when_already_closed(self):
        conv = _make_conversation()
        conv.close()
        assert conv.close() is False

    def test_close_refreshes_instance(self):
        conv = _make_conversation()
        assert conv.ended_at is None
        conv.close()
        assert conv.ended_at is not None


@pytest.mark.django_db
class TestConversationDrainImages:
    def test_removes_ids(self):
        conv = _make_conversation(summary={"pending_image_ids": ["a", "b"]})
        conv.drain_images(["a"])
        conv.refresh_from_db()
        assert conv.summary["pending_image_ids"] == ["b"]

    def test_idempotent_for_unknown_ids(self):
        conv = _make_conversation(summary={"pending_image_ids": ["a"]})
        conv.drain_images(["zzz"])
        conv.refresh_from_db()
        assert conv.summary["pending_image_ids"] == ["a"]

    def test_does_not_close(self):
        conv = _make_conversation(summary={"pending_image_ids": []})
        conv.drain_images([])
        assert conv.ended_at is None
