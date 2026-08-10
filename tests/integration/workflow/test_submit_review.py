import pathlib

import pytest
from django.contrib.auth.models import User
from django.core.files import File
from django.urls import reverse
from model_bakery import baker

from id_dedup.workflow.models import Conversation, Image, Trigger


@pytest.fixture
def open_ticket(cluster_review_ticket):
    return cluster_review_ticket()


def _url(pk: str) -> str:
    return reverse("workflow:submit_review", kwargs={"pk": pk})


def _make_image(ticket, tmp_path: pathlib.Path, name: str) -> Image:
    path = tmp_path / name
    path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16)
    with path.open("rb") as f:
        return baker.make(
            Image,
            batch=ticket.batch,
            cluster_ticket=ticket,
            source_image=File(f, name=name),
            embedding=None,
        )


@pytest.mark.django_db
class TestSubmitReview:
    def test_anonymous_redirected_to_login(self, client, open_ticket):
        response = client.post(_url(pk=open_ticket.pk))
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def test_submit_with_no_kept(self, logged_in_client, tmp_path, open_ticket):
        _make_image(open_ticket, tmp_path, "a.jpg")
        _make_image(open_ticket, tmp_path, "b.jpg")

        response = logged_in_client.post(_url(pk=open_ticket.pk))

        open_ticket.refresh_from_db()
        assert open_ticket.is_closed
        assert response.status_code == 302
        assert response["Location"] == reverse("workflow:ticket_list")

    def test_submit_keeps_selected_images(self, logged_in_client, tmp_path, open_ticket):
        kept = _make_image(open_ticket, tmp_path, "keep.jpg")
        _make_image(open_ticket, tmp_path, "discard.jpg")

        logged_in_client.post(_url(pk=open_ticket.pk), {"keep": [str(kept.pk)]})

        conv = Conversation.objects.get(trigger=Trigger.CLUSTER_REVIEW, summary__ticket_id=str(open_ticket.pk))
        assert conv.summary["kept_count"] == 1
        assert conv.summary["discarded_count"] == 1
        assert conv.summary["kept_image_ids"] == [str(kept.pk)]

    def test_submit_closes_ticket_with_reviewed_by(self, logged_in_client, tmp_path, open_ticket):
        _make_image(open_ticket, tmp_path, "a.jpg")

        logged_in_client.post(_url(pk=open_ticket.pk))

        open_ticket.refresh_from_db()
        assert open_ticket.reviewed_by == User.objects.get(username="testuser")

    def test_submit_closed_ticket_shows_message(self, logged_in_client, tmp_path, open_ticket):
        _make_image(open_ticket, tmp_path, "a.jpg")

        logged_in_client.post(_url(pk=open_ticket.pk))
        response = logged_in_client.post(_url(pk=open_ticket.pk), follow=True)

        assert response.status_code == 200
        assert "was already reviewed" in response.content.decode()

    def test_submit_creates_conversation(self, logged_in_client, tmp_path, open_ticket):
        _make_image(open_ticket, tmp_path, "a.jpg")

        logged_in_client.post(_url(pk=open_ticket.pk))

        conv = Conversation.objects.get(trigger=Trigger.CLUSTER_REVIEW, summary__ticket_id=str(open_ticket.pk))
        assert conv.user == User.objects.get(username="testuser")
        assert conv.ended_at is not None

    def test_submit_conversation_summary(self, logged_in_client, tmp_path, open_ticket):
        kept = _make_image(open_ticket, tmp_path, "keep.jpg")
        _make_image(open_ticket, tmp_path, "discard.jpg")

        logged_in_client.post(_url(pk=open_ticket.pk), {"keep": [str(kept.pk)]})

        conv = Conversation.objects.get(trigger=Trigger.CLUSTER_REVIEW, summary__ticket_id=str(open_ticket.pk))
        assert conv.summary["kept_count"] == 1
        assert conv.summary["discarded_count"] == 1
        assert conv.summary["kept_image_ids"] == [str(kept.pk)]
        assert conv.summary["reviewed_by"] == "testuser"
        assert conv.summary["cluster_label"] == 0
        assert conv.summary["ticket_id"] == str(open_ticket.pk)

    def test_submit_all_images_kept_allowed(self, logged_in_client, tmp_path, open_ticket):
        _make_image(open_ticket, tmp_path, "a.jpg")
        _make_image(open_ticket, tmp_path, "b.jpg")

        all_ids = [str(i.pk) for i in Image.objects.filter(cluster_ticket=open_ticket)]
        logged_in_client.post(_url(pk=open_ticket.pk), {"keep": all_ids})

        open_ticket.refresh_from_db()
        assert open_ticket.is_closed
        conv = Conversation.objects.get(trigger=Trigger.CLUSTER_REVIEW, summary__ticket_id=str(open_ticket.pk))
        assert conv.summary["kept_count"] == 2
        assert conv.summary["discarded_count"] == 0
