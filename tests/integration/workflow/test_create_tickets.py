import pathlib

import numpy as np
import pytest

from id_dedup.ml.pipeline import ClusterMember, ClusterResult
from id_dedup.workflow.models import ClusterReviewTicket, Image
from id_dedup.workflow.service import create_tickets_from_result


def _unit_vector(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.random(512).astype(np.float32)
    return v / np.linalg.norm(v)


def _make_image_file(tmp_path: pathlib.Path, name: str) -> pathlib.Path:
    p = tmp_path / name
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16)
    return p


@pytest.mark.django_db
class TestCreateTicketsFromResult:
    def test_two_groups_yields_two_tickets(self, tmp_path, batch):
        batch = batch()
        result = ClusterResult()
        result.clusters[0] = [
            ClusterMember(file=_make_image_file(tmp_path, "g0a.jpg"), embedding=_unit_vector(10)),
            ClusterMember(file=_make_image_file(tmp_path, "g0b.jpg"), embedding=_unit_vector(11)),
        ]
        result.clusters[1] = [
            ClusterMember(file=_make_image_file(tmp_path, "g1a.jpg"), embedding=_unit_vector(20)),
        ]
        result.clusters[-1] = [
            ClusterMember(file=_make_image_file(tmp_path, "s0.jpg"), embedding=_unit_vector(30)),
            ClusterMember(file=_make_image_file(tmp_path, "s1.jpg"), embedding=_unit_vector(31)),
        ]

        tickets = create_tickets_from_result(result, batch)

        assert len(tickets) == 2
        assert ClusterReviewTicket.objects.filter(batch=batch).count() == 2

    def test_singletons_produce_no_tickets(self, tmp_path, batch):
        batch = batch()
        result = ClusterResult()
        result.clusters[-1] = [
            ClusterMember(file=_make_image_file(tmp_path, "s0.jpg"), embedding=_unit_vector(30)),
            ClusterMember(file=_make_image_file(tmp_path, "s1.jpg"), embedding=_unit_vector(31)),
        ]

        tickets = create_tickets_from_result(result, batch)

        assert tickets == []
        assert ClusterReviewTicket.objects.filter(batch=batch).count() == 0

    def test_group_ticket_has_correct_cluster_label(self, tmp_path, batch):
        batch = batch()
        result = ClusterResult()
        result.clusters[0] = [
            ClusterMember(file=_make_image_file(tmp_path, "g0a.jpg"), embedding=_unit_vector(10)),
        ]

        tickets = create_tickets_from_result(result, batch)

        assert tickets[0].cluster_label == 0

    def test_images_created_for_group_members_only(self, tmp_path, batch):
        batch = batch()
        result = ClusterResult()
        result.clusters[0] = [
            ClusterMember(file=_make_image_file(tmp_path, "g0a.jpg"), embedding=_unit_vector(10)),
            ClusterMember(file=_make_image_file(tmp_path, "g0b.jpg"), embedding=_unit_vector(11)),
        ]
        result.clusters[-1] = [
            ClusterMember(file=_make_image_file(tmp_path, "s0.jpg"), embedding=_unit_vector(30)),
        ]

        create_tickets_from_result(result, batch)

        assert Image.objects.filter(batch=batch).count() == 2

    def test_images_linked_to_their_ticket(self, tmp_path, batch):
        batch = batch()
        result = ClusterResult()
        result.clusters[0] = [
            ClusterMember(file=_make_image_file(tmp_path, "g0a.jpg"), embedding=_unit_vector(10)),
            ClusterMember(file=_make_image_file(tmp_path, "g0b.jpg"), embedding=_unit_vector(11)),
        ]

        tickets = create_tickets_from_result(result, batch)

        assert Image.objects.filter(cluster_ticket=tickets[0]).count() == 2

    def test_empty_result_yields_no_tickets(self, batch):
        batch = batch()
        tickets = create_tickets_from_result(ClusterResult(), batch)

        assert tickets == []
        assert ClusterReviewTicket.objects.filter(batch=batch).count() == 0

    def test_tickets_scoped_to_given_batch(self, tmp_path, batch):
        result = ClusterResult()
        result.clusters[0] = [
            ClusterMember(file=_make_image_file(tmp_path, "g0a.jpg"), embedding=_unit_vector(10)),
        ]

        batch_a, batch_b = batch(_quantity=2)
        create_tickets_from_result(result, batch_a)

        assert ClusterReviewTicket.objects.filter(batch=batch_a).count() == 1
        assert ClusterReviewTicket.objects.filter(batch=batch_b).count() == 0

    def test_ticket_created_even_when_image_file_missing(self, tmp_path, batch):
        batch = batch()
        ghost = tmp_path / "ghost.jpg"  # intentionally not created on disk
        result = ClusterResult()
        result.clusters[0] = [
            ClusterMember(file=ghost, embedding=_unit_vector(99)),
        ]

        tickets = create_tickets_from_result(result, batch)

        assert len(tickets) == 1
        assert ClusterReviewTicket.objects.filter(batch=batch).count() == 1
        assert Image.objects.filter(batch=batch).count() == 0
