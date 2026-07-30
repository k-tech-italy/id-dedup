import pytest
from django.contrib.auth.models import User

from id_dedup.workflow.models import Batch, ClusterReviewTicket, Conversation, Trigger
from id_dedup.workflow.service import get_kept_image_ids, submit_ticket_review


@pytest.mark.django_db
class TestGetKeptImageIds:
    def test_returns_empty_set_for_open_ticket(self):
        ticket = ClusterReviewTicket.objects.create(batch=Batch.objects.create(), cluster_label=0)
        assert get_kept_image_ids(ticket) == set()

    def test_returns_empty_set_when_no_conversation(self, logged_in_client):
        ticket = ClusterReviewTicket.objects.create(batch=Batch.objects.create(), cluster_label=0)
        ticket.close(user=User.objects.get(username="testuser"))
        assert get_kept_image_ids(ticket) == set()

    def test_returns_empty_set_when_summary_missing_kept_key(self, logged_in_client):
        ticket = ClusterReviewTicket.objects.create(batch=Batch.objects.create(), cluster_label=0)
        ticket.close(user=User.objects.get(username="testuser"))
        Conversation.objects.create(
            trigger=Trigger.CLUSTER_REVIEW,
            user=User.objects.get(username="testuser"),
            summary={"ticket_id": str(ticket.pk)},
        )
        assert get_kept_image_ids(ticket) == set()

    def test_returns_kept_ids_for_closed_ticket(self, logged_in_client, tmp_path):
        batch = Batch.objects.create()
        ticket = ClusterReviewTicket.objects.create(batch=batch, cluster_label=3)
        image = ticket.images.create(batch=batch)
        submit_ticket_review(ticket, user=User.objects.get(username="testuser"), kept_ids=[str(image.pk)])
        assert get_kept_image_ids(ticket) == {str(image.pk)}

    def test_returns_all_kept_ids_when_all_images_kept(self, logged_in_client):
        batch = Batch.objects.create()
        ticket = ClusterReviewTicket.objects.create(batch=batch, cluster_label=0)
        img1 = ticket.images.create(batch=batch)
        img2 = ticket.images.create(batch=batch)
        all_ids = [str(img1.pk), str(img2.pk)]
        submit_ticket_review(ticket, user=User.objects.get(username="testuser"), kept_ids=all_ids)
        assert get_kept_image_ids(ticket) == set(all_ids)

    def test_excludes_discarded_images(self, logged_in_client):
        batch = Batch.objects.create()
        ticket = ClusterReviewTicket.objects.create(batch=batch, cluster_label=0)
        kept = ticket.images.create(batch=batch)
        ticket.images.create(batch=batch)
        submit_ticket_review(ticket, user=User.objects.get(username="testuser"), kept_ids=[str(kept.pk)])
        assert get_kept_image_ids(ticket) == {str(kept.pk)}
