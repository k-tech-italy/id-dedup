from __future__ import annotations

import logging
from typing import cast

from celery import Celery, current_app, shared_task
from django.db import transaction
from django.utils import timezone

from id_dedup.workflow import service
from id_dedup.workflow.models import Batch, OutboxMessage, OutboxMessageQuerySet

logger = logging.getLogger(__name__)


@shared_task
def process_batch(batch_id: str, user_id: int | None = None) -> None:
    """Dispatch clustering of a batch's images to the service layer."""
    try:
        service.process_batch(batch_id, user_id)
    except (Batch.DoesNotExist, service.AlreadyClustered):
        # the exceptions caught here are expected signals, not failures
        return


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
    pending and it is re-sent, which the tasks on this outbox tolerate via
    idempotency guards). A row whose `send_task` keeps failing is attempted
    at most once per sweep (interval `OUTBOX_SWEEP_SECONDS`, default 10 s)
    until `attempts >= max_attempts` (`OUTBOX_MAX_ATTEMPTS`, default 5),
    then dead-lettered and never swept again — a broker outage burns one
    attempt per sweep instead of exhausting the cap in a single run. Scheduled
    by Celery beat; also runnable via the `dispatch_outbox` management command
    for manual/CI recovery.

    :param limit: max pending rows a single sweep attempts (default 50). Caps a
        beat tick's work so it stays short; any remaining rows are picked up by
        the next sweep. Counts attempts, not just successes — a row whose
        ``send_task`` fails still consumes one iteration.
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
