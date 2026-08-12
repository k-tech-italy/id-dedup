import pytest
from django.urls import reverse
from model_bakery import baker

from id_dedup.workflow.models import Batch, ClusterReviewTicket


@pytest.mark.django_db
class TestTicketListView:
    def _url(self, status: str = "") -> str:
        base_url = reverse("workflow:ticket_list")
        query = f"?status={status}" if status else ""
        return f"{base_url}{query}"

    def _create_tickets(self, count: int) -> None:
        baker.make(ClusterReviewTicket, batch=baker.make(Batch), _quantity=count)

    def test_anonymous_user_redirected_to_login(self, client):
        response = client.get(self._url())
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def test_authenticated_user_gets_200(self, logged_in_client):
        response = logged_in_client.get(self._url())
        assert response.status_code == 200

    def test_default_status_is_open(self, logged_in_client):
        response = logged_in_client.get(self._url())
        assert response.context["status"] == "open"

    def test_status_closed_param(self, logged_in_client):
        response = logged_in_client.get(self._url(status="closed"))
        assert response.context["status"] == "closed"

    def test_status_all_param(self, logged_in_client):
        response = logged_in_client.get(self._url(status="all"))
        assert response.context["status"] == "all"

    def test_invalid_status_falls_back_to_open(self, logged_in_client):
        response = logged_in_client.get(self._url(status="invalid"))
        assert response.context["status"] == "open"

    def test_open_ticket_appears_in_open_tab(self, logged_in_client, cluster_review_ticket):
        response = logged_in_client.get(self._url(status="open"))
        assert cluster_review_ticket in response.context["tickets"]

    def test_open_ticket_absent_from_closed_tab(self, logged_in_client, cluster_review_ticket):
        response = logged_in_client.get(self._url(status="closed"))
        assert list(response.context["tickets"]) == []

    def test_closed_ticket_appears_in_closed_tab(self, logged_in_client, closed_cluster_review_ticket):
        response = logged_in_client.get(self._url(status="closed"))
        assert closed_cluster_review_ticket in response.context["tickets"]

    def test_closed_ticket_absent_from_open_tab(self, logged_in_client, closed_cluster_review_ticket):
        response = logged_in_client.get(self._url(status="open"))
        assert list(response.context["tickets"]) == []

    def test_open_and_closed_ticket_appear_in_all_tab(
        self,
        logged_in_client,
        cluster_review_ticket,
        closed_cluster_review_ticket,
    ):
        response = logged_in_client.get(self._url(status="all"))
        assert cluster_review_ticket in response.context["tickets"]
        assert closed_cluster_review_ticket in response.context["tickets"]

    def test_default_page_size_is_10(self, logged_in_client):
        response = logged_in_client.get(self._url())
        assert response.context["page_size"] == "10"

    def test_page_size_20_param(self, logged_in_client):
        response = logged_in_client.get(f"{self._url()}?page_size=20")
        assert response.context["page_size"] == "20"

    def test_invalid_page_size_falls_back_to_10(self, logged_in_client):
        response = logged_in_client.get(f"{self._url()}?page_size=50")
        assert response.context["page_size"] == "10"

    def test_first_page_contains_10_tickets(self, logged_in_client):
        self._create_tickets(15)
        response = logged_in_client.get(self._url())
        assert len(response.context["tickets"]) == 10
        assert response.context["page_obj"].number == 1
        assert response.context["page_obj"].paginator.num_pages == 2

    def test_page_size_20_fits_all_on_one_page(self, logged_in_client):
        self._create_tickets(15)
        response = logged_in_client.get(f"{self._url()}?page_size=20")
        assert len(response.context["tickets"]) == 15
        assert response.context["page_obj"].paginator.num_pages == 1

    def test_out_of_range_page_returns_last_page(self, logged_in_client):
        self._create_tickets(15)
        response = logged_in_client.get(f"{self._url()}?page=99")
        assert response.context["page_obj"].number == 2

    def test_non_numeric_page_returns_first_page(self, logged_in_client):
        self._create_tickets(15)
        response = logged_in_client.get(f"{self._url()}?page=abc")
        assert response.context["page_obj"].number == 1

    def test_elided_page_range_for_middle_page(self, logged_in_client):
        self._create_tickets(120)
        response = logged_in_client.get(f"{self._url()}?page=6")
        assert response.context["page_range"] == [1, "…", 5, 6, 7, "…", 12]

    def test_no_elision_when_few_pages(self, logged_in_client):
        self._create_tickets(15)
        response = logged_in_client.get(self._url())
        assert list(response.context["page_range"]) == [1, 2]
