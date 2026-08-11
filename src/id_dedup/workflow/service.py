from __future__ import annotations

import contextlib
import pathlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

import numpy as np
from django.contrib.auth.models import User
from django.db import transaction

from id_dedup.images import UnsupportedImageType, validate_image
from id_dedup.ml.pipeline import ClusterMember, ClusterResult, cluster_dbscan, extract_embedding

from .models import (
    Batch,
    ClusterReviewTicket,
    Conversation,
    ConversationQuerySet,
    Image,
    NothingToResume,
    OutboxMessage,
    TicketAlreadyClosed,
    Trigger,
)

if TYPE_CHECKING:
    from django.core.files.uploadedfile import UploadedFile


class EmptyBatch(Exception):
    """No batch can be created — no files were uploaded, or none were valid images."""


def _check_uploads(uploads: list[UploadedFile] | None) -> tuple[list[UploadedFile], list[str]]:
    """
    Partition uploads into valid files and skipped names.

    Raises :class:`EmptyBatch` when the request carried no files at all, or
    when none of the uploaded files are valid images. Skipped names never
    appear in the message — they are the caller's audit trail.
    """
    if not uploads:
        raise EmptyBatch("No files selected.")

    valid: list[UploadedFile] = []
    skipped: list[str] = []
    for file in uploads:
        try:
            validate_image(file)
        except UnsupportedImageType:
            skipped.append(file.name or "upload")
        else:
            valid.append(file)

    if not valid:
        raise EmptyBatch("None of the uploaded files were valid images.")

    return valid, skipped


@transaction.atomic
def register_upload(
    uploads: list[UploadedFile] | None,
    user_id: int | None = None,
) -> Batch:
    """
    Validate uploads, register Batch + Image rows, and enqueue processing atomically.

    Invalid files are skipped rather than fatal: their names are recorded on
    `batch.skipped_files` as an audit trail and only the valid files are
    registered. If nothing valid remains, :class:`EmptyBatch` is raised and
    nothing is persisted. Each valid file's name is uniquified with a
    `time.time_ns()` suffix so same-named uploads cannot overwrite each other
    in storage; the `UniqueConstraint` on `Image.source_image` is the backoff.
    No broker calls are made — the resulting `OutboxMessage` is published
    later by the `dispatch_outbox` reaper.
    """
    valid, skipped = _check_uploads(uploads)

    # FIXME: enforce file count / total size limits at upload.
    batch = Batch.new()
    if skipped:
        batch.record_skipped_files(skipped)
    Image.register_uploads(batch, valid)

    OutboxMessage.new(
        task="id_dedup.workflow.tasks.process_batch",
        payload={"batch_id": str(batch.pk), "user_id": user_id},
    )
    return batch


class AlreadyClustered(Exception):
    """Clustering already happened during this conversation."""


@dataclass
class ClusteringWork:
    """Output of the clustering phase, passed to the commit phase."""

    result: ClusterResult
    valid_images: list[Image]
    singleton_ids: list[str]
    total_images: int


def process_batch(batch_id: str, user_id: int | None = None) -> None:
    """
    Extract embeddings, cluster the batch's images, and open cluster review tickets.

    Single-commit design: the only DB writes are (a) the conversation
    dedupe under the batch lock and (b) one atomic commit at the end containing
    embeddings + tickets + singleton outbox + conversation summary (clustering_done)
    + drain evaluation. A crash either leaves nothing committed (clean retry) or
    leaves everything committed including a durable outbox row for the singletons.
    Idempotency is keyed on `summary.clustering_done` so redelivery — including
    zero-ticket batches — returns early.

    Error handling: on failure the conversation is marked failed in its own
    short transaction (`Conversation.fail`), then the exception re-raises so the
    Celery task records/retries it. Recording the failure is best-effort: if
    that write itself fails (e.g. the DB is unreachable) it is suppressed so
    the original error always propagates — losing the failure record to a
    second exception is the accepted tradeoff.

    Raises :class:`Batch.DoesNotExist` if the batch is gone (a no-op for the
    caller) and :class:`AlreadyClustered` on idempotent redelivery.
    """
    conversation: Conversation | None = None
    try:
        batch, conversation = _acquire_conversation(batch_id, user_id)
        if conversation is None:
            return
        work = _run_clustering(batch)
        _commit_clustering(batch, conversation, work, user_id)
    except Exception as exc:
        if conversation is not None:
            # best-effort: never mask the original error
            with contextlib.suppress(Exception):  # noqa: BLE001
                conversation.fail(str(exc))
        raise


@transaction.atomic
def _acquire_conversation(
    batch_id: str,
    user_id: int | None,
) -> tuple[Batch, Conversation]:
    """Lock the batch row, get-or-create the UPLOAD conversation, resume if errored."""
    batch = Batch.objects.select_for_update().filter(pk=batch_id).get()
    user = User.objects.get(pk=user_id) if user_id else None
    conversation, created = Conversation.get_or_create_for_upload(batch, user)
    if conversation.summary.get("clustering_done"):
        # idempotent redelivery, incl. zero-ticket batches
        raise AlreadyClustered()
    if not created:
        # NOTE: `NothingToResume` is an expected signal, not a failure
        with contextlib.suppress(NothingToResume):
            # clear error fields if a previous run failed
            conversation.resume()
    return batch, conversation


def _run_clustering(batch: Batch) -> ClusteringWork:
    """Extract face embeddings and run DBSCAN."""
    images = list(batch.images.order_by("created_at", "pk"))
    valid: list[Image] = []
    by_path: dict[Path, Image] = {}

    for image in images:
        embedding = extract_embedding(image.source_image.path)
        if embedding is None:
            continue
        image.embedding = np.asarray(embedding, dtype=np.float32)
        by_path[Path(image.source_image.path)] = image
        valid.append(image)

    result = ClusterResult()
    if valid:
        embeddings = np.stack([np.asarray(img.embedding, dtype=np.float32) for img in valid])
        labels, normalized = cluster_dbscan(embeddings)
        for image, emb, label in zip(valid, normalized, labels, strict=False):
            image.embedding = emb.tolist()
            result.clusters.setdefault(int(label), []).append(
                ClusterMember(file=Path(image.source_image.path), embedding=emb),
            )

    singleton_ids = [str(by_path[member.file].pk) for member in result.singletons]
    return ClusteringWork(
        result=result,
        valid_images=valid,
        singleton_ids=singleton_ids,
        total_images=len(images),
    )


@transaction.atomic
def _commit_clustering(
    batch: Batch,
    conversation: Conversation,
    work: ClusteringWork,
    user_id: int | None,
) -> None:
    """Single atomic commit — embeddings + tickets + singleton outbox + summary + drain."""
    locked = Batch.objects.select_for_update().filter(pk=batch.pk).first()
    if locked is None:
        return
    conversation.refresh_from_db()
    if conversation.summary.get("clustering_done"):
        return

    if work.valid_images:
        Image.bulk_store_embeddings(work.valid_images)
    tickets = create_tickets_from_result(work.result, locked)

    if work.singleton_ids:
        OutboxMessage.objects.create(
            task_name="id_dedup.workflow.tasks.auto_adjudicate_set",
            payload={
                "image_ids": work.singleton_ids,
                "conversation_id": str(conversation.pk),
                "user_id": user_id,
            },
        )

    conversation.mark_clustered(
        {
            "batch_id": str(locked.pk),
            "total_images": work.total_images,
            "embeddings_extracted": len(work.valid_images),
            "failed_images": work.total_images - len(work.valid_images),
            "skipped_files": locked.skipped_files,
            "clusters": len(work.result.groups),
            "tickets_created": len(tickets),
            "singletons": len(work.singleton_ids),
            "pending_image_ids": work.singleton_ids,
        },
    )
    close_conversation_if_drained(conversation)


@transaction.atomic
def create_tickets_from_result(
    result: ClusterResult,
    batch: Batch,
) -> list[ClusterReviewTicket]:
    """
    Create one ClusterReviewTicket per group cluster, linking the batch's registered images.

    Images are matched by source_image.path and linked to their ticket — a
    graph edge only. Embeddings are never written here: they are persisted
    for every valid image earlier in the clustering commit
    (`_commit_clustering`), before this function runs. Singletons (label -1)
    produce no tickets. Members whose path doesn't match a registered image
    are skipped; the ticket is still created.
    """
    by_path = {
        pathlib.Path(image.source_image.path): image
        for image in Image.objects.select_for_update().filter(batch=batch).order_by("pk")
    }
    tickets: list[ClusterReviewTicket] = []

    for label in result.groups:
        ticket = ClusterReviewTicket.new(batch, label)
        to_update: list[Image] = []
        for member in result.groups[label]:
            image = by_path.get(member.file)
            if image is None:
                continue
            to_update.append(image)
        Image.bulk_link_to_ticket(ticket, to_update)
        tickets.append(ticket)

    return tickets


def get_kept_image_ids(ticket: ClusterReviewTicket) -> set[str]:
    """Return kept image UUIDs for a closed ticket. Returns empty set if open."""
    if not ticket.is_closed:
        return set()
    conversation = (
        Conversation.objects.filter(
            trigger=Trigger.CLUSTER_REVIEW,
            summary__ticket_id=str(ticket.pk),
        )
        .only("summary")
        .first()
    )
    if conversation is None:
        return set()
    return set(conversation.summary.get("kept_image_ids", []))


@transaction.atomic
def close_conversation_if_drained(
    conversation: Conversation,
    drained_ids: list[str] | None = None,
) -> bool:
    """
    Remove drained image IDs from the pending set and close if empty.

    Returns whether this call closed the conversation.
    """
    if drained_ids:
        conversation.drain_images(drained_ids)
    if conversation.is_drained():
        return conversation.close()
    return False


@transaction.atomic
def submit_ticket_review(
    ticket: ClusterReviewTicket,
    user: User | None = None,
    kept_ids: list[str] | None = None,
) -> list[str]:
    """
    Finalise a cluster review by closing the ticket and advancing kept images.

    Closes the ticket (recording the reviewing user), creates a separate
    *CLUSTER_REVIEW* conversation tracking the kept survivors (parented to the
    upload conversation, lineage only), and writes a durable *OutboxMessage*
    for auto-adjudication of the kept set — all in one transaction.

    Raises :class:`TicketAlreadyClosed` if the ticket is already closed or the
    close lost a race against another caller. Returns the normalized kept image
    IDs (ticket members only, order-preserving, deduplicated).
    """
    if ticket.is_closed:
        raise TicketAlreadyClosed(f"Ticket {ticket.id} is already closed")

    claimed = ticket.close(user=user)
    if not claimed:
        raise TicketAlreadyClosed(f"Ticket {ticket.id} lost the close race")

    ticket_image_pks = {str(pk) for pk in ticket.images.values_list("pk", flat=True)}
    seen: set[str] = set()
    normalized: list[str] = []
    for raw in kept_ids or []:
        pk = str(raw)
        if pk in ticket_image_pks and pk not in seen:
            seen.add(pk)
            normalized.append(pk)

    parent = cast("ConversationQuerySet", Conversation.objects).upload_for_batch(ticket.batch).first()
    conversation = Conversation.create_for_cluster_review(
        ticket,
        normalized,
        user=user,
        parent=parent,
    )

    if normalized:
        OutboxMessage.new(
            task="id_dedup.workflow.tasks.auto_adjudicate_set",
            payload={
                "image_ids": normalized,
                "conversation_id": str(conversation.pk),
                "user_id": user.pk if user else None,
            },
        )

    close_conversation_if_drained(conversation)
    return normalized
