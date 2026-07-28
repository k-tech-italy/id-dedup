import datetime
from typing import cast

import pytest

from id_dedup.workflow.models import Conversation, ConversationQuerySet, Trigger


@pytest.mark.django_db
class TestConversationQuerySet:
    def test_pending_returns_unended_conversations(self):
        c1 = Conversation.objects.create(trigger=Trigger.UPLOAD)
        Conversation.objects.create(
            trigger=Trigger.UPLOAD,
            ended_at=datetime.datetime(2026, 7, 23, 12, 0, 0, tzinfo=datetime.timezone.utc),
        )
        Conversation.objects.create(trigger=Trigger.UPLOAD, error_message="oops")

        assert list(cast("ConversationQuerySet", Conversation.objects).pending().values_list("pk", flat=True)) == [
            c1.pk,
        ]

    def test_completed_returns_ended_without_error(self):
        Conversation.objects.create(trigger=Trigger.UPLOAD)
        c2 = Conversation.objects.create(
            trigger=Trigger.UPLOAD,
            ended_at=datetime.datetime(2026, 7, 23, 12, 0, 0, tzinfo=datetime.timezone.utc),
        )
        Conversation.objects.create(
            trigger=Trigger.UPLOAD,
            ended_at=datetime.datetime(2026, 7, 23, 12, 0, 0, tzinfo=datetime.timezone.utc),
            error_message="oops",
        )

        assert list(cast("ConversationQuerySet", Conversation.objects).completed().values_list("pk", flat=True)) == [
            c2.pk,
        ]

    def test_errored_returns_conversations_with_error(self):
        Conversation.objects.create(trigger=Trigger.UPLOAD)
        Conversation.objects.create(
            trigger=Trigger.UPLOAD,
            ended_at=datetime.datetime(2026, 7, 23, 12, 0, 0, tzinfo=datetime.timezone.utc),
        )
        c3 = Conversation.objects.create(trigger=Trigger.UPLOAD, error_message="oops")

        assert list(cast("ConversationQuerySet", Conversation.objects).errored().values_list("pk", flat=True)) == [
            c3.pk,
        ]

    def test_errored_excludes_empty_error_message(self):
        Conversation.objects.create(trigger=Trigger.UPLOAD, error_message="")
        c2 = Conversation.objects.create(trigger=Trigger.UPLOAD, error_message="oops")

        assert list(cast("ConversationQuerySet", Conversation.objects).errored().values_list("pk", flat=True)) == [
            c2.pk,
        ]
