import pytest
from django.urls import reverse

from id_dedup.workflow.models import Batch, ClusterReviewTicket


@pytest.mark.django_db
class TestTicketListView:
    def _url(self, status: str = "") -> str:
        base_url = reverse("workflow:ticket_list")
        query = f"?status={status}" if status else ""
        return f"{base_url}{query}"

    def test_anonymous_user_redirected_to_login(self, client):
        response = client.get(self._url())
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def test_authenticated_user_gets_200(self, logged_in_client, django_user_model):
        response = logged_in_client.get(self._url())
        assert response.status_code == 200

    def test_default_status_is_open(self, logged_in_client, django_user_model):
        response = logged_in_client.get(self._url())
        assert response.context["status"] == "open"

    def test_status_closed_param(self, logged_in_client, django_user_model):
        response = logged_in_client.get(self._url(status="closed"))
        assert response.context["status"] == "closed"

    def test_invalid_status_falls_back_to_open(self, logged_in_client, django_user_model):
        response = logged_in_client.get(self._url(status="invalid"))
        assert response.context["status"] == "open"

    def test_open_ticket_appears_in_open_tab(self, logged_in_client, django_user_model):
        ticket = ClusterReviewTicket.objects.create(batch=Batch.objects.create(), cluster_label=0)
        response = logged_in_client.get(self._url(status="open"))
        assert ticket in response.context["tickets"]

    def test_open_ticket_absent_from_closed_tab(self, logged_in_client, django_user_model):
        ClusterReviewTicket.objects.create(batch=Batch.objects.create(), cluster_label=0)
        response = logged_in_client.get(self._url(status="closed"))
        assert list(response.context["tickets"]) == []

    def test_closed_ticket_appears_in_closed_tab(self, logged_in_client, django_user_model):
        ticket = ClusterReviewTicket.objects.create(batch=Batch.objects.create(), cluster_label=0)
        ticket.close()
        response = logged_in_client.get(self._url(status="closed"))
        assert ticket in response.context["tickets"]

    def test_closed_ticket_absent_from_open_tab(self, logged_in_client, django_user_model):
        ticket = ClusterReviewTicket.objects.create(batch=Batch.objects.create(), cluster_label=0)
        ticket.close()
        response = logged_in_client.get(self._url(status="open"))
        assert list(response.context["tickets"]) == []
