import pathlib

import pytest
from django.contrib.auth.models import User
from django.core.files import File
from django.urls import reverse

from id_dedup.workflow.models import Batch, ClusterReviewTicket, Conversation, Image, Trigger


def _url(pk: str) -> str:
    return reverse("workflow:submit_review", kwargs={"pk": pk})


def _make_image(ticket, tmp_path: pathlib.Path, name: str) -> Image:
    path = tmp_path / name
    path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16)
    with path.open("rb") as f:
        return Image.objects.create(
            batch=ticket.batch,
            cluster_ticket=ticket,
            source_image=File(f, name=name),
        )


@pytest.mark.django_db
class TestSubmitReview:
    def test_anonymous_redirected_to_login(self, client):
        ticket = ClusterReviewTicket.objects.create(batch=Batch.objects.create(), cluster_label=0)
        response = client.post(_url(pk=ticket.pk))
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def test_submit_with_no_discards(self, logged_in_client, tmp_path):
        batch = Batch.objects.create()
        ticket = ClusterReviewTicket.objects.create(batch=batch, cluster_label=0)
        _make_image(ticket, tmp_path, "a.jpg")
        _make_image(ticket, tmp_path, "b.jpg")

        response = logged_in_client.post(_url(pk=ticket.pk))

        ticket.refresh_from_db()
        assert ticket.is_closed
        assert response.status_code == 302
        assert response["Location"] == reverse("workflow:ticket_list")

    def test_submit_discards_selected_images(self, logged_in_client, tmp_path):

        batch = Batch.objects.create()
        ticket = ClusterReviewTicket.objects.create(batch=batch, cluster_label=0)
        keep = _make_image(ticket, tmp_path, "keep.jpg")
        discard = _make_image(ticket, tmp_path, "discard.jpg")

        logged_in_client.post(_url(pk=ticket.pk), {"discard": [str(discard.pk)]})

        keep.refresh_from_db()
        discard.refresh_from_db()
        assert keep.discarded is False
        assert discard.discarded is True

    def test_submit_closes_ticket_with_reviewed_by(self, logged_in_client, tmp_path):
        batch = Batch.objects.create()
        ticket = ClusterReviewTicket.objects.create(batch=batch, cluster_label=0)
        _make_image(ticket, tmp_path, "a.jpg")

        logged_in_client.post(_url(pk=ticket.pk))

        ticket.refresh_from_db()
        assert ticket.reviewed_by == User.objects.get(username="testuser")

    def test_submit_closed_ticket_returns_404(self, logged_in_client, tmp_path):
        batch = Batch.objects.create()
        ticket = ClusterReviewTicket.objects.create(batch=batch, cluster_label=0)
        _make_image(ticket, tmp_path, "a.jpg")

        logged_in_client.post(_url(pk=ticket.pk))
        response = logged_in_client.post(_url(pk=ticket.pk))

        assert response.status_code == 404

    def test_submit_creates_conversation(self, logged_in_client, tmp_path):
        batch = Batch.objects.create()
        ticket = ClusterReviewTicket.objects.create(batch=batch, cluster_label=0)
        _make_image(ticket, tmp_path, "a.jpg")

        logged_in_client.post(_url(pk=ticket.pk))

        conv = Conversation.objects.get(trigger=Trigger.CLUSTER_REVIEW, summary__ticket_id=str(ticket.pk))
        assert conv.user == User.objects.get(username="testuser")
        assert conv.ended_at is not None

    def test_submit_conversation_summary(self, logged_in_client, tmp_path):
        batch = Batch.objects.create()
        ticket = ClusterReviewTicket.objects.create(batch=batch, cluster_label=0)
        _make_image(ticket, tmp_path, "keep.jpg")
        discard = _make_image(ticket, tmp_path, "discard.jpg")

        logged_in_client.post(_url(pk=ticket.pk), {"discard": [str(discard.pk)]})

        conv = Conversation.objects.get(trigger=Trigger.CLUSTER_REVIEW, summary__ticket_id=str(ticket.pk))
        assert conv.summary["confirmed_count"] == 1
        assert conv.summary["discarded_count"] == 1
        assert conv.summary["discarded_image_ids"] == [str(discard.pk)]
        assert conv.summary["reviewed_by"] == "testuser"
        assert conv.summary["cluster_label"] == 0
        assert conv.summary["ticket_id"] == str(ticket.pk)

    def test_submit_all_images_discarded_allowed(self, logged_in_client, tmp_path):
        batch = Batch.objects.create()
        ticket = ClusterReviewTicket.objects.create(batch=batch, cluster_label=0)
        _make_image(ticket, tmp_path, "a.jpg")
        _make_image(ticket, tmp_path, "b.jpg")

        all_ids = [str(i.pk) for i in Image.objects.filter(cluster_ticket=ticket)]
        logged_in_client.post(_url(pk=ticket.pk), {"discard": all_ids})

        ticket.refresh_from_db()
        assert ticket.is_closed
        assert all(img.discarded for img in Image.objects.filter(cluster_ticket=ticket))

    def test_submit_404_for_closed_ticket(self, logged_in_client, tmp_path):
        batch = Batch.objects.create()
        ticket = ClusterReviewTicket.objects.create(batch=batch, cluster_label=0)
        ticket.close()

        response = logged_in_client.post(_url(pk=ticket.pk))
        assert response.status_code == 404