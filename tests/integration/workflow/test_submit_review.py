import pathlib

import pytest
from django.contrib.auth.models import User
from django.core.files import File
from django.urls import reverse

from id_dedup.workflow.models import (
    Batch,
    ClusterReviewTicket,
    Conversation,
    Image,
    OutboxMessage,
    TicketAlreadyClosed,
    Trigger,
)
from id_dedup.workflow.service import submit_ticket_review

AUTO_ADJUDICATE_TASK = "id_dedup.workflow.tasks.auto_adjudicate_set"


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


def _upload_conversation(batch: Batch, user: User | None = None) -> Conversation:
    return Conversation.objects.create(
        trigger=Trigger.UPLOAD,
        user=user,
        summary={"batch_id": str(batch.pk)},
    )


def _review_conversation(ticket: ClusterReviewTicket) -> Conversation:
    return Conversation.objects.get(trigger=Trigger.CLUSTER_REVIEW, summary__ticket_id=str(ticket.pk))


@pytest.mark.django_db
class TestSubmitReview:
    def test_anonymous_redirected_to_login(self, client):
        ticket = ClusterReviewTicket.objects.create(batch=Batch.objects.create(), cluster_label=0)
        response = client.post(_url(pk=ticket.pk))
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def test_submit_with_no_kept(self, logged_in_client, tmp_path):
        batch = Batch.objects.create()
        ticket = ClusterReviewTicket.objects.create(batch=batch, cluster_label=0)
        _make_image(ticket, tmp_path, "a.jpg")
        _make_image(ticket, tmp_path, "b.jpg")

        response = logged_in_client.post(_url(pk=ticket.pk))

        ticket.refresh_from_db()
        assert ticket.is_closed
        assert response.status_code == 302
        assert response["Location"] == reverse("workflow:ticket_list")

        assert OutboxMessage.objects.count() == 0
        conv = _review_conversation(ticket)
        assert conv.summary["pending_image_ids"] == []
        assert conv.ended_at is not None

    def test_submit_keeps_selected_images(self, logged_in_client, tmp_path):
        batch = Batch.objects.create()
        ticket = ClusterReviewTicket.objects.create(batch=batch, cluster_label=0)
        upload = _upload_conversation(batch)
        kept = _make_image(ticket, tmp_path, "keep.jpg")
        _make_image(ticket, tmp_path, "discard.jpg")

        logged_in_client.post(_url(pk=ticket.pk), {"keep": [str(kept.pk)]})

        conv = _review_conversation(ticket)
        assert conv.summary["kept_count"] == 1
        assert conv.summary["discarded_count"] == 1
        assert conv.summary["kept_image_ids"] == [str(kept.pk)]
        assert conv.summary["pending_image_ids"] == [str(kept.pk)]
        assert conv.ended_at is None
        assert conv.parent == upload

    def test_submit_creates_single_outbox_message(self, logged_in_client, tmp_path):
        batch = Batch.objects.create()
        ticket = ClusterReviewTicket.objects.create(batch=batch, cluster_label=0)
        kept = _make_image(ticket, tmp_path, "keep.jpg")
        _make_image(ticket, tmp_path, "discard.jpg")

        logged_in_client.post(_url(pk=ticket.pk), {"keep": [str(kept.pk)]})

        messages = list(OutboxMessage.objects.all())
        assert len(messages) == 1
        assert messages[0].task_name == AUTO_ADJUDICATE_TASK
        conv = _review_conversation(ticket)
        assert messages[0].payload == {
            "image_ids": [str(kept.pk)],
            "conversation_id": str(conv.pk),
            "user_id": User.objects.get(username="testuser").pk,
        }

    def test_submit_drops_non_member_ids(self, logged_in_client, tmp_path):
        batch = Batch.objects.create()
        ticket = ClusterReviewTicket.objects.create(batch=batch, cluster_label=0)
        other_ticket = ClusterReviewTicket.objects.create(batch=batch, cluster_label=1)
        kept = _make_image(ticket, tmp_path, "keep.jpg")
        foreign = _make_image(other_ticket, tmp_path, "foreign.jpg")

        logged_in_client.post(_url(pk=ticket.pk), {"keep": [str(kept.pk), str(foreign.pk)]})

        msg = OutboxMessage.objects.get()
        assert msg.payload["image_ids"] == [str(kept.pk)]
        conv = _review_conversation(ticket)
        assert conv.summary["pending_image_ids"] == [str(kept.pk)]

    def test_submit_dedupes_kept_ids_order_preserving(self, logged_in_client, tmp_path):
        batch = Batch.objects.create()
        ticket = ClusterReviewTicket.objects.create(batch=batch, cluster_label=0)
        a = _make_image(ticket, tmp_path, "a.jpg")
        b = _make_image(ticket, tmp_path, "b.jpg")

        logged_in_client.post(_url(pk=ticket.pk), {"keep": [str(a.pk), str(b.pk), str(a.pk)]})

        msg = OutboxMessage.objects.get()
        assert msg.payload["image_ids"] == [str(a.pk), str(b.pk)]

    def test_double_submit_is_noop(self, logged_in_client, tmp_path):
        batch = Batch.objects.create()
        ticket = ClusterReviewTicket.objects.create(batch=batch, cluster_label=0)
        kept = _make_image(ticket, tmp_path, "keep.jpg")

        logged_in_client.post(_url(pk=ticket.pk), {"keep": [str(kept.pk)]})
        response = logged_in_client.post(_url(pk=ticket.pk), {"keep": [str(kept.pk)]}, follow=True)

        assert response.status_code == 200
        assert OutboxMessage.objects.count() == 1
        review_convs = Conversation.objects.filter(trigger=Trigger.CLUSTER_REVIEW, summary__ticket_id=str(ticket.pk))
        assert review_convs.count() == 1

    def test_submit_closes_ticket_with_reviewed_by(self, logged_in_client, tmp_path):
        batch = Batch.objects.create()
        ticket = ClusterReviewTicket.objects.create(batch=batch, cluster_label=0)
        _make_image(ticket, tmp_path, "a.jpg")

        logged_in_client.post(_url(pk=ticket.pk))

        ticket.refresh_from_db()
        assert ticket.reviewed_by == User.objects.get(username="testuser")

    def test_submit_closed_ticket_shows_message(self, logged_in_client, tmp_path):
        batch = Batch.objects.create()
        ticket = ClusterReviewTicket.objects.create(batch=batch, cluster_label=0)
        _make_image(ticket, tmp_path, "a.jpg")

        logged_in_client.post(_url(pk=ticket.pk))
        response = logged_in_client.post(_url(pk=ticket.pk), follow=True)

        assert response.status_code == 200
        assert "was already reviewed" in response.content.decode()

    def test_submit_creates_review_conversation(self, logged_in_client, tmp_path):
        batch = Batch.objects.create()
        ticket = ClusterReviewTicket.objects.create(batch=batch, cluster_label=0)
        upload = _upload_conversation(batch)
        kept = _make_image(ticket, tmp_path, "keep.jpg")

        logged_in_client.post(_url(pk=ticket.pk), {"keep": [str(kept.pk)]})

        conv = _review_conversation(ticket)
        assert conv.user == User.objects.get(username="testuser")
        assert conv.parent == upload
        assert conv.ended_at is None

    def test_submit_conversation_summary(self, logged_in_client, tmp_path):
        batch = Batch.objects.create()
        ticket = ClusterReviewTicket.objects.create(batch=batch, cluster_label=0)
        kept = _make_image(ticket, tmp_path, "keep.jpg")
        _make_image(ticket, tmp_path, "discard.jpg")

        logged_in_client.post(_url(pk=ticket.pk), {"keep": [str(kept.pk)]})

        conv = _review_conversation(ticket)
        assert conv.summary["kept_count"] == 1
        assert conv.summary["discarded_count"] == 1
        assert conv.summary["kept_image_ids"] == [str(kept.pk)]
        assert conv.summary["pending_image_ids"] == [str(kept.pk)]
        assert conv.summary["reviewed_by"] == "testuser"
        assert conv.summary["cluster_label"] == 0
        assert conv.summary["ticket_id"] == str(ticket.pk)

    def test_submit_all_images_kept_allowed(self, logged_in_client, tmp_path):
        batch = Batch.objects.create()
        ticket = ClusterReviewTicket.objects.create(batch=batch, cluster_label=0)
        _make_image(ticket, tmp_path, "a.jpg")
        _make_image(ticket, tmp_path, "b.jpg")

        all_ids = [str(i.pk) for i in Image.objects.filter(cluster_ticket=ticket)]
        logged_in_client.post(_url(pk=ticket.pk), {"keep": all_ids})

        ticket.refresh_from_db()
        assert ticket.is_closed
        conv = _review_conversation(ticket)
        assert conv.summary["kept_count"] == 2
        assert conv.summary["discarded_count"] == 0
        msg = OutboxMessage.objects.get()
        assert msg.payload["image_ids"] == all_ids

    def test_seeded_ticket_has_no_parent(self, logged_in_client, tmp_path):
        batch = Batch.objects.create()
        ticket = ClusterReviewTicket.objects.create(batch=batch, cluster_label=0)
        kept = _make_image(ticket, tmp_path, "keep.jpg")

        logged_in_client.post(_url(pk=ticket.pk), {"keep": [str(kept.pk)]})

        conv = _review_conversation(ticket)
        assert conv.parent is None
        assert OutboxMessage.objects.count() == 1

    def test_upload_conversation_untouched(self, logged_in_client, tmp_path):
        batch = Batch.objects.create()
        ticket = ClusterReviewTicket.objects.create(batch=batch, cluster_label=0)
        upload = _upload_conversation(batch, user=User.objects.get(username="testuser"))
        kept = _make_image(ticket, tmp_path, "keep.jpg")

        logged_in_client.post(_url(pk=ticket.pk), {"keep": [str(kept.pk)]})

        upload.refresh_from_db()
        assert upload.summary == {"batch_id": str(batch.pk)}
        assert upload.ended_at is None


@pytest.mark.django_db
class TestSubmitReviewService:
    def test_raises_when_ticket_already_closed(self, logged_in_client, tmp_path):
        batch = Batch.objects.create()
        ticket = ClusterReviewTicket.objects.create(batch=batch, cluster_label=0)
        _make_image(ticket, tmp_path, "a.jpg")
        ticket.close()

        with pytest.raises(TicketAlreadyClosed):
            submit_ticket_review(ticket)

    def test_raises_when_close_lost_race(self, monkeypatch):
        batch = Batch.objects.create()
        ticket = ClusterReviewTicket.objects.create(batch=batch, cluster_label=0)
        monkeypatch.setattr(ClusterReviewTicket, "close", lambda self, user=None: False)

        with pytest.raises(TicketAlreadyClosed):
            submit_ticket_review(ticket)

    def test_returns_normalized_kept_ids(self, tmp_path):
        batch = Batch.objects.create()
        ticket = ClusterReviewTicket.objects.create(batch=batch, cluster_label=0)
        a = _make_image(ticket, tmp_path, "a.jpg")
        b = _make_image(ticket, tmp_path, "b.jpg")

        returned = submit_ticket_review(ticket, kept_ids=[str(a.pk), str(b.pk), str(a.pk)])

        assert returned == [str(a.pk), str(b.pk)]
