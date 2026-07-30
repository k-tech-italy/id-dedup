from __future__ import annotations

from celery import shared_task


@shared_task
def process_reviewed_set(
    ticket_id: str,
    kept_ids: list[str] | None = None,
    user_id: int | None = None,
) -> None:
    """
    Stub — placeholder for auto-adjudication of a reviewed cluster's survivors.

    This task will eventually:
    1. Query survivors from *kept_ids*
    2. Run pgvector similarity matching against existing identities
    3. Auto-assign new identities for unmatched survivors
    4. Create AdjudicationTickets for matched survivors
    5. Log conversation events
    """
