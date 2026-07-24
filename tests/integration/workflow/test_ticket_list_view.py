import pytest


@pytest.mark.django_db
class TestTicketListView:
    def test_anonymous_user_redirected_to_login(self, client):
        response = client.get("/workflow/tickets/")
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def test_authenticated_user_gets_200(self, client, django_user_model):
        client.force_login(django_user_model.objects.create_user(username="op", password="pass"))
        response = client.get("/workflow/tickets/")
        assert response.status_code == 200

    def test_default_status_is_open(self, client, django_user_model):
        client.force_login(django_user_model.objects.create_user(username="op", password="pass"))
        response = client.get("/workflow/tickets/")
        assert response.context["status"] == "open"

    def test_status_closed_param(self, client, django_user_model):
        client.force_login(django_user_model.objects.create_user(username="op", password="pass"))
        response = client.get("/workflow/tickets/?status=closed")
        assert response.context["status"] == "closed"

    def test_invalid_status_falls_back_to_open(self, client, django_user_model):
        client.force_login(django_user_model.objects.create_user(username="op", password="pass"))
        response = client.get("/workflow/tickets/?status=invalid")
        assert response.context["status"] == "open"

    def test_open_ticket_appears_in_open_tab(self, client, django_user_model):
        from id_dedup.workflow.models import Batch, ClusterReviewTicket

        client.force_login(django_user_model.objects.create_user(username="op", password="pass"))
        ticket = ClusterReviewTicket.objects.create(batch=Batch.objects.create(), cluster_label=0)
        response = client.get("/workflow/tickets/?status=open")
        assert ticket in response.context["tickets"]

    def test_open_ticket_absent_from_closed_tab(self, client, django_user_model):
        from id_dedup.workflow.models import Batch, ClusterReviewTicket

        client.force_login(django_user_model.objects.create_user(username="op", password="pass"))
        ClusterReviewTicket.objects.create(batch=Batch.objects.create(), cluster_label=0)
        response = client.get("/workflow/tickets/?status=closed")
        assert list(response.context["tickets"]) == []

    def test_closed_ticket_appears_in_closed_tab(self, client, django_user_model):
        from id_dedup.workflow.models import Batch, ClusterReviewTicket

        client.force_login(django_user_model.objects.create_user(username="op", password="pass"))
        ticket = ClusterReviewTicket.objects.create(batch=Batch.objects.create(), cluster_label=0)
        ticket.close()
        response = client.get("/workflow/tickets/?status=closed")
        assert ticket in response.context["tickets"]

    def test_closed_ticket_absent_from_open_tab(self, client, django_user_model):
        from id_dedup.workflow.models import Batch, ClusterReviewTicket

        client.force_login(django_user_model.objects.create_user(username="op", password="pass"))
        ticket = ClusterReviewTicket.objects.create(batch=Batch.objects.create(), cluster_label=0)
        ticket.close()
        response = client.get("/workflow/tickets/?status=open")
        assert list(response.context["tickets"]) == []