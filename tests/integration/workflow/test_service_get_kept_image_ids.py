import pytest
from django.contrib.auth.models import User
from model_bakery import baker

from id_dedup.workflow.models import Conversation, Image, Trigger
from id_dedup.workflow.service import get_kept_image_ids, submit_ticket_review


def _make_image(batch, ticket, name: str) -> Image:
    return baker.make(Image, batch=batch, cluster_ticket=ticket, source_image=f"images/{name}")


@pytest.mark.django_db
class TestGetKeptImageIds:
    def test_returns_empty_set_for_open_ticket(self, cluster_review_ticket):
        assert get_kept_image_ids(cluster_review_ticket) == set()

    def test_returns_empty_set_when_no_conversation(self, logged_in_client, cluster_review_ticket):
        cluster_review_ticket.close(user=User.objects.get(username="testuser"))
        assert get_kept_image_ids(cluster_review_ticket) == set()

    def test_returns_empty_set_when_summary_missing_kept_key(self, logged_in_client, cluster_review_ticket):
        cluster_review_ticket.close(user=User.objects.get(username="testuser"))
        baker.make(
            Conversation,
            trigger=Trigger.CLUSTER_REVIEW,
            user=User.objects.get(username="testuser"),
            summary={"ticket_id": str(cluster_review_ticket.pk)},
        )
        assert get_kept_image_ids(cluster_review_ticket) == set()

    def test_returns_kept_ids_for_closed_ticket(self, logged_in_client, batch, cluster_review_ticket):
        image = _make_image(batch, cluster_review_ticket, "kept.jpg")
        submit_ticket_review(
            cluster_review_ticket,
            user=User.objects.get(username="testuser"),
            kept_ids=[str(image.pk)],
        )
        assert get_kept_image_ids(cluster_review_ticket) == {str(image.pk)}

    def test_returns_all_kept_ids_when_all_images_kept(self, logged_in_client, batch, cluster_review_ticket):
        img1 = _make_image(batch, cluster_review_ticket, "a.jpg")
        img2 = _make_image(batch, cluster_review_ticket, "b.jpg")
        all_ids = [str(img1.pk), str(img2.pk)]
        submit_ticket_review(cluster_review_ticket, user=User.objects.get(username="testuser"), kept_ids=all_ids)
        assert get_kept_image_ids(cluster_review_ticket) == set(all_ids)

    def test_excludes_discarded_images(self, logged_in_client, batch, cluster_review_ticket):
        kept = _make_image(batch, cluster_review_ticket, "a.jpg")
        _make_image(batch, cluster_review_ticket, "b.jpg")
        submit_ticket_review(cluster_review_ticket, user=User.objects.get(username="testuser"), kept_ids=[str(kept.pk)])
        assert get_kept_image_ids(cluster_review_ticket) == {str(kept.pk)}
