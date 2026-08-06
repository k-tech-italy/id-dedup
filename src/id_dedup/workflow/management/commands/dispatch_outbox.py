import argparse

from django.core.management.base import BaseCommand

from id_dedup.workflow.models import OutboxMessage
from id_dedup.workflow.tasks import dispatch_outbox


class Command(BaseCommand):
    """Publish pending OutboxMessage rows to the Celery broker (manual/CI recovery)."""

    help = "Publish pending OutboxMessage rows to the Celery broker (manual/CI recovery)."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Register the ``--dead`` and ``--requeue-dead`` flags."""
        parser.add_argument(
            "--dead",
            action="store_true",
            help="List dead-lettered outbox rows instead of dispatching.",
        )
        parser.add_argument(
            "--requeue-dead",
            action="store_true",
            help="Reset dead-lettered rows (attempts, last_error, dead marker) so the sweep retries them.",
        )

    def handle(self, *args: str, **options: object) -> None:
        """Dispatch pending rows, or list/requeue dead-lettered rows when a flag is given."""
        if options["dead"]:
            self._list_dead()
            return
        if options["requeue_dead"]:
            self._requeue_dead()
            return
        result = dispatch_outbox.apply()
        count = result.get()
        self.stdout.write(self.style.SUCCESS(f"Dispatched {count} message(s)"))

    def _list_dead(self) -> None:
        """Print dead-lettered rows in oldest-first order."""
        rows = OutboxMessage.objects.filter(dead_lettered_at__isnull=False).order_by("created_at")
        if not rows.exists():
            self.stdout.write("No dead-lettered messages.")
            return
        for row in rows:
            self.stdout.write(
                f"{row.pk} {row.task_name} "
                f"attempts={row.attempts}/{row.max_attempts} "
                f"error={row.last_error!r} created={row.created_at}",
            )

    def _requeue_dead(self) -> None:
        """Reset dead-lettered rows so the sweep retries them."""
        count = OutboxMessage.objects.filter(dead_lettered_at__isnull=False).update(
            dead_lettered_at=None,
            attempts=0,
            last_error="",
        )
        self.stdout.write(self.style.SUCCESS(f"Requeued {count} dead-lettered message(s)"))
