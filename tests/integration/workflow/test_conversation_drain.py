from __future__ import annotations

import pytest
from django.utils import timezone
from model_bakery import baker

from id_dedup.workflow.models import Conversation, NothingToResume, Trigger


def _make_conversation(summary: dict | None = None, **kwargs) -> Conversation:
    return baker.make(Conversation, trigger=Trigger.UPLOAD, summary=summary or {}, **kwargs)


@pytest.mark.django_db
class TestConversationResume:
    def test_raises_when_not_errored(self):
        conv = _make_conversation()
        with pytest.raises(NothingToResume):
            conv.resume()

    def test_raises_when_pending_no_error(self):
        conv = _make_conversation(summary={"pending_image_ids": ["a"]})
        with pytest.raises(NothingToResume):
            conv.resume()

    def test_clears_error_and_ended_at(self):
        conv = _make_conversation(summary={"batch_id": "x"}, error_message="boom", ended_at=timezone.now())
        conv.resume()
        conv.refresh_from_db()
        assert conv.error_message == ""
        assert conv.ended_at is None
        assert conv.summary["batch_id"] == "x"

    def test_resume_preserves_existing_summary(self):
        conv = _make_conversation(
            summary={"batch_id": "x", "clustering_done": True},
            error_message="boom",
            ended_at=timezone.now(),
        )
        conv.resume()
        conv.refresh_from_db()
        assert conv.summary["clustering_done"] is True


@pytest.mark.django_db
class TestConversationFail:
    def test_sets_error_message_and_ended_at(self):
        conv = _make_conversation()
        conv.fail("something broke")
        conv.refresh_from_db()
        assert conv.error_message == "something broke"
        assert conv.ended_at is not None

    def test_overwrites_previous_error(self):
        conv = _make_conversation(error_message="first", ended_at=timezone.now())
        conv.fail("second")
        conv.refresh_from_db()
        assert conv.error_message == "second"

    def test_fail_then_resume_round_trip(self):
        conv = _make_conversation(summary={"batch_id": "b1"})
        conv.fail("error during processing")
        conv.resume()
        conv.refresh_from_db()
        assert conv.error_message == ""
        assert conv.ended_at is None


@pytest.mark.django_db
class TestConversationMarkClustered:
    def test_sets_clustering_done(self):
        conv = _make_conversation()
        conv.mark_clustered({"batch_id": "x"})
        conv.refresh_from_db()
        assert conv.summary["clustering_done"] is True

    def test_replaces_summary_dict(self):
        conv = _make_conversation(summary={"old_key": "old_value"})
        conv.mark_clustered({"batch_id": "x", "total_images": 4})
        conv.refresh_from_db()
        assert "old_key" not in conv.summary
        assert conv.summary["batch_id"] == "x"
        assert conv.summary["total_images"] == 4
        assert conv.summary["clustering_done"] is True

    def test_does_not_set_ended_at(self):
        conv = _make_conversation()
        conv.mark_clustered({"batch_id": "x"})
        conv.refresh_from_db()
        assert conv.ended_at is None

    def test_batch_id_preserved_for_resume_lookup(self):
        conv = _make_conversation(summary={"batch_id": "abc-123"})
        conv.mark_clustered({"batch_id": "abc-123", "clustering_done": True})
        conv.refresh_from_db()
        assert conv.summary["batch_id"] == "abc-123"
        assert Conversation.objects.filter(trigger=Trigger.UPLOAD, summary__batch_id="abc-123").exists()
