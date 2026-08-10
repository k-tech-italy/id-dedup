import pytest
from django.contrib.auth.models import User
from model_bakery import baker

from id_dedup.workflow.models import Conversation, Trigger
from id_dedup.workflow.service import get_kept_image_ids, submit_ticket_review


@pytest.fixture
def open_ticket(cluster_review_ticket):
    return cluster_review_ticket()


@pytest.mark.django_db
class TestGetKeptImageIds:
    def test_returns_empty_set_for_open_ticket(self, open_ticket):
        assert get_kept_image_ids(open_ticket) == set()

    def test_returns_empty_set_when_no_conversation(self, logged_in_client, open_ticket):
        open_ticket.close(user=User.objects.get(username="testuser"))
        assert get_kept_image_ids(open_ticket) == set()

    def test_returns_empty_set_when_summary_missing_kept_key(self, logged_in_client, open_ticket):
        open_ticket.close(user=User.objects.get(username="testuser"))
        baker.make(
            Conversation,
            trigger=Trigger.CLUSTER_REVIEW,
            user=User.objects.get(username="testuser"),
            summary={"ticket_id": str(open_ticket.pk)},
        )
        assert get_kept_image_ids(open_ticket) == set()

    def test_returns_kept_ids_for_closed_ticket(self, logged_in_client, tmp_path, batch, open_ticket):
        image = open_ticket.images.create(batch=batch())
        submit_ticket_review(open_ticket, user=User.objects.get(username="testuser"), kept_ids=[str(image.pk)])
        assert get_kept_image_ids(open_ticket) == {str(image.pk)}

    def test_returns_all_kept_ids_when_all_images_kept(self, logged_in_client, batch, open_ticket):
        img1 = open_ticket.images.create(batch=batch())
        img2 = open_ticket.images.create(batch=batch())
        all_ids = [str(img1.pk), str(img2.pk)]
        submit_ticket_review(open_ticket, user=User.objects.get(username="testuser"), kept_ids=all_ids)
        assert get_kept_image_ids(open_ticket) == set(all_ids)

    def test_excludes_discarded_images(self, logged_in_client, batch, open_ticket):
        kept = open_ticket.images.create(batch=batch())
        open_ticket.images.create(batch=batch())
        submit_ticket_review(open_ticket, user=User.objects.get(username="testuser"), kept_ids=[str(kept.pk)])
        assert get_kept_image_ids(open_ticket) == {str(kept.pk)}
