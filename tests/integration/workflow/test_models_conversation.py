import pytest

pytestmark = pytest.mark.django_db


@pytest.fixture
def conversation(conversation_factory):
    return conversation_factory()


@pytest.fixture
def empty_pending_conversation(conversation_factory):
    return conversation_factory(summary={"pending_image_ids": []})


@pytest.fixture
def single_pending_conversation(conversation_factory):
    return conversation_factory(summary={"pending_image_ids": ["a"]})


@pytest.fixture
def pair_pending_conversation(conversation_factory):
    return conversation_factory(summary={"pending_image_ids": ["a", "b"]})


class TestConversationIsDrained:
    def test_empty_pending_set(self, empty_pending_conversation):
        assert empty_pending_conversation.is_drained() is True

    def test_non_empty_pending_set(self, single_pending_conversation):
        assert single_pending_conversation.is_drained() is False

    def test_missing_key_is_drained(self, conversation):
        assert conversation.is_drained() is True


class TestConversationClose:
    def test_close_returns_true(self, conversation):
        assert conversation.close() is True
        conversation.refresh_from_db()
        assert conversation.ended_at is not None

    def test_close_returns_false_when_already_closed(self, conversation):
        conversation.close()
        assert conversation.close() is False

    def test_close_refreshes_instance(self, conversation):
        assert conversation.ended_at is None
        conversation.close()
        assert conversation.ended_at is not None


class TestConversationDrainImages:
    def test_removes_ids(self, pair_pending_conversation):
        pair_pending_conversation.drain_images(["a"])
        pair_pending_conversation.refresh_from_db()
        assert pair_pending_conversation.summary["pending_image_ids"] == ["b"]

    def test_idempotent_for_unknown_ids(self, single_pending_conversation):
        single_pending_conversation.drain_images(["zzz"])
        single_pending_conversation.refresh_from_db()
        assert single_pending_conversation.summary["pending_image_ids"] == ["a"]

    def test_does_not_close(self, empty_pending_conversation):
        empty_pending_conversation.drain_images([])
        assert empty_pending_conversation.ended_at is None
