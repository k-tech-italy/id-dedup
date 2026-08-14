from __future__ import annotations

import pytest
from django.utils import timezone

from id_dedup.workflow.models import Conversation, NothingToResume, Trigger


@pytest.fixture
def conversation(conversation_factory):
    return conversation_factory()


@pytest.fixture
def pending_conversation(conversation_factory):
    return conversation_factory(summary={"pending_image_ids": ["a"]})


@pytest.fixture
def errored_conversation(conversation_factory):
    return conversation_factory(summary={"batch_id": "x"}, error_message="boom", ended_at=timezone.now())


@pytest.fixture
def errored_clustered_conversation(conversation_factory):
    return conversation_factory(
        summary={"batch_id": "x", "clustering_done": True},
        error_message="boom",
        ended_at=timezone.now(),
    )


@pytest.fixture
def prior_error_conversation(conversation_factory):
    return conversation_factory(error_message="first", ended_at=timezone.now())


@pytest.fixture
def batched_conversation(conversation_factory):
    return conversation_factory(summary={"batch_id": "abc-123"})


@pytest.fixture
def old_summary_conversation(conversation_factory):
    return conversation_factory(summary={"old_key": "old_value"})


@pytest.mark.django_db
class TestConversationResume:
    def test_raises_when_not_errored(self, conversation):
        with pytest.raises(NothingToResume):
            conversation.resume()

    def test_raises_when_pending_no_error(self, pending_conversation):
        with pytest.raises(NothingToResume):
            pending_conversation.resume()

    def test_clears_error_and_ended_at(self, errored_conversation):
        errored_conversation.resume()
        errored_conversation.refresh_from_db()
        assert errored_conversation.error_message == ""
        assert errored_conversation.ended_at is None
        assert errored_conversation.summary["batch_id"] == "x"

    def test_resume_preserves_existing_summary(self, errored_clustered_conversation):
        errored_clustered_conversation.resume()
        errored_clustered_conversation.refresh_from_db()
        assert errored_clustered_conversation.summary["clustering_done"]


@pytest.mark.django_db
class TestConversationFail:
    def test_sets_error_message_and_ended_at(self, conversation):
        conversation.fail("something broke")
        conversation.refresh_from_db()
        assert conversation.error_message == "something broke"
        assert conversation.ended_at is not None

    def test_overwrites_previous_error(self, prior_error_conversation):
        prior_error_conversation.fail("second")
        prior_error_conversation.refresh_from_db()
        assert prior_error_conversation.error_message == "second"

    def test_fail_then_resume_round_trip(self, batched_conversation):
        batched_conversation.fail("error during processing")
        batched_conversation.resume()
        batched_conversation.refresh_from_db()
        assert batched_conversation.error_message == ""
        assert batched_conversation.ended_at is None


@pytest.mark.django_db
class TestConversationMarkClustered:
    def test_sets_clustering_done(self, conversation):
        conversation.mark_clustered({"batch_id": "x"})
        conversation.refresh_from_db()
        assert conversation.summary["clustering_done"]

    def test_replaces_summary_dict(self, old_summary_conversation):
        old_summary_conversation.mark_clustered({"batch_id": "x", "total_images": 4})
        old_summary_conversation.refresh_from_db()
        assert "old_key" not in old_summary_conversation.summary
        assert old_summary_conversation.summary["batch_id"] == "x"
        assert old_summary_conversation.summary["total_images"] == 4
        assert old_summary_conversation.summary["clustering_done"]

    def test_does_not_set_ended_at(self, conversation):
        conversation.mark_clustered({"batch_id": "x"})
        conversation.refresh_from_db()
        assert conversation.ended_at is None

    def test_batch_id_preserved_for_resume_lookup(self, batched_conversation):
        batched_conversation.mark_clustered({"batch_id": "abc-123", "clustering_done": True})
        batched_conversation.refresh_from_db()
        assert batched_conversation.summary["batch_id"] == "abc-123"
        assert Conversation.objects.filter(trigger=Trigger.UPLOAD, summary__batch_id="abc-123").exists()
