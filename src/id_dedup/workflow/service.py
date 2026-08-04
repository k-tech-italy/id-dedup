from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from django.core.files import File
from django.db import transaction

from .models import Batch, ClusterReviewTicket, Conversation, Image, OutboxMessage, TicketAlreadyClosed, Trigger

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
def close_if_drained(conversation: Conversation, drained_ids: list[str] | None = None) -> bool:
    """
    Remove drained image IDs from the pending set and close if empty.

    Business-logic orchestration over three model primitives:
    ``Conversation.remove_from_pending``, ``Conversation.is_drained``,
    and ``Conversation.close``.

    Returns whether this call closed the conversation.
    """
    if drained_ids:
        conversation.remove_from_pending(drained_ids)
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

    parent = Conversation.objects.upload_for_batch(ticket.batch_id).first()
    conversation = Conversation.create_for_cluster_review(
        ticket=ticket,
        user=user,
        kept_ids=normalized,
        parent=parent,
    )

    if normalized:
        OutboxMessage.objects.create(
            task_name="id_dedup.workflow.tasks.auto_adjudicate_set",
            payload={
                "image_ids": normalized,
                "conversation_id": str(conversation.pk),
                "user_id": user.pk if user else None,
            },
        )

    close_if_drained(conversation)
    return normalized
