from __future__ import annotations

import pathlib
import uuid

import numpy as np
import pytest
from django.urls import reverse

from id_dedup.dedup.models import Identity
from id_dedup.dedup.pipeline import ClusterMember, ClusterResult
from id_dedup.dedup.services import ClusterProposal, IdentityMatch


def _result() -> ClusterResult:
    result = ClusterResult()
    result.clusters[0] = [
        ClusterMember(file=pathlib.Path("tests/person_a/photo_1.jpg"), embedding=np.zeros(512, dtype="float32")),
        ClusterMember(file=pathlib.Path("tests/person_a/photo_2.jpg"), embedding=np.zeros(512, dtype="float32")),
    ]
    result.clusters[1] = [
        ClusterMember(file=pathlib.Path("tests/person_b/photo_1.jpg"), embedding=np.zeros(512, dtype="float32")),
    ]
    result.clusters[-1] = [
        ClusterMember(file=pathlib.Path("tests/singleton_1.jpg"), embedding=np.zeros(512, dtype="float32")),
    ]
    return result


def _proposals(count: int = 3) -> list[ClusterProposal]:
    proposals = []
    for i in range(count):
        matches = [
            IdentityMatch(identity_id=uuid.uuid4(), display_name=f"Person {i}", similarity=0.85 - i * 0.1, matched_image_count=2),
        ]
        member = ClusterMember(
            file=pathlib.Path(f"tests/cluster_{i}/photo.jpg"),
            embedding=np.zeros(512, dtype="float32"),
        )
        proposals.append(ClusterProposal(
            members=[member],
            centroid=np.zeros(512, dtype="float32"),
            proposed_matches=matches,
        ))
    return proposals


def _setup_result(client, result: ClusterResult):
    """Store a serialized ClusterResult in the client session."""
    from id_dedup.dedup.views import _serialize_result
    session = client.session
    session["wizard_cluster_result"] = _serialize_result(result)
    session.save()


def _setup_proposals(client, proposals: list[ClusterProposal], adj_index: int = 0):
    """Store serialized proposals + index in the client session."""
    from id_dedup.dedup.views import _serialize_proposal
    session = client.session
    session["wizard_proposals"] = [_serialize_proposal(p) for p in proposals]
    session["wizard_adj_index"] = adj_index
    session.save()


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

class TestWizardUpload:
    def test_get_returns_200(self, client):
        resp = client.get(reverse("wizard:upload"))
        assert resp.status_code == 200
        assert b"Upload" in resp.content

    @pytest.mark.django_db(transaction=True)
    def test_post_without_files_stays_on_upload(self, client):
        resp = client.post(reverse("wizard:upload"), {})
        assert resp.status_code == 200
        assert b"Upload" in resp.content


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------

class TestWizardReview:
    @pytest.mark.django_db(transaction=True)
    def test_get_without_session_redirects_to_upload(self, client):
        resp = client.get(reverse("wizard:review"))
        assert resp.status_code == 302
        assert resp.url == reverse("wizard:upload")

    @pytest.mark.django_db(transaction=True)
    def test_get_with_session_renders_clusters(self, client):
        _setup_result(client, _result())
        resp = client.get(reverse("wizard:review"))
        assert resp.status_code == 200
        assert b"Review" in resp.content


# ---------------------------------------------------------------------------
# Split
# ---------------------------------------------------------------------------

class TestWizardSplit:
    @pytest.mark.django_db(transaction=True)
    def test_split_removes_file_from_source(self, client):
        result = _result()
        _setup_result(client, result)
        file_to_move = result.clusters[0][0].file

        client.post(
            reverse("wizard:split"),
            {"cluster_label": "0", "file": str(file_to_move)},
        )

        from id_dedup.dedup.views import _deserialize_result
        updated = _deserialize_result(client.session["wizard_cluster_result"])
        assert len(updated.clusters[0]) == 1
        assert updated.clusters[0][0].file == result.clusters[0][1].file

    @pytest.mark.django_db(transaction=True)
    def test_split_without_session_returns_400(self, client):
        resp = client.post(reverse("wizard:split"), {"cluster_label": "0", "file": "test.jpg"})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Review Save
# ---------------------------------------------------------------------------

class TestWizardReviewSave:
    @pytest.mark.django_db(transaction=True)
    def test_save_without_session_redirects_upload(self, client):
        resp = client.get(reverse("wizard:review_save"))
        assert resp.status_code == 302
        assert resp.url == reverse("wizard:upload")


# ---------------------------------------------------------------------------
# Adjudication
# ---------------------------------------------------------------------------

class TestWizardAdjudication:
    @pytest.mark.django_db(transaction=True)
    def test_get_without_session_redirects_upload(self, client):
        resp = client.get(reverse("wizard:adjudication"))
        assert resp.status_code == 302
        assert resp.url == reverse("wizard:upload")

    @pytest.mark.django_db(transaction=True)
    def test_get_shows_cluster(self, client):
        _setup_proposals(client, _proposals(3))
        resp = client.get(reverse("wizard:adjudication"))
        assert resp.status_code == 200
        assert b"Adjudication" in resp.content

    @pytest.mark.django_db(transaction=True)
    def test_completed_redirects_to_complete(self, client):
        _setup_proposals(client, _proposals(2), adj_index=2)
        resp = client.get(reverse("wizard:adjudication"))
        assert resp.status_code == 302
        assert resp.url == reverse("wizard:complete")

    @pytest.mark.django_db(transaction=True)
    def test_next_advances_index(self, client):
        _setup_proposals(client, _proposals(3), adj_index=0)
        resp = client.get(reverse("wizard:adjudication_next"))
        assert resp.status_code == 302
        assert resp.url == reverse("wizard:adjudication")
        assert client.session["wizard_adj_index"] == 1

    @pytest.mark.django_db(transaction=True)
    def test_next_past_last_redirects_complete(self, client):
        _setup_proposals(client, _proposals(2), adj_index=1)
        resp = client.get(reverse("wizard:adjudication_next"))
        assert resp.status_code == 302
        assert resp.url == reverse("wizard:complete")

    @pytest.mark.django_db(transaction=True)
    def test_prev_goes_back(self, client):
        _setup_proposals(client, _proposals(3), adj_index=2)
        client.get(reverse("wizard:adjudication_prev"))
        assert client.session["wizard_adj_index"] == 1

    @pytest.mark.django_db(transaction=True)
    def test_prev_at_zero_stays(self, client):
        _setup_proposals(client, _proposals(3), adj_index=0)
        client.get(reverse("wizard:adjudication_prev"))
        assert client.session["wizard_adj_index"] == 0

    @pytest.mark.django_db(transaction=True)
    def test_assign_sets_assignment_and_advances(self, client):
        proposals = _proposals(3)
        identity_id = proposals[0].proposed_matches[0].identity_id
        _setup_proposals(client, proposals, adj_index=0)

        resp = client.post(reverse("wizard:assign"), {"identity_id": str(identity_id)})
        assert resp.status_code == 302
        assert resp.url == reverse("wizard:adjudication")
        assert client.session["wizard_adj_index"] == 1
        assert "0" in client.session["wizard_assignments"]
        assert client.session["wizard_assignments"]["0"]["identity_id"] == str(identity_id)

    @pytest.mark.django_db(transaction=True)
    def test_assign_last_redirects_complete(self, client):
        proposals = _proposals(2)
        identity_id = proposals[1].proposed_matches[0].identity_id
        _setup_proposals(client, proposals, adj_index=1)

        resp = client.post(reverse("wizard:assign"), {"identity_id": str(identity_id)})
        assert resp.status_code == 302
        assert resp.url == reverse("wizard:complete")

    @pytest.mark.django_db(transaction=True)
    def test_new_identity_creates_and_advances(self, client):
        _setup_proposals(client, _proposals(3), adj_index=0)

        resp = client.post(reverse("wizard:new_identity"), {"display_name": "New Person"})
        assert resp.status_code == 302
        assert resp.url == reverse("wizard:adjudication")
        assert client.session["wizard_adj_index"] == 1

        identity = Identity.objects.get(display_name="New Person")
        assert client.session["wizard_assignments"]["0"]["identity_id"] == str(identity.pk)
        assert client.session["wizard_assignments"]["0"]["is_new"] is True

    @pytest.mark.django_db(transaction=True)
    def test_search_returns_results(self, client):
        Identity.objects.create(display_name="Alice")
        Identity.objects.create(display_name="Bob")
        Identity.objects.create(display_name="Albert")

        resp = client.get(reverse("wizard:search"), {"q": "Ali"})
        assert resp.status_code == 200
        content = resp.content.decode()
        assert "Alice" in content
        assert "Bob" not in content

    @pytest.mark.django_db(transaction=True)
    def test_search_empty_query(self, client):
        resp = client.get(reverse("wizard:search"), {"q": ""})
        assert resp.status_code == 200
        assert b"Type to search" in resp.content

    @pytest.mark.django_db(transaction=True)
    def test_search_no_results(self, client):
        resp = client.get(reverse("wizard:search"), {"q": "NonexistentXYZ"})
        assert resp.status_code == 200
        assert b"No identities found" in resp.content


# ---------------------------------------------------------------------------
# Complete
# ---------------------------------------------------------------------------

class TestWizardComplete:
    @pytest.mark.django_db(transaction=True)
    def test_complete_shows_summary(self, client):
        _setup_proposals(client, _proposals(4), adj_index=0)
        session = client.session
        session["wizard_assignments"] = {
            "0": {"identity_id": "1", "display_name": "Alice", "is_new": False},
            "1": {"identity_id": "2", "display_name": "Bob", "is_new": False},
            "2": {"identity_id": "3", "display_name": "New Person", "is_new": True},
        }
        session.save()

        resp = client.get(reverse("wizard:complete"))
        assert resp.status_code == 200
        assert b"Complete" in resp.content

    @pytest.mark.django_db(transaction=True)
    def test_complete_clears_session(self, client):
        _setup_proposals(client, _proposals(2), adj_index=0)
        session = client.session
        session["wizard_assignments"] = {"0": {"identity_id": "1", "display_name": "A", "is_new": False}}
        session.save()

        client.get(reverse("wizard:complete"))

        assert "wizard_proposals" not in client.session
        assert "wizard_assignments" not in client.session
        assert "wizard_adj_index" not in client.session
        assert "wizard_cluster_result" not in client.session
