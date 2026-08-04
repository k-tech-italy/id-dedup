from typing import cast

import pytest
from model_bakery import baker

from id_dedup.workflow.models import Conversation, ConversationQuerySet


@pytest.mark.django_db
class TestConversationQuerySet:
    def test_pending_returns_unended_conversations(
        self,
        open_conversation,
        completed_conversation,
        errored_conversation,
    ):
        assert list(cast("ConversationQuerySet", Conversation.objects).pending().values_list("pk", flat=True)) == [
            open_conversation.pk,
        ]

    def test_completed_returns_ended_without_error(
        self,
        open_conversation,
        completed_conversation,
        errored_conversation,
    ):
        assert list(cast("ConversationQuerySet", Conversation.objects).completed().values_list("pk", flat=True)) == [
            completed_conversation.pk,
        ]

    def test_errored_returns_conversations_with_error(
        self,
        open_conversation,
        completed_conversation,
        errored_conversation,
    ):
        assert list(cast("ConversationQuerySet", Conversation.objects).errored().values_list("pk", flat=True)) == [
            errored_conversation.pk,
        ]

    def test_errored_excludes_empty_error_message(self, errored_conversation):
        baker.make_recipe("tests.errored_conversation", error_message="")

        assert list(cast("ConversationQuerySet", Conversation.objects).errored().values_list("pk", flat=True)) == [
            errored_conversation.pk,
        ]
