from __future__ import annotations

import pathlib

import pytest

from id_dedup.dedup.pipeline import ClusterMember, ClusterResult
from id_dedup.dedup.services import IdentityMatch
from tests.unit.helpers import unit_vector


# ---------------------------------------------------------------------------
# Fixtures — services layer (synthetic data, no real images, no DB)
# ---------------------------------------------------------------------------

@pytest.fixture
def unit_member() -> ClusterMember:
    """A single ClusterMember with a unit-normalised embedding."""
    return ClusterMember(
        file=pathlib.Path("examples/person1/photo_1.jpg"),
        embedding=unit_vector(seed=0),
    )


@pytest.fixture
def two_member_group() -> list[ClusterMember]:
    """Two ClusterMembers for testing centroid computation."""
    return [
        ClusterMember(file=pathlib.Path("examples/person1/photo_1.jpg"), embedding=unit_vector(seed=0)),
        ClusterMember(file=pathlib.Path("examples/person1/photo_2.jpg"), embedding=unit_vector(seed=1)),
    ]


@pytest.fixture
def cluster_result_with_groups() -> ClusterResult:
    """A ClusterResult with two groups and one singleton for propose_matches tests."""
    result = ClusterResult()
    result.clusters[0] = [
        ClusterMember(file=pathlib.Path("g0_a.jpg"), embedding=unit_vector(seed=10)),
        ClusterMember(file=pathlib.Path("g0_b.jpg"), embedding=unit_vector(seed=11)),
    ]
    result.clusters[1] = [
        ClusterMember(file=pathlib.Path("g1_a.jpg"), embedding=unit_vector(seed=20)),
        ClusterMember(file=pathlib.Path("g1_b.jpg"), embedding=unit_vector(seed=21)),
    ]
    result.clusters[-1] = [
        ClusterMember(file=pathlib.Path("singleton.jpg"), embedding=unit_vector(seed=30)),
    ]
    return result


@pytest.fixture
def strong_and_weak_match() -> list[IdentityMatch]:
    """Strong (0.9) and weak (0.6) match — band filter should drop the weak one."""
    return [
        IdentityMatch(identity_id=1, display_name="Alice", similarity=0.9, matched_image_count=3),
        IdentityMatch(identity_id=2, display_name="Bob", similarity=0.6, matched_image_count=1),
    ]


@pytest.fixture
def close_matches() -> list[IdentityMatch]:
    """Two matches within 0.05 of each other — both should survive the band filter."""
    return [
        IdentityMatch(identity_id=1, display_name="Alice", similarity=0.88, matched_image_count=2),
        IdentityMatch(identity_id=2, display_name="Bob", similarity=0.85, matched_image_count=1),
    ]