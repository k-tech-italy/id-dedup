import pytest
from django.urls import reverse

from id_dedup.workflow.models import (
    ClusterReviewTicket,
    Conversation,
    Image,
    OutboxMessage,
    TicketAlreadyClosed,
    Trigger,
)
from id_dedup.workflow.service import submit_ticket_review

pytestmark = pytest.mark.django_db

AUTO_ADJUDICATE_TASK = "id_dedup.workflow.tasks.auto_adjudicate_set"


def _url(pk: str) -> str:
    return reverse("workflow:submit_review", kwargs={"pk": pk})


def _submit_review(client, ticket, keep=None, follow=False):
    data = {"keep": keep} if keep else {}
    return client.post(_url(pk=ticket.pk), data, follow=follow)


def _review_conversation(ticket: ClusterReviewTicket) -> Conversation:
    return Conversation.objects.get(trigger=Trigger.CLUSTER_REVIEW, summary__ticket_id=str(ticket.pk))


@pytest.fixture
def linked_image(linked_image_factory, tmp_path):
    return linked_image_factory(tmp_path, "a.jpg")


@pytest.fixture
def upload_conversation(conversation_factory, cluster_review_ticket):
    return conversation_factory(trigger=Trigger.UPLOAD, summary={"batch_id": str(cluster_review_ticket.batch.pk)})


class TestSubmitReview:
    def test_anonymous_redirected_to_login(self, client, cluster_review_ticket):
        response = _submit_review(client, cluster_review_ticket)
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def test_submit_with_no_kept(self, logged_in_client, tmp_path, cluster_review_ticket, linked_image_factory):
        linked_image_factory(tmp_path, "a.jpg")
        linked_image_factory(tmp_path, "b.jpg")

        response = _submit_review(logged_in_client, cluster_review_ticket)

        cluster_review_ticket.refresh_from_db()
        assert cluster_review_ticket.is_closed
        assert response.status_code == 302
        assert response["Location"] == reverse("workflow:ticket_list")

        assert OutboxMessage.objects.count() == 0
        conv = _review_conversation(cluster_review_ticket)
        assert conv.summary["pending_image_ids"] == []
        assert conv.ended_at is not None

    def test_submit_keeps_selected_images(
        self,
        logged_in_client,
        tmp_path,
        linked_image_factory,
        cluster_review_ticket,
        upload_conversation,
    ):
        kept = linked_image_factory(tmp_path, "keep.jpg")
        linked_image_factory(tmp_path, "discard.jpg")

        _submit_review(logged_in_client, cluster_review_ticket, keep=[str(kept.pk)])

        conv = _review_conversation(cluster_review_ticket)
        assert conv.summary["kept_count"] == 1
        assert conv.summary["discarded_count"] == 1
        assert conv.summary["kept_image_ids"] == [str(kept.pk)]
        assert conv.summary["pending_image_ids"] == [str(kept.pk)]
        assert conv.ended_at is None
        assert conv.parent == upload_conversation

    def test_submit_creates_single_outbox_message(
        self,
        logged_in_client,
        user,
        tmp_path,
        cluster_review_ticket,
        linked_image_factory,
    ):
        kept = linked_image_factory(tmp_path, "keep.jpg")
        linked_image_factory(tmp_path, "discard.jpg")

        _submit_review(logged_in_client, cluster_review_ticket, keep=[str(kept.pk)])

        messages = list(OutboxMessage.objects.all())
        assert len(messages) == 1
        assert messages[0].task_name == AUTO_ADJUDICATE_TASK
        conv = _review_conversation(cluster_review_ticket)
        assert messages[0].payload == {
            "image_ids": [str(kept.pk)],
            "conversation_id": str(conv.pk),
            "user_id": user.pk,
        }

    @pytest.fixture
    def unrelated_ticket(self, cluster_review_ticket, cluster_review_ticket_factory):
        return cluster_review_ticket_factory(batch=cluster_review_ticket.batch, cluster_label=1)

    def test_submit_drops_non_member_ids(
        self,
        logged_in_client,
        tmp_path,
        cluster_review_ticket,
        unrelated_ticket,
        linked_image_factory,
    ):
        kept = linked_image_factory(tmp_path, "keep.jpg", ticket=cluster_review_ticket)
        foreign = linked_image_factory(tmp_path, "foreign.jpg", ticket=unrelated_ticket)

        _submit_review(logged_in_client, cluster_review_ticket, keep=[str(kept.pk), str(foreign.pk)])

        msg = OutboxMessage.objects.get()
        assert msg.payload["image_ids"] == [str(kept.pk)]
        conv = _review_conversation(cluster_review_ticket)
        assert conv.summary["pending_image_ids"] == [str(kept.pk)]

    def test_submit_dedupes_kept_ids_order_preserving(
        self,
        logged_in_client,
        tmp_path,
        cluster_review_ticket,
        linked_image_factory,
    ):
        a = linked_image_factory(tmp_path, "a.jpg")
        b = linked_image_factory(tmp_path, "b.jpg")

        _submit_review(logged_in_client, cluster_review_ticket, keep=[str(a.pk), str(b.pk), str(a.pk)])

        msg = OutboxMessage.objects.get()
        assert msg.payload["image_ids"] == [str(a.pk), str(b.pk)]

    @pytest.fixture
    def kept_linked_image(self, linked_image_factory, tmp_path):
        return linked_image_factory(tmp_path, "keep.jpg")

    def test_double_submit_is_noop(self, logged_in_client, tmp_path, cluster_review_ticket, kept_linked_image):
        _submit_review(logged_in_client, cluster_review_ticket, keep=[str(kept_linked_image.pk)])
        response = _submit_review(
            logged_in_client,
            cluster_review_ticket,
            keep=[str(kept_linked_image.pk)],
            follow=True,
        )

        assert response.status_code == 200
        assert OutboxMessage.objects.count() == 1
        review_convs = Conversation.objects.filter(
            trigger=Trigger.CLUSTER_REVIEW,
            summary__ticket_id=str(cluster_review_ticket.pk),
        )
        assert review_convs.count() == 1

    def test_submit_closes_ticket_with_reviewed_by(
        self,
        logged_in_client,
        user,
        tmp_path,
        cluster_review_ticket,
        linked_image,
    ):
        _submit_review(logged_in_client, cluster_review_ticket)

        cluster_review_ticket.refresh_from_db()
        assert cluster_review_ticket.reviewed_by == user

    def test_submit_closed_ticket_shows_message(
        self,
        logged_in_client,
        tmp_path,
        cluster_review_ticket,
        linked_image,
    ):
        _submit_review(logged_in_client, cluster_review_ticket)
        response = _submit_review(logged_in_client, cluster_review_ticket, follow=True)

        assert response.status_code == 200
        assert "was already reviewed" in response.content.decode()

    def test_submit_creates_review_conversation(
        self,
        logged_in_client,
        user,
        tmp_path,
        upload_conversation,
        cluster_review_ticket,
        kept_linked_image,
    ):
        _submit_review(logged_in_client, cluster_review_ticket, keep=[str(kept_linked_image.pk)])

        conv = _review_conversation(cluster_review_ticket)
        assert conv.user == user
        assert conv.parent == upload_conversation
        assert conv.ended_at is None

    @pytest.fixture
    def discarded_linked_image(self, linked_image_factory, tmp_path):
        return linked_image_factory(tmp_path, "discard.jpg")

    def test_submit_conversation_summary(
        self,
        logged_in_client,
        user,
        cluster_review_ticket,
        kept_linked_image,
        discarded_linked_image,
    ):

        _submit_review(logged_in_client, cluster_review_ticket, keep=[str(kept_linked_image.pk)])

        conv = _review_conversation(cluster_review_ticket)
        assert conv.summary["kept_count"] == 1
        assert conv.summary["discarded_count"] == 1
        assert conv.summary["kept_image_ids"] == [str(kept_linked_image.pk)]
        assert conv.summary["pending_image_ids"] == [str(kept_linked_image.pk)]
        assert conv.summary["reviewed_by"] == user.username
        assert conv.summary["cluster_label"] == 0
        assert conv.summary["ticket_id"] == str(cluster_review_ticket.pk)

    def test_submit_all_images_kept_allowed(
        self,
        logged_in_client,
        tmp_path,
        cluster_review_ticket,
        linked_image_factory,
    ):
        linked_image_factory(tmp_path, "a.jpg")
        linked_image_factory(tmp_path, "b.jpg")

        all_ids = [str(i.pk) for i in Image.objects.filter(cluster_ticket=cluster_review_ticket)]
        _submit_review(logged_in_client, cluster_review_ticket, keep=all_ids)

        cluster_review_ticket.refresh_from_db()
        assert cluster_review_ticket.is_closed
        conv = _review_conversation(cluster_review_ticket)
        assert conv.summary["kept_count"] == 2
        assert conv.summary["discarded_count"] == 0
        msg = OutboxMessage.objects.get()
        assert msg.payload["image_ids"] == all_ids

    def test_seeded_ticket_has_no_parent(self, logged_in_client, cluster_review_ticket, kept_linked_image):
        _submit_review(logged_in_client, cluster_review_ticket, keep=[str(kept_linked_image.pk)])

        conv = _review_conversation(cluster_review_ticket)
        assert conv.parent is None
        assert OutboxMessage.objects.count() == 1

    def test_upload_conversation_untouched(
        self,
        logged_in_client,
        cluster_review_ticket,
        upload_conversation,
        kept_linked_image,
    ):
        _submit_review(logged_in_client, cluster_review_ticket, keep=[str(kept_linked_image.pk)])

        upload_conversation.refresh_from_db()
        assert upload_conversation.summary == {"batch_id": str(cluster_review_ticket.batch.pk)}
        assert upload_conversation.ended_at is None


class TestSubmitReviewService:
    def test_raises_when_ticket_already_closed(self, cluster_review_ticket):
        cluster_review_ticket.close()

        with pytest.raises(TicketAlreadyClosed):
            submit_ticket_review(cluster_review_ticket)

    def test_raises_when_close_lost_race(self, monkeypatch, cluster_review_ticket):
        monkeypatch.setattr(ClusterReviewTicket, "close", lambda self, user=None: False)

        with pytest.raises(TicketAlreadyClosed):
            submit_ticket_review(cluster_review_ticket)

    def test_returns_normalized_kept_ids(self, tmp_path, cluster_review_ticket, linked_image_factory):
        a = linked_image_factory(tmp_path, "a.jpg")
        b = linked_image_factory(tmp_path, "b.jpg")

        returned = submit_ticket_review(cluster_review_ticket, kept_ids=[str(a.pk), str(b.pk), str(a.pk)])

        assert returned == [str(a.pk), str(b.pk)]
