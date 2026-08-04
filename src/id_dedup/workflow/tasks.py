from __future__ import annotations

from celery import shared_task


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
    4. Open AdjudicationTickets for images with matches
    5. Assign identities, then close_if_drained(drained_ids=image_ids)
       on the referenced conversation.

    The body is a no-op for now. It must NOT drain the referenced conversation:
    until the set's images are actually parked (assigned an identity or placed
    in an adjudication ticket), draining would falsely close a conversation
    whose images are still in flight. The drain call arrives with that feature.
    """
