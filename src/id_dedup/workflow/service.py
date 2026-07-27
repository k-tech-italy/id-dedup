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
    all_images: list[Image] = []

    for label in result.groups:
        ticket = ClusterReviewTicket.objects.create(batch=batch, cluster_label=label)
        for member in result.groups[label]:
            if not member.file.exists():
                continue
            ext = "".join(member.file.suffixes)
            img = Image(batch=batch, ticket=ticket, embedding=member.embedding)
            with member.file.open("rb") as f:
                # save=False writes the file to storage and sets img.source_image.name
                # without a DB INSERT. Image.save() — including any future override — is
                # not called; bulk_create below handles all inserts in one query.
                img.source_image.save(f"{uuid.uuid4()}{ext}", File(f), save=False)
            all_images.append(img)
        tickets.append(ticket)

    Image.objects.bulk_create(all_images)
    return tickets
