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
def open_conversation(conversation_factory):
    return conversation_factory(trigger=Trigger.UPLOAD, summary={})


@pytest.fixture
def completed_conversation(conversation_factory):
    return conversation_factory(trigger=Trigger.UPLOAD, summary={}, ended_at=timezone.now())


@pytest.fixture
def errored_conversation(conversation_factory):
    return conversation_factory(trigger=Trigger.UPLOAD, summary={}, error_message="oops")


@pytest.fixture
def errorless_conversation(conversation_factory):
    return conversation_factory(trigger=Trigger.UPLOAD, summary={})


@pytest.fixture
def outbox_message_with_payload(outbox_message_factory):
    return outbox_message_factory(payload={"batch_id": "x"})


@pytest.fixture
def image(image_factory):
    return image_factory(source_image="images/a.jpg")


@pytest.fixture
def images(image_factory):
    return [image_factory(source_image=f"images/{i}.jpg") for i in range(3)]


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

    def test_errored_excludes_empty_error_message(self, errored_conversation, errorless_conversation):
        assert list(cast("ConversationQuerySet", Conversation.objects).errored().values_list("pk", flat=True)) == [
            errored_conversation.pk,
        ]


@pytest.mark.django_db
class TestOutboxMessage:
    def test_defaults(self, outbox_message_with_payload):
        msg = outbox_message_with_payload

        assert msg.dispatched_at is None
        assert msg.attempts == 0
        assert msg.last_error == ""
        assert msg.dead_lettered_at is None
        assert msg.max_attempts == getattr(settings, "OUTBOX_MAX_ATTEMPTS", 5)
        assert msg.created_at is not None
        assert msg.payload == {"batch_id": "x"}

    def test_max_attempts_reads_settings_at_creation(self, monkeypatch):
        monkeypatch.setattr(settings, "OUTBOX_MAX_ATTEMPTS", 9, raising=False)
        # Inline, not a fixture: max_attempts is resolved at insert time, so the
        # row must be created after the setting is patched above.
        assert baker.make(OutboxMessage).max_attempts == 9

    def test_max_attempts_falls_back_to_five(self):
        # Inline alongside the settings test: both exercise the insert-time default.
        assert baker.make(OutboxMessage).max_attempts == 5

    def test_zero_max_attempts_rejected(self):
        # Inline, not a fixture: the IntegrityError fires at insert, outside the
        # fixture setup phase pytest.raises could wrap.
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
    def test_persists_embedding_only(self, image):
        embedding = [0.1] * 512

        image.store_embedding(embedding)

        stored = Image.objects.get(pk=image.pk)
        assert list(stored.embedding) == embedding
        assert stored.cluster_ticket is None

    def test_save_false_leaves_unpersisted(self, image):
        embedding = [0.1] * 512

        image.store_embedding(embedding, save=False)

        assert list(image.embedding) == embedding
        assert image.updated_at is not None
        stored = Image.objects.get(pk=image.pk)
        assert stored.embedding is None


@pytest.mark.django_db
class TestImageLinkToTicket:
    def test_persists_ticket_only(self, image, cluster_review_ticket):
        embedding = [0.1] * 512
        image.store_embedding(embedding)

        image.link_to_ticket(cluster_review_ticket)

        stored = Image.objects.get(pk=image.pk)
        assert stored.cluster_ticket == cluster_review_ticket
        assert list(stored.embedding) == embedding

    def test_does_not_touch_existing_embedding(self, image, cluster_review_ticket):
        image.link_to_ticket(cluster_review_ticket)

        stored = Image.objects.get(pk=image.pk)
        assert stored.cluster_ticket == cluster_review_ticket
        assert stored.embedding is None

    def test_save_false_leaves_unpersisted(self, image, cluster_review_ticket):
        image.link_to_ticket(cluster_review_ticket, save=False)

        assert image.cluster_ticket == cluster_review_ticket
        assert image.updated_at is not None
        stored = Image.objects.get(pk=image.pk)
        assert stored.cluster_ticket is None


@pytest.mark.django_db
class TestImageBulkStoreEmbeddings:
    def test_persists_embeddings_in_one_write(self, images):
        for i, img in enumerate(images):
            img.embedding = [0.1 * i] * 512

        Image.bulk_store_embeddings(images)

        for i, img in enumerate(images):
            stored = Image.objects.get(pk=img.pk)
            assert list(stored.embedding) == [0.1 * i] * 512

    def test_bumps_updated_at(self, image):
        Image.objects.filter(pk=image.pk).update(updated_at=timezone.now() - datetime.timedelta(days=1))
        stale = Image.objects.get(pk=image.pk).updated_at
        image.embedding = [0.1] * 512

        Image.bulk_store_embeddings([image])

        assert Image.objects.get(pk=image.pk).updated_at > stale


@pytest.mark.django_db
class TestImageBulkLinkToTicket:
    def test_links_images_without_touching_embedding(self, images, cluster_review_ticket):
        for img in images:
            img.store_embedding([0.1] * 512)

        Image.bulk_link_to_ticket(cluster_review_ticket, images)

        for img in images:
            stored = Image.objects.get(pk=img.pk)
            assert stored.cluster_ticket == cluster_review_ticket
            assert list(stored.embedding) == [0.1] * 512

    def test_bumps_updated_at(self, image, cluster_review_ticket):
        Image.objects.filter(pk=image.pk).update(updated_at=timezone.now() - datetime.timedelta(days=1))
        stale = Image.objects.get(pk=image.pk).updated_at

        Image.bulk_link_to_ticket(cluster_review_ticket, [image])

        assert Image.objects.get(pk=image.pk).updated_at > stale


@pytest.mark.django_db
class TestImageSourceImageUnique:
    def _create_image(self, tmp_path, name: str) -> Image:
        # Helper (not a fixture): needs a real file on disk and no batch, so it uses baker.make directly.
        p = tmp_path / name
        p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16)
        with p.open("rb") as f:
            return baker.make(Image, source_image=File(f, name=name))

    def test_distinct_names_allowed(self, tmp_path):
        self._create_image(tmp_path, "a.jpg")
        self._create_image(tmp_path, "b.jpg")
        assert Image.objects.count() == 2

    def test_duplicate_source_image_rejected(self):
        # Inline, not a fixture: the second insert must fail, which only works when
        # the row creation happens inside the test body. Plain path strings bypass
        # storage uniquification, pinning the DB constraint.
        baker.make(Image, source_image="images/same.jpg")
        with pytest.raises(IntegrityError):
            baker.make(Image, source_image="images/same.jpg")

    def test_unique_constraint_declared(self):
        assert "image_source_image_unique" in [c.name for c in Image._meta.constraints]
