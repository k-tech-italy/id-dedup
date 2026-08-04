import pytest
from id_dedup.workflow.models import ClusterReviewTicket


@pytest.mark.django_db
class TestClusterReviewTicketQuerySet:
    def test_open_returns_tickets_without_closed_at(self, open_ticket, closed_ticket):
        assert list(ClusterReviewTicket.objects.open().values_list("pk", flat=True)) == [open_ticket.pk]

    def test_closed_returns_tickets_with_closed_at(self, open_ticket, closed_ticket):
        assert list(ClusterReviewTicket.objects.closed().values_list("pk", flat=True)) == [closed_ticket.pk]

    def test_open_excludes_closed_tickets(self, closed_ticket):
        assert ClusterReviewTicket.objects.open().count() == 0

    def test_closed_excludes_open_tickets(self, open_ticket):
        assert ClusterReviewTicket.objects.closed().count() == 0

    def test_close_method_sets_closed_at(self, open_ticket):
        assert open_ticket.closed_at is None

        open_ticket.close()
        open_ticket.refresh_from_db()

        assert open_ticket.closed_at is not None

    def test_close_moves_ticket_to_closed_queryset(self, open_ticket):
        open_ticket.close()

        assert ClusterReviewTicket.objects.open().count() == 0
        assert ClusterReviewTicket.objects.closed().count() == 1
