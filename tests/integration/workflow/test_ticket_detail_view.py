import pathlib

import pytest
from django.core.files import File
from django.urls import reverse
from model_bakery import baker

from id_dedup.workflow.models import Image


def _make_image(ticket, tmp_path: pathlib.Path, name: str) -> Image:
    path = tmp_path / name
    path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16)
    with path.open("rb") as f:
        return baker.make(Image, batch=ticket.batch, cluster_ticket=ticket, source_image=File(f, name=name))


@pytest.mark.django_db
class TestTicketDetailView:
    def _url(self, pk: str) -> str:
        return reverse("workflow:ticket_detail", kwargs={"pk": pk})

    def test_anonymous_redirected_to_login(self, client, cluster_review_ticket):
        response = client.get(self._url(pk=cluster_review_ticket.pk))
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def test_authenticated_gets_200(self, logged_in_client, tmp_path, cluster_review_ticket):
        _make_image(cluster_review_ticket, tmp_path, "face.jpg")
        response = logged_in_client.get(self._url(pk=cluster_review_ticket.pk))
        assert response.status_code == 200
        assert cluster_review_ticket == response.context["ticket"]

    def test_images_in_context(self, logged_in_client, tmp_path, cluster_review_ticket):
        _make_image(cluster_review_ticket, tmp_path, "a.jpg")
        _make_image(cluster_review_ticket, tmp_path, "b.jpg")

        response = logged_in_client.get(self._url(pk=cluster_review_ticket.pk))
        assert list(response.context["ticket"].images.all()) == list(cluster_review_ticket.images.all())

    def test_404_for_nonexistent_ticket(self, logged_in_client):
        response = logged_in_client.get(self._url(pk="00000000-0000-0000-0000-000000000000"))
        assert response.status_code == 404

    def test_closed_ticket_renders_normally(self, logged_in_client, tmp_path, closed_cluster_review_ticket):
        _make_image(closed_cluster_review_ticket, tmp_path, "a.jpg")

        response = logged_in_client.get(self._url(pk=closed_cluster_review_ticket.pk))
        assert response.status_code == 200
