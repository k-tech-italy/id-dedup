import pytest
from django.urls import reverse


@pytest.fixture
def linked_image(linked_image_factory, tmp_path):
    return linked_image_factory(tmp_path, "a.jpg")


@pytest.fixture
def linked_images(linked_image_factory, tmp_path):
    return [
        linked_image_factory(tmp_path, "a.jpg"),
        linked_image_factory(tmp_path, "b.jpg"),
    ]


@pytest.fixture
def closed_ticket_image(linked_image_factory, tmp_path, closed_cluster_review_ticket):
    return linked_image_factory(tmp_path, "a.jpg", ticket=closed_cluster_review_ticket)


@pytest.mark.django_db
class TestTicketDetailView:
    def _url(self, pk: str) -> str:
        return reverse("workflow:ticket_detail", kwargs={"pk": pk})

    def test_anonymous_redirected_to_login(self, client, cluster_review_ticket):
        response = client.get(self._url(pk=cluster_review_ticket.pk))
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def test_authenticated_gets_200(self, logged_in_client, cluster_review_ticket, linked_image):
        response = logged_in_client.get(self._url(pk=cluster_review_ticket.pk))
        assert response.status_code == 200
        assert cluster_review_ticket == response.context["ticket"]

    def test_images_in_context(self, logged_in_client, cluster_review_ticket, linked_images):
        response = logged_in_client.get(self._url(pk=cluster_review_ticket.pk))
        assert list(response.context["ticket"].images.all()) == list(cluster_review_ticket.images.all())

    def test_404_for_nonexistent_ticket(self, logged_in_client):
        response = logged_in_client.get(self._url(pk="00000000-0000-0000-0000-000000000000"))
        assert response.status_code == 404

    def test_closed_ticket_renders_normally(self, logged_in_client, closed_cluster_review_ticket, closed_ticket_image):
        response = logged_in_client.get(self._url(pk=closed_cluster_review_ticket.pk))
        assert response.status_code == 200
