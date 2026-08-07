import io
import uuid
from typing import cast

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from id_dedup.workflow.models import Batch, ClusterReviewTicket, ClusterReviewTicketQuerySet, Image


class Command(BaseCommand):
    help = "Seed the database with 20 open cluster review tickets for manual testing."

    def handle(self, *args, **options):
        batch, _ = Batch.objects.get_or_create(id=uuid.UUID("00000000-0000-0000-0000-000000000001"))

        for label in range(20):
            ticket, created = ClusterReviewTicket.objects.get_or_create(
                batch=batch,
                cluster_label=label,
                defaults={"reviewed_by": None},
            )
            if not created:
                self.stdout.write(f"  Ticket cluster={label} already exists, skipping")
                continue

            for i in range(3):
                buf = io.BytesIO()
                buf.write(b"\xff\xd8\xff\xe0" + b"\x00" * 64)
                name = f"seed_{label}_{i}.jpg"
                Image.objects.create(
                    batch=batch,
                    cluster_ticket=ticket,
                    source_image=ContentFile(buf.getvalue(), name=name),
                )

            self.stdout.write(self.style.SUCCESS(f"  Created ticket cluster={label} with 3 images"))

        open_count = cast("ClusterReviewTicketQuerySet", ClusterReviewTicket.objects).open().count()
        self.stdout.write(self.style.SUCCESS(f"Done — {open_count} open tickets"))
