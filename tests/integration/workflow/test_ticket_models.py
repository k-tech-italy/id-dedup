import datetime

import pytest


@pytest.mark.django_db
class TestClusterReviewTicketQuerySet:
    def test_open_returns_tickets_without_closed_at(self):
        from id_dedup.workflow.models import Batch, ClusterReviewTicket

        batch = Batch.objects.create()
        t1 = ClusterReviewTicket.objects.create(batch=batch, cluster_label=0)
        ClusterReviewTicket.objects.create(
            batch=batch,
            cluster_label=1,
            closed_at=datetime.datetime(2026, 7, 24, 12, 0, 0, tzinfo=datetime.timezone.utc),
        )

        assert list(ClusterReviewTicket.objects.open().values_list("pk", flat=True)) == [t1.pk]

    def test_closed_returns_tickets_with_closed_at(self):
        from id_dedup.workflow.models import Batch, ClusterReviewTicket

        batch = Batch.objects.create()
        ClusterReviewTicket.objects.create(batch=batch, cluster_label=0)
        t2 = ClusterReviewTicket.objects.create(
            batch=batch,
            cluster_label=1,
            closed_at=datetime.datetime(2026, 7, 24, 12, 0, 0, tzinfo=datetime.timezone.utc),
        )

        assert list(ClusterReviewTicket.objects.closed().values_list("pk", flat=True)) == [t2.pk]

    def test_open_excludes_closed_tickets(self):
        from id_dedup.workflow.models import Batch, ClusterReviewTicket

        batch = Batch.objects.create()
        ClusterReviewTicket.objects.create(
            batch=batch,
            cluster_label=0,
            closed_at=datetime.datetime(2026, 7, 24, 12, 0, 0, tzinfo=datetime.timezone.utc),
        )

        assert ClusterReviewTicket.objects.open().count() == 0

    def test_closed_excludes_open_tickets(self):
        from id_dedup.workflow.models import Batch, ClusterReviewTicket

        batch = Batch.objects.create()
        ClusterReviewTicket.objects.create(batch=batch, cluster_label=0)

        assert ClusterReviewTicket.objects.closed().count() == 0

    def test_close_method_sets_closed_at(self):
        from id_dedup.workflow.models import Batch, ClusterReviewTicket

        batch = Batch.objects.create()
        ticket = ClusterReviewTicket.objects.create(batch=batch, cluster_label=0)
        assert ticket.closed_at is None

        ticket.close()
        ticket.refresh_from_db()

        assert ticket.closed_at is not None

    def test_close_moves_ticket_to_closed_queryset(self):
        from id_dedup.workflow.models import Batch, ClusterReviewTicket

        batch = Batch.objects.create()
        ticket = ClusterReviewTicket.objects.create(batch=batch, cluster_label=0)
        ticket.close()

        assert ClusterReviewTicket.objects.open().count() == 0
        assert ClusterReviewTicket.objects.closed().count() == 1
