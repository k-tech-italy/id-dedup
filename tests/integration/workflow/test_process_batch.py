from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pytest
from django.contrib.auth.models import User
from django.core.files import File

from id_dedup.workflow import service as workflow_service
from id_dedup.workflow.models import (
    Batch,
    ClusterReviewTicket,
    Conversation,
    Image,
    OutboxMessage,
    Trigger,
)
from id_dedup.workflow.tasks import auto_adjudicate_set, process_batch

if TYPE_CHECKING:
    import pathlib

_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
_AUTO_ADJUDICATE_TASK = "id_dedup.workflow.tasks.auto_adjudicate_set"


def _unit_vector(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.random(512).astype(np.float32)
    return v / np.linalg.norm(v)


def _make_images(batch: Batch, tmp_path: "pathlib.Path", count: int) -> list[Image]:
    images = []
    for i in range(count):
        name = f"img{i}.jpg"
        path = tmp_path / name
        path.write_bytes(_JPEG)
        with path.open("rb") as f:
            images.append(Image.objects.create(batch=batch, source_image=File(f, name=name)))
    return images


def _patch_pipeline(
    monkeypatch,
    images: list[Image],
    *,
    labels: list[int],
    none_indices: set[int] | None = None,
) -> None:
    """
    Patch extract_embedding and cluster_dbscan for the given images.

    ``labels`` has one entry per image (in creation order); entries for images
    whose index is in ``none_indices`` are skipped because extract_embedding
    returns None for them.
    """
    none_set = none_indices or set()

    extract_map: dict[str, np.ndarray | None] = {}
    for i, img in enumerate(images):
        p = str(img.source_image.path)
        extract_map[p] = None if i in none_set else _unit_vector(i + 1)

    monkeypatch.setattr(
        "id_dedup.workflow.service.extract_embedding",
        lambda path: extract_map.get(str(path)),
    )

    valid_labels = np.array(
        [labels[i] for i in range(len(labels)) if i not in none_set],
        dtype=int,
    )

    def _cluster(embeddings: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        normalized = embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)
        return valid_labels, normalized

    monkeypatch.setattr("id_dedup.workflow.service.cluster_dbscan", _cluster)


def _upload_conv(batch: Batch) -> Conversation:
    return Conversation.objects.get(
        trigger=Trigger.UPLOAD,
        summary__batch_id=str(batch.pk),
    )


def _raise(message: str):
    def _raiser(*_args, **_kwargs):
        raise RuntimeError(message)

    return _raiser


@pytest.mark.django_db
class TestProcessBatch:
    def test_happy_path_mixed_clusters_and_singletons(self, monkeypatch, tmp_path):
        batch = Batch.objects.create()
        images = _make_images(batch, tmp_path, 4)
        _patch_pipeline(monkeypatch, images, labels=[0, 0, -1, -1])

        process_batch(str(batch.pk))

        # One ticket for the [0, 0] group
        assert ClusterReviewTicket.objects.count() == 1
        ticket = ClusterReviewTicket.objects.get()
        assert ticket.images.count() == 2

        # Two singletons → one auto-adjudicate outbox message
        assert OutboxMessage.objects.count() == 1
        msg = OutboxMessage.objects.get()
        assert msg.task_name == _AUTO_ADJUDICATE_TASK
        conv = _upload_conv(batch)
        assert msg.payload["conversation_id"] == str(conv.pk)
        assert msg.payload["user_id"] is None
        assert set(msg.payload["image_ids"]) == {str(images[2].pk), str(images[3].pk)}

        # Conversation is pending with correct summary
        assert conv.ended_at is None
        assert conv.summary["clustering_done"] is True
        assert conv.summary["total_images"] == 4
        assert conv.summary["embeddings_extracted"] == 4
        assert conv.summary["failed_images"] == 0
        assert conv.summary["clusters"] == 1
        assert conv.summary["tickets_created"] == 1
        assert conv.summary["singletons"] == 2
        assert set(conv.summary["pending_image_ids"]) == {str(images[2].pk), str(images[3].pk)}

    def test_all_valid_images_persist_embeddings(self, monkeypatch, tmp_path):
        """Embeddings are persisted at clustering time for every valid image — including singletons."""
        batch = Batch.objects.create()
        images = _make_images(batch, tmp_path, 4)
        _patch_pipeline(monkeypatch, images, labels=[0, 0, -1, -1])

        process_batch(str(batch.pk))

        for i, img in enumerate(images):
            img.refresh_from_db()
            assert img.embedding is not None, f"{img.source_image.name} lost its embedding"
            assert np.allclose(np.asarray(img.embedding), _unit_vector(i + 1))

    def test_singletons_only(self, monkeypatch, tmp_path):
        batch = Batch.objects.create()
        images = _make_images(batch, tmp_path, 3)
        _patch_pipeline(monkeypatch, images, labels=[-1, -1, -1])

        process_batch(str(batch.pk))

        assert ClusterReviewTicket.objects.count() == 0
        assert OutboxMessage.objects.count() == 1
        conv = _upload_conv(batch)
        assert conv.ended_at is None  # still pending — singletons not yet adjudicated
        assert set(conv.summary["pending_image_ids"]) == {str(i.pk) for i in images}

    def test_all_in_one_cluster_ends_conversation(self, monkeypatch, tmp_path):
        batch = Batch.objects.create()
        images = _make_images(batch, tmp_path, 3)
        _patch_pipeline(monkeypatch, images, labels=[0, 0, 0])

        process_batch(str(batch.pk))

        assert ClusterReviewTicket.objects.count() == 1
        assert OutboxMessage.objects.count() == 0
        conv = _upload_conv(batch)
        assert conv.ended_at is not None  # drained — no singletons

    def test_all_failed_ends_conversation(self, monkeypatch, tmp_path):
        batch = Batch.objects.create()
        images = _make_images(batch, tmp_path, 3)
        _patch_pipeline(monkeypatch, images, labels=[-1, -1, -1], none_indices={0, 1, 2})

        process_batch(str(batch.pk))

        assert ClusterReviewTicket.objects.count() == 0
        assert OutboxMessage.objects.count() == 0
        conv = _upload_conv(batch)
        assert conv.summary["total_images"] == 3
        assert conv.summary["embeddings_extracted"] == 0
        assert conv.summary["failed_images"] == 3
        assert conv.ended_at is not None  # drained — pending set empty

    def test_error_path_sets_error_and_propagates(self, monkeypatch, tmp_path):
        batch = Batch.objects.create()
        _make_images(batch, tmp_path, 2)

        monkeypatch.setattr("id_dedup.workflow.service.extract_embedding", _raise("disk read error"))

        with pytest.raises(RuntimeError, match="disk read error"):
            process_batch(str(batch.pk))

        conv = _upload_conv(batch)
        assert conv.error_message
        assert conv.ended_at is not None

    def test_fail_record_failure_does_not_mask_original(self, monkeypatch, tmp_path):
        batch = Batch.objects.create()
        _make_images(batch, tmp_path, 2)

        monkeypatch.setattr("id_dedup.workflow.service.extract_embedding", _raise("disk read error"))

        def _failing_fail(conversation, message):
            raise RuntimeError("cannot record failure")

        monkeypatch.setattr(Conversation, "fail", _failing_fail)

        with pytest.raises(RuntimeError, match="disk read error"):
            process_batch(str(batch.pk))

    def test_atomicity_rollback_on_ticket_creation_failure(self, monkeypatch, tmp_path):
        batch = Batch.objects.create()
        images = _make_images(batch, tmp_path, 4)
        _patch_pipeline(monkeypatch, images, labels=[0, 0, -1, -1])

        should_fail = [True]
        create_tickets = workflow_service.create_tickets_from_result

        def _maybe_fail(result, batch):
            if should_fail[0]:
                raise RuntimeError("ticket creation failed")
            return create_tickets(result, batch)

        monkeypatch.setattr("id_dedup.workflow.service.create_tickets_from_result", _maybe_fail)

        with pytest.raises(RuntimeError, match="ticket creation failed"):
            process_batch(str(batch.pk))

        # Nothing committed from the failed transaction
        assert ClusterReviewTicket.objects.count() == 0
        assert OutboxMessage.objects.count() == 0
        assert Image.objects.filter(embedding__isnull=False).count() == 0
        conv = _upload_conv(batch)
        assert conv.error_message
        assert conv.ended_at is not None

        # Re-run: patches for extract/cluster still active, create_tickets succeeds
        should_fail[0] = False
        process_batch(str(batch.pk))

        assert ClusterReviewTicket.objects.count() == 1
        assert OutboxMessage.objects.count() == 1
        assert Conversation.objects.count() == 1
        conv.refresh_from_db()
        assert conv.error_message == ""
        assert conv.summary["clustering_done"] is True

    def test_retry_after_failure(self, monkeypatch, tmp_path):
        batch = Batch.objects.create()
        images = _make_images(batch, tmp_path, 3)

        # First run: extract fails
        monkeypatch.setattr("id_dedup.workflow.service.extract_embedding", _raise("disk error"))

        with pytest.raises(RuntimeError, match="disk error"):
            process_batch(str(batch.pk))

        # Second run: patches for success
        _patch_pipeline(monkeypatch, images, labels=[0, 0, 0])
        process_batch(str(batch.pk))

        conv = _upload_conv(batch)
        assert conv.error_message == ""
        assert conv.summary["clustering_done"] is True
        assert Conversation.objects.count() == 1
        assert ClusterReviewTicket.objects.count() == 1

    def test_idempotent_rerun_after_ticket_batch(self, monkeypatch, tmp_path):
        batch = Batch.objects.create()
        images = _make_images(batch, tmp_path, 3)
        _patch_pipeline(monkeypatch, images, labels=[0, 0, 0])

        process_batch(str(batch.pk))
        assert ClusterReviewTicket.objects.count() == 1
        assert OutboxMessage.objects.count() == 0

        process_batch(str(batch.pk))
        assert ClusterReviewTicket.objects.count() == 1  # no second ticket
        assert OutboxMessage.objects.count() == 0

    def test_idempotent_rerun_singletons_only(self, monkeypatch, tmp_path):
        batch = Batch.objects.create()
        images = _make_images(batch, tmp_path, 3)
        _patch_pipeline(monkeypatch, images, labels=[-1, -1, -1])

        process_batch(str(batch.pk))
        assert OutboxMessage.objects.count() == 1

        process_batch(str(batch.pk))
        assert OutboxMessage.objects.count() == 1  # exactly one (R4)

    def test_no_face_image_counted_as_failed(self, monkeypatch, tmp_path):
        batch = Batch.objects.create()
        images = _make_images(batch, tmp_path, 4)
        _patch_pipeline(monkeypatch, images, labels=[0, 0, -1, -1], none_indices={2})

        process_batch(str(batch.pk))

        conv = _upload_conv(batch)
        assert conv.summary["total_images"] == 4
        assert conv.summary["embeddings_extracted"] == 3
        assert conv.summary["failed_images"] == 1
        assert conv.summary["singletons"] == 1
        assert str(images[3].pk) in conv.summary["pending_image_ids"]
        assert str(images[2].pk) not in conv.summary["pending_image_ids"]

        # Failed image not linked to any ticket
        failed = images[2]
        failed.refresh_from_db()
        assert failed.cluster_ticket is None
        assert failed.embedding is None

        # Valid singleton still gets its embedding persisted early
        valid = images[3]
        valid.refresh_from_db()
        assert valid.cluster_ticket is None
        assert np.allclose(np.asarray(valid.embedding), _unit_vector(4))

    def test_missing_batch_is_noop(self):
        process_batch("00000000-0000-0000-0000-000000000000")
        assert Batch.objects.count() == 0
        assert Conversation.objects.count() == 0
        assert OutboxMessage.objects.count() == 0

    def test_user_id_recorded_in_conversation(self, monkeypatch, tmp_path):
        user = User.objects.create_user(username="worker", password="pass")
        batch = Batch.objects.create()
        images = _make_images(batch, tmp_path, 3)
        _patch_pipeline(monkeypatch, images, labels=[-1, -1, -1])

        process_batch(str(batch.pk), user_id=user.pk)

        conv = _upload_conv(batch)
        assert conv.user == user
        msg = OutboxMessage.objects.get()
        assert msg.payload["user_id"] == user.pk

    def test_singleton_outbox_dispatches_auto_adjudicate(self, monkeypatch, tmp_path):
        """The singleton outbox row points at auto_adjudicate_set with the upload conversation id."""
        batch = Batch.objects.create()
        images = _make_images(batch, tmp_path, 2)
        _patch_pipeline(monkeypatch, images, labels=[-1, -1])

        process_batch(str(batch.pk))

        conv = _upload_conv(batch)
        msg = OutboxMessage.objects.get()
        assert msg.task_name == _AUTO_ADJUDICATE_TASK
        assert msg.payload["conversation_id"] == str(conv.pk)
        assert msg.payload["image_ids"] == [str(i.pk) for i in images]


@pytest.mark.django_db
class TestAutoAdjudicateSetStub:
    def test_stub_does_not_drain_upload_conversation(self, monkeypatch, tmp_path):
        """The stub must not close a conversation whose pending set matches image_ids."""
        batch = Batch.objects.create()
        images = _make_images(batch, tmp_path, 2)
        _patch_pipeline(monkeypatch, images, labels=[-1, -1])

        process_batch(str(batch.pk))

        conv = _upload_conv(batch)
        assert conv.ended_at is None  # still pending — singletons not yet adjudicated

        auto_adjudicate_set(list(conv.summary["pending_image_ids"]), conversation_id=str(conv.pk))

        conv.refresh_from_db()
        assert conv.ended_at is None  # stub is a no-op — drain arrives with real adjudication
        assert set(conv.summary["pending_image_ids"]) == {str(i.pk) for i in images}

    def test_stub_noops_without_conversation(self):
        """No conversation id (or a missing one) must not raise for the no-op stub."""
        auto_adjudicate_set(["does-not-matter"])
        auto_adjudicate_set(["does-not-matter"], conversation_id="00000000-0000-0000-0000-000000000000")
