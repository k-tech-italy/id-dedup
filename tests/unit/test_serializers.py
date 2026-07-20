from __future__ import annotations

import pathlib
import uuid

import numpy as np
import pytest

from id_dedup.dedup.pipeline import ClusterMember, ClusterResult
from id_dedup.dedup.serializers import (
    deserialize_identity_match,
    deserialize_member,
    deserialize_proposal,
    deserialize_result,
    serialize_identity_match,
    serialize_member,
    serialize_proposal,
    serialize_result,
)
from id_dedup.dedup.service.proposals import ClusterProposal, IdentityMatch
from tests.unit.helpers import unit_vector


# ---------------------------------------------------------------------------
# ClusterMember round-trips
# ---------------------------------------------------------------------------


def test_member_round_trip_preserves_file():
    m = ClusterMember(file=pathlib.Path("person/a.jpg"), embedding=unit_vector(0))
    assert deserialize_member(serialize_member(m)).file == m.file


def test_member_round_trip_preserves_embedding():
    m = ClusterMember(file=pathlib.Path("a.jpg"), embedding=unit_vector(0))
    np.testing.assert_array_equal(deserialize_member(serialize_member(m)).embedding, m.embedding)


def test_member_embedding_is_writable():
    m = ClusterMember(file=pathlib.Path("a.jpg"), embedding=unit_vector(0))
    restored = deserialize_member(serialize_member(m))
    restored.embedding[0] = 0.0  # must not raise ValueError on read-only array


# ---------------------------------------------------------------------------
# ClusterResult round-trips
# ---------------------------------------------------------------------------


def test_result_round_trip_preserves_cluster_keys():
    result = ClusterResult()
    result.clusters[0] = [ClusterMember(file=pathlib.Path("a.jpg"), embedding=unit_vector(0))]
    result.clusters[1] = [ClusterMember(file=pathlib.Path("b.jpg"), embedding=unit_vector(1))]
    result.clusters[-1] = [ClusterMember(file=pathlib.Path("c.jpg"), embedding=unit_vector(2))]
    restored = deserialize_result(serialize_result(result))
    assert set(restored.clusters.keys()) == {0, 1, -1}


def test_result_round_trip_preserves_member_files():
    result = ClusterResult()
    result.clusters[0] = [
        ClusterMember(file=pathlib.Path("person/photo_1.jpg"), embedding=unit_vector(0)),
        ClusterMember(file=pathlib.Path("person/photo_2.jpg"), embedding=unit_vector(1)),
    ]
    restored = deserialize_result(serialize_result(result))
    assert [m.file for m in restored.clusters[0]] == [
        pathlib.Path("person/photo_1.jpg"),
        pathlib.Path("person/photo_2.jpg"),
    ]


def test_result_round_trip_preserves_embeddings():
    emb = unit_vector(42)
    result = ClusterResult()
    result.clusters[0] = [ClusterMember(file=pathlib.Path("a.jpg"), embedding=emb)]
    restored = deserialize_result(serialize_result(result))
    np.testing.assert_array_equal(restored.clusters[0][0].embedding, emb)


def test_result_round_trip_preserves_failed():
    result = ClusterResult()
    result.failed = [pathlib.Path("bad1.jpg"), pathlib.Path("bad2.jpg")]
    restored = deserialize_result(serialize_result(result))
    assert restored.failed == result.failed


def test_result_round_trip_empty():
    restored = deserialize_result(serialize_result(ClusterResult()))
    assert restored.clusters == {}
    assert restored.failed == []


# ---------------------------------------------------------------------------
# IdentityMatch round-trips
# ---------------------------------------------------------------------------


def test_identity_match_round_trip_preserves_all_fields():
    m = IdentityMatch(
        identity_id=uuid.UUID(int=1),
        display_name="Alice",
        similarity=0.87,
        matched_image_count=5,
        image_url="/media/alice.jpg",
    )
    restored = deserialize_identity_match(serialize_identity_match(m))
    assert restored.identity_id == m.identity_id
    assert restored.display_name == m.display_name
    assert restored.similarity == m.similarity
    assert restored.matched_image_count == m.matched_image_count
    assert restored.image_url == m.image_url


def test_identity_match_round_trip_null_image_url():
    m = IdentityMatch(
        identity_id=uuid.UUID(int=2),
        display_name="Bob",
        similarity=0.7,
        matched_image_count=1,
    )
    assert deserialize_identity_match(serialize_identity_match(m)).image_url is None


# ---------------------------------------------------------------------------
# ClusterProposal round-trips
# ---------------------------------------------------------------------------


def test_proposal_round_trip_preserves_member_files():
    member = ClusterMember(file=pathlib.Path("face.jpg"), embedding=unit_vector(0))
    proposal = ClusterProposal(members=[member], centroid=unit_vector(1), proposed_matches=[])
    restored = deserialize_proposal(serialize_proposal(proposal))
    assert restored.members[0].file == pathlib.Path("face.jpg")


def test_proposal_round_trip_preserves_member_embeddings():
    emb = unit_vector(10)
    member = ClusterMember(file=pathlib.Path("face.jpg"), embedding=emb)
    proposal = ClusterProposal(members=[member], centroid=unit_vector(1), proposed_matches=[])
    restored = deserialize_proposal(serialize_proposal(proposal))
    np.testing.assert_array_equal(restored.members[0].embedding, emb)


def test_proposal_round_trip_preserves_centroid():
    centroid = unit_vector(99)
    proposal = ClusterProposal(members=[], centroid=centroid, proposed_matches=[])
    restored = deserialize_proposal(serialize_proposal(proposal))
    np.testing.assert_array_equal(restored.centroid, centroid)


def test_proposal_centroid_is_writable():
    proposal = ClusterProposal(members=[], centroid=unit_vector(0), proposed_matches=[])
    restored = deserialize_proposal(serialize_proposal(proposal))
    restored.centroid[0] = 0.0  # must not raise ValueError on read-only array


def test_proposal_round_trip_preserves_matches():
    match = IdentityMatch(
        identity_id=uuid.UUID(int=3),
        display_name="Carol",
        similarity=0.9,
        matched_image_count=2,
    )
    proposal = ClusterProposal(members=[], centroid=unit_vector(0), proposed_matches=[match])
    restored = deserialize_proposal(serialize_proposal(proposal))
    assert len(restored.proposed_matches) == 1
    assert restored.proposed_matches[0].display_name == "Carol"