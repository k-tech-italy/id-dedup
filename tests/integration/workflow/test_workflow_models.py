import datetime
from typing import cast

import pytest
from django.conf import settings
from django.core.files import File
from django.db import IntegrityError
from django.utils import timezone
from model_bakery import baker

from id_dedup.workflow.models import (
    Batch,
    ClusterReviewTicket,
    Conversation,
    ConversationQuerySet,
    Identity,
    Image,
    OutboxMessage,
    Trigger,
)


@pytest.fixture
def open_conversation():
    return baker.make(Conversation, trigger=Trigger.UPLOAD, summary={})


@pytest.fixture
def completed_conversation():
    return baker.make(Conversation, trigger=Trigger.UPLOAD, summary={}, ended_at=timezone.now())


@pytest.fixture
def errored_conversation():
    return baker.make(Conversation, trigger=Trigger.UPLOAD, summary={}, error_message="oops")


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
        baker.make(Conversation, trigger=Trigger.UPLOAD, error_message="")

        assert list(cast("ConversationQuerySet", Conversation.objects).errored().values_list("pk", flat=True)) == [
            errored_conversation.pk,
        ]


@pytest.mark.django_db
class TestOutboxMessage:
    def test_defaults(self):
        msg = baker.make(OutboxMessage, task_name="id_dedup.workflow.tasks.process_batch", payload={"batch_id": "x"})

        assert msg.dispatched_at is None
        assert msg.attempts == 0
        assert msg.last_error == ""
        assert msg.dead_lettered_at is None
        assert msg.max_attempts == getattr(settings, "OUTBOX_MAX_ATTEMPTS", 5)
        assert msg.created_at is not None
        assert msg.payload == {"batch_id": "x"}

    def test_max_attempts_reads_settings_at_creation(self, monkeypatch):
        monkeypatch.setattr(settings, "OUTBOX_MAX_ATTEMPTS", 9, raising=False)
        assert baker.make(OutboxMessage).max_attempts == 9

    def test_max_attempts_falls_back_to_five(self):
        assert baker.make(OutboxMessage).max_attempts == 5

    def test_zero_max_attempts_rejected(self):
        with pytest.raises(IntegrityError):
            baker.make(OutboxMessage, max_attempts=0)

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
class TestClusterReviewTicketNew:
    def test_new_persists_ticket(self, batch):
        ticket = ClusterReviewTicket.new(batch, 3)

        assert ticket.pk is not None
        assert ticket.batch == batch
        assert ClusterReviewTicket.objects.get(pk=ticket.pk).cluster_label == 3

    def test_new_accepts_positional_args(self, batch):
        ticket = ClusterReviewTicket.new(batch, 7)

        assert ClusterReviewTicket.objects.get(pk=ticket.pk).cluster_label == 7


@pytest.mark.django_db
class TestBatchRecordSkippedFiles:
    def test_persists_skipped_files(self, batch):
        batch.record_skipped_files(["bad.txt", "doc.pdf"])

        stored = Batch.objects.get(pk=batch.pk)
        assert stored.skipped_files == ["bad.txt", "doc.pdf"]

    def test_empty_skipped_clears(self, batch):
        batch.record_skipped_files(["bad.txt"])

        batch.record_skipped_files([])

        assert Batch.objects.get(pk=batch.pk).skipped_files == []


@pytest.mark.django_db
class TestImageStoreEmbedding:
    def test_persists_embedding_only(self, batch):
        img = baker.make(Image, batch=batch, source_image="images/a.jpg")
        embedding = [0.1] * 512

        img.store_embedding(embedding)

        stored = Image.objects.get(pk=img.pk)
        assert list(stored.embedding) == embedding
        assert stored.cluster_ticket is None

    def test_save_false_leaves_unpersisted(self, batch):
        img = baker.make(Image, batch=batch, source_image="images/a.jpg")
        embedding = [0.1] * 512

        img.store_embedding(embedding, save=False)

        assert list(img.embedding) == embedding
        assert img.updated_at is not None
        stored = Image.objects.get(pk=img.pk)
        assert stored.embedding is None


@pytest.mark.django_db
class TestImageLinkToTicket:
    def test_persists_ticket_only(self, batch, cluster_review_ticket):
        img = baker.make(Image, batch=batch, source_image="images/a.jpg")
        embedding = [0.1] * 512
        img.store_embedding(embedding)

        img.link_to_ticket(cluster_review_ticket)

        stored = Image.objects.get(pk=img.pk)
        assert stored.cluster_ticket == cluster_review_ticket
        assert list(stored.embedding) == embedding

    def test_does_not_touch_existing_embedding(self, batch, cluster_review_ticket):
        img = baker.make(Image, batch=batch, source_image="images/a.jpg")

        img.link_to_ticket(cluster_review_ticket)

        stored = Image.objects.get(pk=img.pk)
        assert stored.cluster_ticket == cluster_review_ticket
        assert stored.embedding is None

    def test_save_false_leaves_unpersisted(self, batch, cluster_review_ticket):
        img = baker.make(Image, batch=batch, source_image="images/a.jpg")

        img.link_to_ticket(cluster_review_ticket, save=False)

        assert img.cluster_ticket == cluster_review_ticket
        assert img.updated_at is not None
        stored = Image.objects.get(pk=img.pk)
        assert stored.cluster_ticket is None


@pytest.mark.django_db
class TestImageBulkStoreEmbeddings:
    def test_persists_embeddings_in_one_write(self, batch):
        imgs = [baker.make(Image, batch=batch, source_image=f"images/{i}.jpg") for i in range(3)]
        for i, img in enumerate(imgs):
            img.embedding = [0.1 * i] * 512

        Image.bulk_store_embeddings(imgs)

        for i, img in enumerate(imgs):
            stored = Image.objects.get(pk=img.pk)
            assert list(stored.embedding) == [0.1 * i] * 512

    def test_bumps_updated_at(self, batch):
        img = baker.make(Image, batch=batch, source_image="images/a.jpg")
        Image.objects.filter(pk=img.pk).update(updated_at=timezone.now() - datetime.timedelta(days=1))
        stale = Image.objects.get(pk=img.pk).updated_at
        img.embedding = [0.1] * 512

        Image.bulk_store_embeddings([img])

        assert Image.objects.get(pk=img.pk).updated_at > stale


@pytest.mark.django_db
class TestImageBulkLinkToTicket:
    def test_links_images_without_touching_embedding(self, batch, cluster_review_ticket):
        imgs = [baker.make(Image, batch=batch, source_image=f"images/{i}.jpg") for i in range(3)]
        for img in imgs:
            img.store_embedding([0.1] * 512)

        Image.bulk_link_to_ticket(cluster_review_ticket, imgs)

        for img in imgs:
            stored = Image.objects.get(pk=img.pk)
            assert stored.cluster_ticket == cluster_review_ticket
            assert list(stored.embedding) == [0.1] * 512

    def test_bumps_updated_at(self, batch, cluster_review_ticket):
        img = baker.make(Image, batch=batch, source_image="images/a.jpg")
        Image.objects.filter(pk=img.pk).update(updated_at=timezone.now() - datetime.timedelta(days=1))
        stale = Image.objects.get(pk=img.pk).updated_at

        Image.bulk_link_to_ticket(cluster_review_ticket, [img])

        assert Image.objects.get(pk=img.pk).updated_at > stale


@pytest.mark.django_db
class TestImageSourceImageUnique:
    def _create_image(self, tmp_path, name: str) -> Image:
        p = tmp_path / name
        p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16)
        with p.open("rb") as f:
            return baker.make(Image, source_image=File(f, name=name))

    def test_distinct_names_allowed(self, tmp_path):
        self._create_image(tmp_path, "a.jpg")
        self._create_image(tmp_path, "b.jpg")
        assert Image.objects.count() == 2

    def test_duplicate_source_image_rejected(self):
        # Plain path strings bypass storage uniquification, pinning the DB constraint.
        baker.make(Image, source_image="images/same.jpg")
        with pytest.raises(IntegrityError):
            baker.make(Image, source_image="images/same.jpg")

    def test_unique_constraint_declared(self):
        assert "image_source_image_unique" in [c.name for c in Image._meta.constraints]
