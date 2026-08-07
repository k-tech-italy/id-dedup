from __future__ import annotations

import pathlib
import time
from typing import TYPE_CHECKING, cast

from django.db import transaction

from id_dedup.images import UnsupportedImageType, is_valid_image

from .models import (
    Batch,
    ClusterReviewTicket,
    Conversation,
    ConversationQuerySet,
    Image,
    OutboxMessage,
    TicketAlreadyClosed,
    Trigger,
)

if TYPE_CHECKING:
    from django.contrib.auth.models import User
    from django.core.files.uploadedfile import UploadedFile

    from id_dedup.ml.pipeline import ClusterResult


class NoFilesUploaded(Exception):
    """The upload request contained no files."""


@transaction.atomic
def register_upload(
    uploads: list[UploadedFile] | None,
    user_id: int | None = None,
) -> Batch:
    """
    Validate uploads, register Batch + Image rows, and enqueue processing atomically.

    Each file's name is uniquified with a `time.time_ns()` suffix so same-named
    uploads cannot overwrite each other in storage. The `UniqueConstraint` on
    `Image.source_image` is the backoff. No broker calls are made — the
    resulting `OutboxMessage` is published later by the `dispatch_outbox` reaper.
    """
    if not uploads:
        raise NoFilesUploaded("No files selected.")

    invalid = [name for file in uploads if (name := file.name) and not is_valid_image(file)]
    if invalid:
        raise UnsupportedImageType(
            f"Unsupported or corrupt file type(s): {', '.join(invalid)}. Only JPG, PNG, and WEBP are accepted.",
        )

    # FIXME: enforce file count / total size limits at upload.
    batch = Batch.new()
    images: list[Image] = []
    for file in uploads:
        name = file.name or "upload"
        path = pathlib.Path(name)
        file.name = f"{path.stem}_{time.time_ns()}{path.suffix}"
        images.append(Image(batch=batch, source_image=file))
    # FIXME: bulk_create() does not call the pre_save() signal
    Image.objects.bulk_create(images)  # FileField.pre_save writes each file to storage

    OutboxMessage.objects.create(
        task_name="id_dedup.workflow.tasks.process_batch",
        payload={"batch_id": str(batch.pk), "user_id": user_id},
    )
    return batch


@transaction.atomic
def create_tickets_from_result(
    result: ClusterResult,
    batch: Batch,
) -> list[ClusterReviewTicket]:
    """
    Create one ClusterReviewTicket per group cluster, linking the batch's registered images.

    Images are matched by source_image.path. Singletons (label -1) produce no
    tickets. Members whose path doesn't match a registered image are skipped;
    the ticket is still created.
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
            image.assign_to_cluster(ticket, member.embedding.tolist(), save=False)
            to_update.append(image)
        Image.objects.bulk_update(to_update, ["cluster_ticket", "embedding", "updated_at"])
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
