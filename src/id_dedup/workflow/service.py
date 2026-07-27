from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from django.core.files import File
from django.db import transaction

from .models import Batch, ClusterReviewTicket, Image

if TYPE_CHECKING:
    from id_dedup.ml.pipeline import ClusterResult


@transaction.atomic
def create_tickets_from_result(
    result: ClusterResult,
    batch: Batch,
) -> list[ClusterReviewTicket]:
    """Create one ClusterReviewTicket per group cluster and persist images to the DB.

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
                    ticket=ticket,
                    embedding=member.embedding,
                    source_image=File(f, name=f"{uuid.uuid4()}{ext}"),
                )
        tickets.append(ticket)

    return tickets
