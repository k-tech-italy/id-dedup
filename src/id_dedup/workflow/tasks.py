from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import numpy as np
from celery import Celery, current_app, shared_task
from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from id_dedup.ml.pipeline import ClusterMember, ClusterResult, cluster_dbscan, extract_embedding
from id_dedup.workflow.models import (
    Batch,
    Conversation,
    Image,
    NothingToResume,
    OutboxMessage,
    OutboxMessageQuerySet,
)
from id_dedup.workflow.service import close_conversation_if_drained, create_tickets_from_result

logger = logging.getLogger(__name__)


class AlreadyClustered(Exception):
    """Clustering already happened during this conversation."""


@dataclass
class ClusteringWork:
    """Output of the clustering phase, passed to the commit phase."""

    result: ClusterResult
    valid_images: list[Image]
    singleton_ids: list[str]
    total_images: int


@shared_task
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
    short transaction (`Conversation.fail`), then the exception re-raises so
    Celery records/retries the task. Recording the failure is best-effort: if
    that write itself fails (e.g. the DB is unreachable) it is suppressed so
    the original error always propagates — losing the failure record to a
    second exception is the accepted tradeoff.
    """
    try:
        batch, conversation = _acquire_conversation(batch_id, user_id)
        if conversation is None:
            return
        work = _run_clustering(batch)
        _commit_clustering(batch, conversation, work, user_id)
    except (Batch.DoesNotExist, AlreadyClustered):
        return
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
    """Step 1: lock the batch row, get-or-create the UPLOAD conversation, resume if errored."""
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
    """Step 2: extract face embeddings and run DBSCAN."""
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


def _commit_clustering(
    batch: Batch,
    conversation: Conversation,
    work: ClusteringWork,
    user_id: int | None,
) -> None:
    """Step 4: single atomic commit — embeddings + tickets + singleton outbox + summary + drain."""
    with transaction.atomic():
        locked = Batch.objects.select_for_update().filter(pk=batch.pk).first()
        if locked is None:
            return
        conversation.refresh_from_db()
        if conversation.summary.get("clustering_done"):
            return

        if work.valid_images:
            for image in work.valid_images:
                image.updated_at = timezone.now()
            Image.objects.bulk_update(work.valid_images, ["embedding", "updated_at"])
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
                "clusters": len(work.result.groups),
                "tickets_created": len(tickets),
                "singletons": len(work.singleton_ids),
                "pending_image_ids": work.singleton_ids,
            },
        )
        close_conversation_if_drained(conversation)


@shared_task
def auto_adjudicate_set(
    image_ids: list[str],
    conversation_id: str | None = None,
    user_id: int | None = None,
) -> None:
    """
    Stub — placeholder for automatic identity assignment of an image set.

    Dispatched via the outbox from: process_batch (singletons → the UPLOAD
    conversation) and submit_ticket_review (kept survivors → their own
    CLUSTER_REVIEW conversation). Will eventually:
    1. Load images by *image_ids*
    2. Query pgvector for matches against existing Identity centroids
    3. Auto-create a new Identity for each image with no matches
    4. Open `AdjudicationTickets` for images with matches
    5. Assign identities, then `close_conversation_if_drained(drained_ids=image_ids)`
       on the referenced conversation.

    The body is a no-op for now. It must NOT drain the referenced conversation:
    until the set's images are actually parked (assigned an identity or placed
    in an adjudication ticket), draining would falsely close a conversation
    whose images are still in flight — `drained_ids` is exactly the
    conversation's pending set, so a stub drain would end it at dispatch time.
    The drain call arrives with that feature.
    """


@shared_task
def dispatch_outbox(limit: int = 50) -> int:
    """
    Publish pending OutboxMessage rows to the broker; return how many were dispatched.

    Each row is claimed in its own short transaction (`select_for_update`
    with `skip_locked`) so concurrent sweeps never double-claim and a slow
    broker never holds a multi-row lock. `dispatched_at` is set **after** a
    successful `send_task` (at-least-once: a crash in between leaves the row
    pending and it is re-sent, which every task tolerates via idempotency
    guards). A row whose `send_task` keeps failing is retried **once per
    sweep** (every ~10 s via beat) until `attempts >= max_attempts`, then
    dead-lettered and never swept again — a broker outage burns one attempt
    per sweep instead of exhausting the cap in a single run. Scheduled by
    Celery beat every 10 s; also runnable via the `dispatch_outbox`
    management command for manual/CI recovery.
    """
    dispatched = 0
    attempted: set[str] = set()
    for _ in range(limit):
        result = _dispatch_next(attempted)
        if result is None:
            break
        row_id, was_dispatched = result
        attempted.add(row_id)
        dispatched += was_dispatched
    return dispatched


@transaction.atomic
def _dispatch_next(attempted: set[str]) -> tuple[str, bool] | None:
    """
    Claim and deliver the next pending `OutboxMessage` in one transaction.

    Returns a tuple with the id of the message and a `bool` showing
    whether the message was dispatched successfully.
    """
    row = (
        cast("OutboxMessageQuerySet", OutboxMessage.objects)
        .dispatchable()
        .exclude(pk__in=attempted)
        .select_for_update(skip_locked=True)
        .order_by("created_at")
        .first()
    )
    if row is None:
        return None
    try:
        cast("Celery", current_app).send_task(row.task_name, kwargs=row.payload)
    except Exception as exc:  # noqa: BLE001 - record per-row, keep sweeping
        row.attempts += 1
        row.last_error = str(exc)
        if row.attempts >= row.max_attempts:
            row.dead_lettered_at = timezone.now()
            logger.error(
                "Outbox %s dead-lettered after %d/%d attempts (%s): %s",
                row.pk,
                row.attempts,
                row.max_attempts,
                row.task_name,
                exc,
            )
        row.save(update_fields=["attempts", "last_error", "dead_lettered_at"])
        return str(row.pk), False
    row.dispatched_at = timezone.now()  # mark AFTER send → at-least-once
    row.save(update_fields=["dispatched_at"])
    return str(row.pk), True
