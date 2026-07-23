import datetime

import pytest


@pytest.mark.django_db
class TestConversationQuerySet:
    def test_pending_returns_unended_conversations(self):
        from id_dedup.workflow.models import Batch, Conversation

        batch = Batch.objects.create()
        c1 = Conversation.objects.create(batch=batch, trigger=Conversation.Trigger.UPLOAD)
        Conversation.objects.create(
            batch=batch, trigger=Conversation.Trigger.UPLOAD,
            ended_at=datetime.datetime(2026, 7, 23, 12, 0, 0, tzinfo=datetime.timezone.utc),
        )
        Conversation.objects.create(batch=batch, trigger=Conversation.Trigger.UPLOAD, error_message="oops")

        assert list(Conversation.objects.pending().values_list("pk", flat=True)) == [c1.pk]

    def test_completed_returns_ended_without_error(self):
        from id_dedup.workflow.models import Batch, Conversation

        batch = Batch.objects.create()
        Conversation.objects.create(batch=batch, trigger=Conversation.Trigger.UPLOAD)
        c2 = Conversation.objects.create(
            batch=batch, trigger=Conversation.Trigger.UPLOAD,
            ended_at=datetime.datetime(2026, 7, 23, 12, 0, 0, tzinfo=datetime.timezone.utc),
        )
        Conversation.objects.create(
            batch=batch, trigger=Conversation.Trigger.UPLOAD,
            ended_at=datetime.datetime(2026, 7, 23, 12, 0, 0, tzinfo=datetime.timezone.utc),
            error_message="oops",
        )

        assert list(Conversation.objects.completed().values_list("pk", flat=True)) == [c2.pk]

    def test_errored_returns_conversations_with_error(self):
        from id_dedup.workflow.models import Batch, Conversation

        batch = Batch.objects.create()
        Conversation.objects.create(batch=batch, trigger=Conversation.Trigger.UPLOAD)
        Conversation.objects.create(
            batch=batch, trigger=Conversation.Trigger.UPLOAD,
            ended_at=datetime.datetime(2026, 7, 23, 12, 0, 0, tzinfo=datetime.timezone.utc),
        )
        c3 = Conversation.objects.create(batch=batch, trigger=Conversation.Trigger.UPLOAD, error_message="oops")

        assert list(Conversation.objects.errored().values_list("pk", flat=True)) == [c3.pk]

    def test_errored_excludes_empty_error_message(self):
        from id_dedup.workflow.models import Batch, Conversation

        batch = Batch.objects.create()
        Conversation.objects.create(batch=batch, trigger=Conversation.Trigger.UPLOAD, error_message="")
        c2 = Conversation.objects.create(batch=batch, trigger=Conversation.Trigger.UPLOAD, error_message="oops")

        assert list(Conversation.objects.errored().values_list("pk", flat=True)) == [c2.pk]
