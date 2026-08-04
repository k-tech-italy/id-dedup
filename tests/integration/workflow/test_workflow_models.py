import datetime
from typing import cast

import pytest
from django.conf import settings
from django.core.files import File
from django.db import IntegrityError

from id_dedup.workflow.models import (
    Conversation,
    ConversationQuerySet,
    Identity,
    Image,
    OutboxMessage,
    Trigger,
)


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


@pytest.mark.django_db
class TestOutboxMessage:
    def test_defaults(self):
        msg = OutboxMessage.objects.create(task_name="id_dedup.workflow.tasks.process_batch", payload={"batch_id": "x"})

        assert msg.dispatched_at is None
        assert msg.attempts == 0
        assert msg.last_error == ""
        assert msg.dead_lettered_at is None
        assert msg.max_attempts == getattr(settings, "OUTBOX_MAX_ATTEMPTS", 5)
        assert msg.created_at is not None
        assert msg.payload == {"batch_id": "x"}

    def test_max_attempts_reads_settings_at_creation(self, monkeypatch):
        monkeypatch.setattr(settings, "OUTBOX_MAX_ATTEMPTS", 9)
        assert OutboxMessage.objects.create().max_attempts == 9

    def test_max_attempts_falls_back_to_five(self):
        assert OutboxMessage.objects.create().max_attempts == 5

    def test_zero_max_attempts_rejected(self):
        with pytest.raises(IntegrityError):
            OutboxMessage.objects.create(max_attempts=0)

    def test_pending_index_present(self):
        assert "outbox_pending_idx" in [idx.name for idx in OutboxMessage._meta.indexes]

    def test_check_constraint_present(self):
        names = {c.name for c in OutboxMessage._meta.constraints}
        assert "outbox_max_attempts_positive" in names


@pytest.mark.django_db
class TestIndexNames:
    def test_image_hnsw_index_renamed(self):
        assert [idx.name for idx in Image._meta.indexes] == ["workflow_embedding_idx"]

    def test_identity_hnsw_index_renamed(self):
        assert [idx.name for idx in Identity._meta.indexes] == ["workflow_identity_centroid_idx"]


@pytest.mark.django_db
class TestImageSourceImageUnique:
    def _create_image(self, tmp_path, name: str) -> Image:
        p = tmp_path / name
        p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16)
        with p.open("rb") as f:
            return Image.objects.create(source_image=File(f, name=name))

    def test_distinct_names_allowed(self, tmp_path):
        self._create_image(tmp_path, "a.jpg")
        self._create_image(tmp_path, "b.jpg")
        assert Image.objects.count() == 2

    def test_duplicate_source_image_rejected(self):
        # Plain path strings bypass storage uniquification, pinning the DB constraint.
        Image.objects.create(source_image="images/same.jpg")
        with pytest.raises(IntegrityError):
            Image.objects.create(source_image="images/same.jpg")

    def test_unique_constraint_declared(self):
        assert "image_source_image_unique" in [c.name for c in Image._meta.constraints]
