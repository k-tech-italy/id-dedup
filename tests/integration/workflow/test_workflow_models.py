from typing import cast

import pytest
from django.utils import timezone
from model_bakery.recipe import Recipe

from id_dedup.workflow import models as workflow_models
from id_dedup.workflow.models import Conversation, ConversationQuerySet


@pytest.fixture
def open_conversation():
    return Recipe(workflow_models.Conversation, trigger=workflow_models.Trigger.UPLOAD).make()


@pytest.fixture
def completed_conversation():
    return Recipe(
        workflow_models.Conversation,
        trigger=workflow_models.Trigger.UPLOAD,
        ended_at=timezone.now,
    ).make()


@pytest.fixture
def errored_conversation():
    return Recipe(
        workflow_models.Conversation,
        trigger=workflow_models.Trigger.UPLOAD,
        error_message="oops",
    ).make()


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
        Recipe(workflow_models.Conversation, trigger=workflow_models.Trigger.UPLOAD).make(error_message="")

        assert list(cast("ConversationQuerySet", Conversation.objects).errored().values_list("pk", flat=True)) == [
            errored_conversation.pk,
        ]
