import pytest

from id_dedup.workflow.service import close_conversation_if_drained

pytestmark = pytest.mark.django_db


@pytest.fixture
def conversation_with_pending(conversation_factory):
    def _make(*pending):
        return conversation_factory(summary={"pending_image_ids": list(pending)})

    return _make


class TestCloseIfDrained:
    def test_no_drained_ids_closes_when_empty(self, conversation_with_pending):
        conv = conversation_with_pending()
        assert close_conversation_if_drained(conv)
        conv.refresh_from_db()
        assert conv.ended_at is not None

    def test_no_drained_ids_stays_open_when_non_empty(self, conversation_with_pending):
        conv = conversation_with_pending("a")
        assert not close_conversation_if_drained(conv)
        assert conv.ended_at is None

    def test_drained_ids_remove_and_close(self, conversation_with_pending):
        conv = conversation_with_pending("a")
        assert close_conversation_if_drained(conv, drained_ids=["a"])
        conv.refresh_from_db()
        assert conv.ended_at is not None
        assert conv.summary["pending_image_ids"] == []

    def test_drained_ids_partial(self, conversation_with_pending):
        conv = conversation_with_pending("a", "b")
        assert not close_conversation_if_drained(conv, drained_ids=["a"])
        conv.refresh_from_db()
        assert conv.summary["pending_image_ids"] == ["b"]

    def test_second_call_after_close_returns_false(self, conversation_with_pending):
        conv = conversation_with_pending()
        assert close_conversation_if_drained(conv)
        assert not close_conversation_if_drained(conv)
