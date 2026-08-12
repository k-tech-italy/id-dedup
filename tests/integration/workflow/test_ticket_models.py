import pytest

from id_dedup.workflow.models import ClusterReviewTicket


@pytest.mark.django_db
class TestClusterReviewTicketQuerySet:
    def test_open_returns_tickets_without_closed_at(self, cluster_review_ticket, closed_cluster_review_ticket):
        assert list(ClusterReviewTicket.objects.open().values_list("pk", flat=True)) == [cluster_review_ticket.pk]

    def test_closed_returns_tickets_with_closed_at(self, cluster_review_ticket, closed_cluster_review_ticket):
        assert list(ClusterReviewTicket.objects.closed().values_list("pk", flat=True)) == [
            closed_cluster_review_ticket.pk,
        ]

    def test_open_excludes_closed_tickets(self, closed_cluster_review_ticket):
        assert ClusterReviewTicket.objects.open().count() == 0

    def test_closed_excludes_open_tickets(self, cluster_review_ticket):
        assert ClusterReviewTicket.objects.closed().count() == 0

    def test_close_method_sets_closed_at(self, cluster_review_ticket):
        assert cluster_review_ticket.closed_at is None

        cluster_review_ticket.close()
        cluster_review_ticket.refresh_from_db()

        assert cluster_review_ticket.closed_at is not None

    def test_close_moves_ticket_to_closed_queryset(self, cluster_review_ticket):
        cluster_review_ticket.close()

        assert ClusterReviewTicket.objects.open().count() == 0
        assert ClusterReviewTicket.objects.closed().count() == 1

    def test_close_returns_true_when_claiming_ticket(self, cluster_review_ticket):
        assert cluster_review_ticket.close() is True

    def test_close_returns_false_when_already_closed(self, cluster_review_ticket):
        cluster_review_ticket.close()
        cluster_review_ticket.refresh_from_db()

        assert cluster_review_ticket.close() is False
