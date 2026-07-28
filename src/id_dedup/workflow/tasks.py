from __future__ import annotations

from celery import shared_task


@shared_task
def process_reviewed_set(ticket_id: str, user_id: int | None = None) -> None:
    """
    Stub — placeholder for auto-adjudication of a reviewed cluster's survivors.

    This task will eventually:
    1. Query survivors (Image.objects.filter(cluster_ticket_id=ticket_id, discarded=False))
    2. Run pgvector similarity matching against existing identities
    3. Auto-assign new identities for unmatched survivors
    4. Create AdjudicationTickets for matched survivors
    5. Log conversation events
    """
