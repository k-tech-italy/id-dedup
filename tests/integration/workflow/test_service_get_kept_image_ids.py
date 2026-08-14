import pytest

from id_dedup.workflow.models import Trigger
from id_dedup.workflow.service import get_kept_image_ids, submit_ticket_review

pytestmark = pytest.mark.django_db


@pytest.fixture
def linked_image_factory(image_factory, batch, cluster_review_ticket):
    """Create `Image`s linked to the same cluster review ticket."""

    def _make(name):
        return image_factory(batch=batch, cluster_ticket=cluster_review_ticket, source_image=f"images/{name}")

    return _make


@pytest.fixture
def closed_ticket(cluster_review_ticket, user):
    cluster_review_ticket.close(user=user)
    return cluster_review_ticket


@pytest.fixture
def review_conversation_without_kept_key(conversation_factory, cluster_review_ticket, user):
    return conversation_factory(
        trigger=Trigger.CLUSTER_REVIEW,
        user=user,
        summary={"ticket_id": str(cluster_review_ticket.pk)},
    )


@pytest.fixture
def reviewed_ticket_with_kept_image(linked_image_factory, cluster_review_ticket, user):
    image = linked_image_factory("kept.jpg")
    submit_ticket_review(cluster_review_ticket, user=user, kept_ids=[str(image.pk)])
    return image


@pytest.fixture
def reviewed_ticket_all_kept(linked_image_factory, cluster_review_ticket, user):
    img_a = linked_image_factory("a.jpg")
    img_b = linked_image_factory("b.jpg")
    submit_ticket_review(cluster_review_ticket, user=user, kept_ids=[str(img_a.pk), str(img_b.pk)])
    return img_a, img_b


@pytest.fixture
def reviewed_ticket_partial_kept(linked_image_factory, cluster_review_ticket, user):
    kept = linked_image_factory("a.jpg")
    linked_image_factory("b.jpg")
    submit_ticket_review(cluster_review_ticket, user=user, kept_ids=[str(kept.pk)])
    return kept


class TestGetKeptImageIds:
    def test_returns_empty_set_for_open_ticket(self, cluster_review_ticket):
        assert get_kept_image_ids(cluster_review_ticket) == set()

    def test_returns_empty_set_when_no_conversation(self, closed_ticket):
        assert get_kept_image_ids(closed_ticket) == set()

    def test_returns_empty_set_when_summary_missing_kept_key(self, closed_ticket, review_conversation_without_kept_key):
        assert get_kept_image_ids(closed_ticket) == set()

    def test_returns_kept_ids_for_closed_ticket(self, cluster_review_ticket, reviewed_ticket_with_kept_image):
        assert get_kept_image_ids(cluster_review_ticket) == {str(reviewed_ticket_with_kept_image.pk)}

    def test_returns_all_kept_ids_when_all_images_kept(self, cluster_review_ticket, reviewed_ticket_all_kept):
        img_a, img_b = reviewed_ticket_all_kept
        assert get_kept_image_ids(cluster_review_ticket) == {str(img_a.pk), str(img_b.pk)}

    def test_excludes_discarded_images(self, cluster_review_ticket, reviewed_ticket_partial_kept):
        assert get_kept_image_ids(cluster_review_ticket) == {str(reviewed_ticket_partial_kept.pk)}
