from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from django.core.files import File
from django.db import transaction
from django.utils import timezone

from .models import Batch, ClusterReviewTicket, Conversation, Image, Trigger
from .tasks import process_reviewed_set

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from id_dedup.ml.pipeline import ClusterResult


@transaction.atomic
def create_tickets_from_result(
    result: ClusterResult,
    batch: Batch,
) -> list[ClusterReviewTicket]:
    """
    Create one ClusterReviewTicket per group cluster and persist images to the DB.

    Only DBSCAN groups (label >= 0) produce tickets. Singletons (label -1) bypass
    the review step entirely and are handled
    downstream. Images whose temp file no longer exists are skipped; their ticket
    is still created.
    """
    tickets: list[ClusterReviewTicket] = []

    for label in result.groups:
        ticket = ClusterReviewTicket.objects.create(batch=batch, cluster_label=label)
        for member in result.groups[label]:
            if not member.file.exists():
                continue
            ext = "".join(member.file.suffixes)
            with member.file.open("rb") as f:
                Image.objects.create(
                    batch=batch,
                    cluster_ticket=ticket,
                    embedding=member.embedding,
                    source_image=File(f, name=f"{uuid.uuid4()}{ext}"),
                )
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
def submit_ticket_review(
    ticket: ClusterReviewTicket,
    user: User | None = None,
    kept_ids: list[str] | None = None,
) -> None:
    """
    Finalise a cluster review by closing the ticket and advancing kept images.

    Closes the ticket (recording the reviewing user), creates a *CLUSTER_REVIEW*
    conversation to audit the outcome, and dispatches a *process_reviewed_set*
    task with the list of kept image IDs.
    """
    if ticket.is_closed:
        raise ValueError(f"Ticket {ticket.id} is already closed")

    ticket.close(user=user)

    kept_ids = kept_ids or []
    total = ticket.images.count()

    Conversation.objects.create(
        trigger=Trigger.CLUSTER_REVIEW,
        user=user,
        summary={
            "ticket_id": str(ticket.id),
            "cluster_label": ticket.cluster_label,
            "kept_count": len(kept_ids),
            "discarded_count": total - len(kept_ids),
            "kept_image_ids": [str(i) for i in kept_ids],
            "reviewed_by": user.username if user else None,
        },
        ended_at=timezone.now(),
    )

    process_reviewed_set.delay(ticket_id=str(ticket.id), kept_ids=kept_ids, user_id=user.pk if user else None)
