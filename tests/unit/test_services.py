from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest

from id_dedup.dedup.services import (
    ClusterProposal,
    IdentityMatch,
    _query_candidates,
    propose_for_members,
    propose_matches,
)
from tests.unit.helpers import chainable_qs, mock_image_row


# ---------------------------------------------------------------------------
# ClusterProposal properties
# ---------------------------------------------------------------------------

def test_cluster_proposal_is_new_identity_when_no_matches():
    proposal = ClusterProposal(members=[], centroid=np.zeros(512), proposed_matches=[])
    assert proposal.is_new_identity is True


def test_cluster_proposal_is_not_new_identity_when_matches_exist():
    match = IdentityMatch(identity_id=1, display_name="Alice", similarity=0.85, matched_image_count=1)
    proposal = ClusterProposal(members=[], centroid=np.zeros(512), proposed_matches=[match])
    assert proposal.is_new_identity is False


def test_cluster_proposal_best_match_returns_first_entry():
    matches = [
        IdentityMatch(identity_id=1, display_name="Alice", similarity=0.9, matched_image_count=2),
        IdentityMatch(identity_id=2, display_name="Bob", similarity=0.8, matched_image_count=1),
    ]
    proposal = ClusterProposal(members=[], centroid=np.zeros(512), proposed_matches=matches)
    assert proposal.best_match is matches[0]


def test_cluster_proposal_best_match_is_none_when_no_matches():
    proposal = ClusterProposal(members=[], centroid=np.zeros(512), proposed_matches=[])
    assert proposal.best_match is None


# ---------------------------------------------------------------------------
# propose_for_members
# ---------------------------------------------------------------------------

@patch("id_dedup.dedup.services._query_candidates", return_value=[])
def test_propose_for_members_returns_proposal_with_correct_members(mock_query, unit_member):
    proposal = propose_for_members([unit_member])
    assert proposal.members == [unit_member]


@patch("id_dedup.dedup.services._query_candidates", return_value=[])
def test_propose_for_members_single_member_centroid_equals_embedding(mock_query, unit_member):
    proposal = propose_for_members([unit_member])
    np.testing.assert_array_equal(proposal.centroid, unit_member.embedding)


@patch("id_dedup.dedup.services._query_candidates", return_value=[])
def test_propose_for_members_multi_member_centroid_is_unit_vector(mock_query, two_member_group):
    proposal = propose_for_members(two_member_group)
    norm = float(np.linalg.norm(proposal.centroid))
    assert abs(norm - 1.0) < 1e-5


@patch("id_dedup.dedup.services._query_candidates", return_value=[])
def test_propose_for_members_passes_params_to_query(mock_query, unit_member):
    propose_for_members([unit_member], top_k=3, min_similarity=0.7, similarity_band=0.05)
    args = mock_query.call_args.args
    assert args[1] == 3      # top_k
    assert args[2] == 0.7    # min_similarity
    assert args[3] == 0.05   # similarity_band


@patch("id_dedup.dedup.services._query_candidates")
def test_propose_for_members_returns_matches_from_query(mock_query, unit_member, strong_and_weak_match):
    mock_query.return_value = strong_and_weak_match
    proposal = propose_for_members([unit_member])
    assert proposal.proposed_matches == strong_and_weak_match
    assert proposal.best_match is strong_and_weak_match[0]


# ---------------------------------------------------------------------------
# propose_matches
# ---------------------------------------------------------------------------

@patch("id_dedup.dedup.services._query_candidates", return_value=[])
def test_propose_matches_one_proposal_per_group(mock_query, cluster_result_with_groups):
    proposals = propose_matches(cluster_result_with_groups)
    group_proposals = [p for p in proposals if len(p.members) > 1]
    assert len(group_proposals) == 2


@patch("id_dedup.dedup.services._query_candidates", return_value=[])
def test_propose_matches_one_proposal_per_singleton(mock_query, cluster_result_with_groups):
    proposals = propose_matches(cluster_result_with_groups)
    singleton_proposals = [p for p in proposals if len(p.members) == 1]
    assert len(singleton_proposals) == 1


@patch("id_dedup.dedup.services._query_candidates", return_value=[])
def test_propose_matches_groups_precede_singletons(mock_query, cluster_result_with_groups):
    proposals = propose_matches(cluster_result_with_groups)
    # First two proposals should be the groups (2 members each)
    assert len(proposals[0].members) == 2
    assert len(proposals[1].members) == 2
    assert len(proposals[2].members) == 1


@patch("id_dedup.dedup.services._query_candidates", return_value=[])
def test_propose_matches_empty_result_returns_empty(mock_query):
    from id_dedup.dedup.pipeline import ClusterResult
    assert propose_matches(ClusterResult()) == []


# ---------------------------------------------------------------------------
# _query_candidates — ORM mocked, testing collapse + filtering logic
# ---------------------------------------------------------------------------

def test_query_candidates_returns_empty_when_no_db_images():
    centroid = np.ones(512, dtype=np.float32)
    centroid /= np.linalg.norm(centroid)
    with patch("id_dedup.dedup.services.Image") as MockImage:
        MockImage.objects.filter.return_value = chainable_qs([])
        result = _query_candidates(centroid, top_k=5, min_similarity=0.6, similarity_band=0.1)
    assert result == []


def test_query_candidates_collapses_multiple_images_of_same_identity():
    centroid = np.ones(512, dtype=np.float32)
    centroid /= np.linalg.norm(centroid)
    rows = [
        mock_image_row(identity_id=1, display_name="Alice", distance=0.1),
        mock_image_row(identity_id=1, display_name="Alice", distance=0.15),
        mock_image_row(identity_id=1, display_name="Alice", distance=0.2),
    ]
    with patch("id_dedup.dedup.services.Image") as MockImage:
        MockImage.objects.filter.return_value = chainable_qs(rows)
        result = _query_candidates(centroid, top_k=5, min_similarity=0.6, similarity_band=0.0)
    assert len(result) == 1


def test_query_candidates_keeps_best_similarity_per_identity():
    centroid = np.ones(512, dtype=np.float32)
    centroid /= np.linalg.norm(centroid)
    rows = [
        mock_image_row(identity_id=1, display_name="Alice", distance=0.1),   # sim=0.9 (best)
        mock_image_row(identity_id=1, display_name="Alice", distance=0.3),   # sim=0.7
    ]
    with patch("id_dedup.dedup.services.Image") as MockImage:
        MockImage.objects.filter.return_value = chainable_qs(rows)
        result = _query_candidates(centroid, top_k=5, min_similarity=0.6, similarity_band=0.0)
    assert abs(result[0].similarity - 0.9) < 1e-5


def test_query_candidates_counts_all_matching_images_per_identity():
    centroid = np.ones(512, dtype=np.float32)
    centroid /= np.linalg.norm(centroid)
    rows = [
        mock_image_row(identity_id=1, display_name="Alice", distance=0.1),
        mock_image_row(identity_id=1, display_name="Alice", distance=0.2),
        mock_image_row(identity_id=1, display_name="Alice", distance=0.3),
    ]
    with patch("id_dedup.dedup.services.Image") as MockImage:
        MockImage.objects.filter.return_value = chainable_qs(rows)
        result = _query_candidates(centroid, top_k=5, min_similarity=0.6, similarity_band=0.0)
    assert result[0].matched_image_count == 3


def test_query_candidates_similarity_band_drops_weak_alternatives():
    centroid = np.ones(512, dtype=np.float32)
    centroid /= np.linalg.norm(centroid)
    rows = [
        mock_image_row(identity_id=1, display_name="Alice", distance=0.1),   # sim=0.9
        mock_image_row(identity_id=2, display_name="Bob",   distance=0.4),   # sim=0.6
    ]
    with patch("id_dedup.dedup.services.Image") as MockImage:
        MockImage.objects.filter.return_value = chainable_qs(rows)
        # band=0.1 → threshold=0.8; Bob (0.6) should be dropped
        result = _query_candidates(centroid, top_k=5, min_similarity=0.5, similarity_band=0.1)
    assert len(result) == 1
    assert result[0].identity_id == 1


def test_query_candidates_similarity_band_keeps_competitive_alternatives():
    centroid = np.ones(512, dtype=np.float32)
    centroid /= np.linalg.norm(centroid)
    rows = [
        mock_image_row(identity_id=1, display_name="Alice", distance=0.12),  # sim≈0.88
        mock_image_row(identity_id=2, display_name="Bob",   distance=0.15),  # sim≈0.85
    ]
    with patch("id_dedup.dedup.services.Image") as MockImage:
        MockImage.objects.filter.return_value = chainable_qs(rows)
        # band=0.1 → both within 0.1 of best, both should survive
        result = _query_candidates(centroid, top_k=5, min_similarity=0.6, similarity_band=0.1)
    assert len(result) == 2


def test_query_candidates_respects_top_k():
    centroid = np.ones(512, dtype=np.float32)
    centroid /= np.linalg.norm(centroid)
    rows = [mock_image_row(identity_id=i, display_name=f"Person{i}", distance=0.05 * i)
            for i in range(1, 8)]
    with patch("id_dedup.dedup.services.Image") as MockImage:
        MockImage.objects.filter.return_value = chainable_qs(rows)
        result = _query_candidates(centroid, top_k=3, min_similarity=0.0, similarity_band=0.0)
    assert len(result) <= 3