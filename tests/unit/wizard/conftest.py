from __future__ import annotations

import pathlib
import uuid

import pytest
from django.contrib.auth.models import User

# ---------------------------------------------------------------------------
# Fixtures — services layer (synthetic data, no real images, no DB)
# ---------------------------------------------------------------------------


@pytest.fixture
def logged_in_client(client):
    """Return a test client logged in as a test user."""
    User.objects.create_user(username="testuser", password="testpass123")
    client.login(username="testuser", password="testpass123")
    return client


@pytest.fixture
def query_centroid():
    from tests.unit.wizard.helpers import unit_vector

    return unit_vector(seed=0)


@pytest.fixture
def unit_member():
    from id_dedup.dedup.pipeline import ClusterMember
    from tests.unit.wizard.helpers import unit_vector

    return ClusterMember(
        file=pathlib.Path("examples/person1/photo_1.jpg"),
        embedding=unit_vector(seed=0),
    )


@pytest.fixture
def two_member_group():
    from id_dedup.dedup.pipeline import ClusterMember
    from tests.unit.wizard.helpers import unit_vector

    return [
        ClusterMember(file=pathlib.Path("examples/person1/photo_1.jpg"), embedding=unit_vector(seed=0)),
        ClusterMember(file=pathlib.Path("examples/person1/photo_2.jpg"), embedding=unit_vector(seed=1)),
    ]


@pytest.fixture
def cluster_result_with_groups():
    from id_dedup.dedup.pipeline import ClusterMember, ClusterResult
    from tests.unit.wizard.helpers import unit_vector

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
def strong_and_weak_match():
    from id_dedup.dedup.service.proposals import IdentityMatch

    return [
        IdentityMatch(identity_id=uuid.UUID(int=1), display_name="Alice", similarity=0.9, matched_image_count=3),
        IdentityMatch(identity_id=uuid.UUID(int=2), display_name="Bob", similarity=0.6, matched_image_count=1),
    ]


@pytest.fixture
def close_matches():
    from id_dedup.dedup.service.proposals import IdentityMatch

    return [
        IdentityMatch(identity_id=uuid.UUID(int=1), display_name="Alice", similarity=0.88, matched_image_count=2),
        IdentityMatch(identity_id=uuid.UUID(int=2), display_name="Bob", similarity=0.85, matched_image_count=1),
    ]
