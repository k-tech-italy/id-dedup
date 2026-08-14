import pathlib
from datetime import timedelta

import numpy as np
import pytest
from django.core.files import File
from django.utils import timezone
from model_bakery import baker

from id_dedup.ml.pipeline import ClusterMember, ClusterResult
from id_dedup.workflow.models import Batch, ClusterReviewTicket, Image
from id_dedup.workflow.service import create_tickets_from_result


def _unit_vector(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.random(512).astype(np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture
def register_image(tmp_path, image_factory):
    def _make(batch, name, **kwargs):
        path = tmp_path / name
        path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16)
        with path.open("rb") as f:
            return image_factory(batch=batch, source_image=File(f, name=name), **kwargs)

    return _make


def _member_for(image: Image, seed: int) -> ClusterMember:
    return ClusterMember(file=pathlib.Path(image.source_image.path), embedding=_unit_vector(seed))


@pytest.mark.django_db
class TestCreateTicketsFromResult:
    def test_two_groups_yields_two_tickets(self, batch, register_image):
        g0a = register_image(batch, "g0a.jpg")
        g0b = register_image(batch, "g0b.jpg")
        g1a = register_image(batch, "g1a.jpg")

        result = ClusterResult()
        result.clusters[0] = [_member_for(g0a, 10), _member_for(g0b, 11)]
        result.clusters[1] = [_member_for(g1a, 20)]

        tickets = create_tickets_from_result(result, batch)

        assert len(tickets) == 2
        assert ClusterReviewTicket.objects.filter(batch=batch).count() == 2
        assert {t.cluster_label for t in tickets} == {0, 1}

    def test_links_registered_images_no_new_rows(self, batch, register_image):
        g0a = register_image(batch, "g0a.jpg")
        g0b = register_image(batch, "g0b.jpg")

        result = ClusterResult()
        result.clusters[0] = [_member_for(g0a, 10), _member_for(g0b, 11)]

        tickets = create_tickets_from_result(result, batch)

        assert Image.objects.count() == 2
        assert Image.objects.filter(cluster_ticket=tickets[0]).count() == 2

    def test_links_ticket_without_touching_embedding(self, batch, register_image):
        """Ticket linking is a graph edge only — embeddings are written earlier in the clustering commit."""
        img = register_image(batch, "g0a.jpg")
        member = _member_for(img, 10)

        result = ClusterResult()
        result.clusters[0] = [member]

        create_tickets_from_result(result, batch)

        img.refresh_from_db()
        assert img.cluster_ticket is not None
        assert img.embedding is None

    def test_updated_at_bumped(self, batch, register_image):
        img = register_image(batch, "g0a.jpg")
        Image.objects.filter(pk=img.pk).update(updated_at=timezone.now() - timedelta(days=1))
        stale = Image.objects.get(pk=img.pk).updated_at

        result = ClusterResult()
        result.clusters[0] = [_member_for(img, 10)]

        create_tickets_from_result(result, batch)

        img.refresh_from_db()
        assert img.updated_at > stale

    def test_singletons_produce_no_tickets_and_untouched(self, batch, register_image):
        s0 = register_image(batch, "s0.jpg")
        s1 = register_image(batch, "s1.jpg")

        result = ClusterResult()
        result.clusters[-1] = [_member_for(s0, 30), _member_for(s1, 31)]

        tickets = create_tickets_from_result(result, batch)

        assert tickets == []
        assert ClusterReviewTicket.objects.count() == 0
        for img in (s0, s1):
            img.refresh_from_db()
            assert img.cluster_ticket is None
            assert img.embedding is None

    def test_ungrouped_registered_image_untouched(self, batch, register_image):
        g0a = register_image(batch, "g0a.jpg")
        lone = register_image(batch, "lone.jpg")

        result = ClusterResult()
        result.clusters[0] = [_member_for(g0a, 10)]

        create_tickets_from_result(result, batch)

        lone.refresh_from_db()
        assert lone.cluster_ticket is None
        assert lone.embedding is None

    def test_unregistered_path_ticket_still_created(self, tmp_path, batch):
        ghost = tmp_path / "ghost.jpg"  # no matching Image row

        result = ClusterResult()
        result.clusters[0] = [ClusterMember(file=ghost, embedding=_unit_vector(99))]

        tickets = create_tickets_from_result(result, batch)

        assert len(tickets) == 1
        assert Image.objects.filter(cluster_ticket=tickets[0]).count() == 0

    def test_empty_result_yields_no_tickets(self, batch):
        tickets = create_tickets_from_result(ClusterResult(), batch)

        assert tickets == []
        assert ClusterReviewTicket.objects.count() == 0

    def test_tickets_scoped_to_given_batch(self, batch, register_image):
        img = register_image(batch, "g0a.jpg")
        other_batch = baker.make(Batch)

        result = ClusterResult()
        result.clusters[0] = [_member_for(img, 10)]

        create_tickets_from_result(result, batch)

        assert ClusterReviewTicket.objects.filter(batch=batch).count() == 1
        assert ClusterReviewTicket.objects.filter(batch=other_batch).count() == 0
