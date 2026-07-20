from __future__ import annotations

import pathlib
import uuid

import numpy as np
import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from id_dedup.dedup.models import Identity
from id_dedup.dedup.pipeline import ClusterMember, ClusterResult
from id_dedup.dedup.service.proposals import ClusterProposal, IdentityMatch

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logged_in_client(client):
    """Return a test client logged in as a test user."""
    User.objects.create_user(username="testuser", password="testpass123")
    client.login(username="testuser", password="testpass123")
    return client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
            IdentityMatch(
                identity_id=uuid.uuid4(),
                display_name=f"Person {i}",
                similarity=0.85 - i * 0.1,
                matched_image_count=2,
            ),
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
    from id_dedup.dedup.serializers import serialize_result
    session = client.session
    session["wizard_cluster_result"] = serialize_result(result)
    session.save()


def _setup_proposals(client, proposals: list[ClusterProposal], adj_index: int = 0):
    """Store serialized proposals + index in the client session."""
    from id_dedup.dedup.serializers import serialize_proposal
    session = client.session
    session["wizard_proposals"] = [serialize_proposal(p) for p in proposals]
    session["wizard_adj_index"] = adj_index
    session.save()


# ---------------------------------------------------------------------------
# Authentication — redirects
# ---------------------------------------------------------------------------


class TestAuthRequired:
    """Anonymous users are redirected to LOGIN_URL for all wizard views."""

    @pytest.mark.django_db(transaction=True)
    def test_upload_get_redirects(self, client):
        resp = client.get(reverse("wizard:upload"))
        assert resp.status_code == 302
        assert resp.url == f"{reverse('login')}?next={reverse('wizard:upload')}"

    @pytest.mark.django_db(transaction=True)
    def test_upload_post_redirects(self, client):
        resp = client.post(reverse("wizard:upload"), {})
        assert resp.status_code == 302
        assert "/accounts/login/" in resp.url

    @pytest.mark.django_db(transaction=True)
    def test_review_redirects(self, client):
        resp = client.get(reverse("wizard:review"))
        assert resp.status_code == 302
        assert "/accounts/login/" in resp.url

    @pytest.mark.django_db(transaction=True)
    def test_split_redirects(self, client):
        resp = client.post(reverse("wizard:split"), {"cluster_label": "0"})
        assert resp.status_code == 302
        assert "/accounts/login/" in resp.url

    @pytest.mark.django_db(transaction=True)
    def test_review_save_redirects(self, client):
        resp = client.post(reverse("wizard:review_save"))
        assert resp.status_code == 302
        assert "/accounts/login/" in resp.url

    @pytest.mark.django_db(transaction=True)
    def test_adjudication_redirects(self, client):
        resp = client.get(reverse("wizard:adjudication"))
        assert resp.status_code == 302
        assert "/accounts/login/" in resp.url

    @pytest.mark.django_db(transaction=True)
    def test_adjudication_next_redirects(self, client):
        resp = client.post(reverse("wizard:adjudication_next"))
        assert resp.status_code == 302
        assert "/accounts/login/" in resp.url

    @pytest.mark.django_db(transaction=True)
    def test_adjudication_prev_redirects(self, client):
        resp = client.post(reverse("wizard:adjudication_prev"))
        assert resp.status_code == 302
        assert "/accounts/login/" in resp.url

    @pytest.mark.django_db(transaction=True)
    def test_assign_redirects(self, client):
        resp = client.post(reverse("wizard:assign"))
        assert resp.status_code == 302
        assert "/accounts/login/" in resp.url

    @pytest.mark.django_db(transaction=True)
    def test_new_identity_redirects(self, client):
        resp = client.post(reverse("wizard:new_identity"))
        assert resp.status_code == 302
        assert "/accounts/login/" in resp.url

    @pytest.mark.django_db(transaction=True)
    def test_search_redirects(self, client):
        resp = client.get(reverse("wizard:search"), {"q": "test"})
        assert resp.status_code == 302
        assert "/accounts/login/" in resp.url

    @pytest.mark.django_db(transaction=True)
    def test_complete_redirects(self, client):
        resp = client.get(reverse("wizard:complete"))
        assert resp.status_code == 302
        assert "/accounts/login/" in resp.url


# ---------------------------------------------------------------------------
# Authentication — login / logout flow
# ---------------------------------------------------------------------------


class TestLoginLogout:
    @pytest.mark.django_db(transaction=True)
    def test_login_page_renders(self, client):
        resp = client.get(reverse("login"))
        assert resp.status_code == 200
        assert b"Sign in" in resp.content

    @pytest.mark.django_db(transaction=True)
    def test_login_invalid_credentials_shows_error(self, client):
        resp = client.post(reverse("login"), {"username": "nobody", "password": "wrong"})
        assert resp.status_code == 200
        assert b"Invalid username or password" in resp.content


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


class TestWizardUpload:
    @pytest.mark.django_db(transaction=True)
    def test_get_returns_200(self, logged_in_client):
        resp = logged_in_client.get(reverse("wizard:upload"))
        assert resp.status_code == 200
        assert resp.context["wizard_step"] == "upload"
        assert b"Upload" in resp.content

    @pytest.mark.django_db(transaction=True)
    def test_post_without_files_stays_on_upload(self, logged_in_client):
        resp = logged_in_client.post(reverse("wizard:upload"), {})
        assert resp.status_code == 200
        assert resp.context["wizard_step"] == "upload"
        assert b"Upload" in resp.content

    @pytest.mark.django_db(transaction=True)
    def test_post_with_non_image_rejects(self, logged_in_client):
        fake = SimpleUploadedFile("doc.pdf", b"%PDF-1.4 garbage", content_type="application/pdf")
        resp = logged_in_client.post(reverse("wizard:upload"), {"images": [fake]})
        assert resp.status_code == 200
        assert b"Unsupported" in resp.content

    @pytest.mark.django_db(transaction=True)
    def test_post_with_disguised_file_rejects(self, logged_in_client):
        # .jpg extension + image/jpeg content-type, but PDF magic bytes — server must catch it
        fake = SimpleUploadedFile("photo.jpg", b"%PDF-1.4 garbage", content_type="image/jpeg")
        resp = logged_in_client.post(reverse("wizard:upload"), {"images": [fake]})
        assert resp.status_code == 200
        assert b"Unsupported" in resp.content

    @pytest.mark.django_db(transaction=True)
    def test_post_with_valid_jpeg_redirects_to_review(self, logged_in_client):
        from unittest.mock import patch
        jpeg = SimpleUploadedFile("photo.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 100, content_type="image/jpeg")
        with patch("id_dedup.dedup.service.workflow.process_uploads", return_value=ClusterResult()):
            resp = logged_in_client.post(reverse("wizard:upload"), {"images": [jpeg]})
        assert resp.status_code == 302
        assert resp.url == reverse("wizard:review")


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------


class TestWizardReview:
    @pytest.mark.django_db(transaction=True)
    def test_get_without_session_redirects_to_upload(self, logged_in_client):
        resp = logged_in_client.get(reverse("wizard:review"))
        assert resp.status_code == 302
        assert resp.url == reverse("wizard:upload")

    @pytest.mark.django_db(transaction=True)
    def test_get_with_session_renders_clusters(self, logged_in_client):
        _setup_result(logged_in_client, _result())
        resp = logged_in_client.get(reverse("wizard:review"))
        assert resp.status_code == 200
        assert b"Review" in resp.content


# ---------------------------------------------------------------------------
# Split
# ---------------------------------------------------------------------------


class TestWizardSplit:
    @pytest.mark.django_db(transaction=True)
    def test_split_removes_file_from_source(self, logged_in_client):
        result = _result()
        _setup_result(logged_in_client, result)
        file_to_move = result.clusters[0][0].file

        logged_in_client.post(
            reverse("wizard:split"),
            {"cluster_label": "0", "file": str(file_to_move)},
        )

        from id_dedup.dedup.serializers import deserialize_result
        updated = deserialize_result(logged_in_client.session["wizard_cluster_result"])
        assert len(updated.clusters[0]) == 1
        assert updated.clusters[0][0].file == result.clusters[0][1].file

    @pytest.mark.django_db(transaction=True)
    def test_split_without_session_returns_400(self, logged_in_client):
        resp = logged_in_client.post(reverse("wizard:split"), {"cluster_label": "0", "file": "test.jpg"})
        assert resp.status_code == 400

    @pytest.mark.django_db(transaction=True)
    def test_split_filenames_list_payload_returns_200(self, client):
        import json
        result = _result()
        _setup_result(client, result)
        file_to_move = result.clusters[0][0].file
        resp = client.post(
            reverse("wizard:split"),
            {"cluster_label": "0", "files": json.dumps([file_to_move.name])},
        )
        assert resp.status_code == 200

    @pytest.mark.django_db(transaction=True)
    def test_split_to_cluster_payload_returns_200(self, client):
        result = _result()
        _setup_result(client, result)
        file_to_move = result.clusters[0][0].file
        resp = client.post(
            reverse("wizard:split"),
            {"cluster_label": "0", "file": str(file_to_move), "to_cluster": "1"},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Review Save
# ---------------------------------------------------------------------------


class TestWizardReviewSave:
    @pytest.mark.django_db(transaction=True)
    def test_save_requires_post(self, logged_in_client):
        resp = logged_in_client.get(reverse("wizard:review_save"))
        assert resp.status_code == 405

    @pytest.mark.django_db(transaction=True)
    def test_save_without_session_redirects_upload(self, logged_in_client):
        resp = logged_in_client.post(reverse("wizard:review_save"))
        assert resp.status_code == 302
        assert resp.url == reverse("wizard:upload")

    @pytest.mark.django_db(transaction=True)
    def test_save_removes_cluster_result_from_session(self, client):
        from unittest.mock import patch
        _setup_result(client, _result())
        with patch("id_dedup.dedup.views.proposals.propose_matches", return_value=[]):
            client.post(reverse("wizard:review_save"))
        assert "wizard_cluster_result" not in client.session

    @pytest.mark.django_db(transaction=True)
    def test_save_writes_proposals_to_session_and_redirects(self, client):
        from unittest.mock import patch
        fake_proposal = _proposals(1)[0]
        _setup_result(client, _result())
        with patch("id_dedup.dedup.views.proposals.propose_matches", return_value=[fake_proposal]):
            resp = client.post(reverse("wizard:review_save"))
        assert resp.status_code == 302
        assert resp.url == reverse("wizard:adjudication")
        assert len(client.session["wizard_proposals"]) == 1
        assert client.session["wizard_adj_index"] == 0
        assert client.session["wizard_assignments"] == {}


# ---------------------------------------------------------------------------
# Review Image
# ---------------------------------------------------------------------------


class TestWizardReviewImage:
    @pytest.mark.django_db(transaction=True)
    def test_serves_file_from_tmpdir(self, client, tmp_path):
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 10)
        session = client.session
        session["wizard_tmpdir"] = str(tmp_path)
        session.save()

        resp = client.get(reverse("wizard:review_image", kwargs={"path": "photo.jpg"}))
        assert resp.status_code == 200

    @pytest.mark.django_db(transaction=True)
    def test_no_session_returns_404(self, client):
        resp = client.get(reverse("wizard:review_image", kwargs={"path": "photo.jpg"}))
        assert resp.status_code == 404

    @pytest.mark.django_db(transaction=True)
    def test_missing_file_returns_404(self, client, tmp_path):
        session = client.session
        session["wizard_tmpdir"] = str(tmp_path)
        session.save()

        resp = client.get(reverse("wizard:review_image", kwargs={"path": "nonexistent.jpg"}))
        assert resp.status_code == 404

    @pytest.mark.django_db(transaction=True)
    def test_path_outside_tmpdir_returns_404(self, client, tmp_path):
        tmpdir = tmp_path / "uploads"
        tmpdir.mkdir()
        outside = tmp_path / "secret.txt"
        outside.write_text("sensitive")
        session = client.session
        session["wizard_tmpdir"] = str(tmpdir)
        session.save()

        resp = client.get("/wizard/review/image/../secret.txt")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Adjudication
# ---------------------------------------------------------------------------


class TestWizardAdjudication:
    @pytest.mark.django_db(transaction=True)
    def test_get_without_session_redirects_upload(self, logged_in_client):
        resp = logged_in_client.get(reverse("wizard:adjudication"))
        assert resp.status_code == 302
        assert resp.url == reverse("wizard:upload")

    @pytest.mark.django_db(transaction=True)
    def test_get_shows_cluster(self, logged_in_client):
        _setup_proposals(logged_in_client, _proposals(3))
        resp = logged_in_client.get(reverse("wizard:adjudication"))
        assert resp.status_code == 200
        assert b"Adjudication" in resp.content

    @pytest.mark.django_db(transaction=True)
    def test_completed_redirects_to_complete(self, logged_in_client):
        _setup_proposals(logged_in_client, _proposals(2), adj_index=2)
        resp = logged_in_client.get(reverse("wizard:adjudication"))
        assert resp.status_code == 302
        assert resp.url == reverse("wizard:complete")

    @pytest.mark.django_db(transaction=True)
    def test_next_requires_post(self, logged_in_client):
        resp = logged_in_client.get(reverse("wizard:adjudication_next"))
        assert resp.status_code == 405

    @pytest.mark.django_db(transaction=True)
    def test_prev_requires_post(self, logged_in_client):
        resp = logged_in_client.get(reverse("wizard:adjudication_prev"))
        assert resp.status_code == 405

    @pytest.mark.django_db(transaction=True)
    def test_next_assigned_advances(self, logged_in_client):
        proposals = _proposals(3)
        _setup_proposals(logged_in_client, proposals, adj_index=1)
        session = logged_in_client.session
        session["wizard_assignments"] = {
            "1": {
                "identity_id": str(proposals[1].proposed_matches[0].identity_id),
                "display_name": "Test",
                "is_new": False,
            },
        }
        session.save()
        resp = logged_in_client.post(reverse("wizard:adjudication_next"))
        assert resp.status_code == 302
        assert resp.url == reverse("wizard:adjudication")
        assert logged_in_client.session["wizard_adj_index"] == 2

    @pytest.mark.django_db(transaction=True)
    def test_next_unassigned_redirects_back(self, logged_in_client):
        _setup_proposals(logged_in_client, _proposals(3), adj_index=1)
        resp = logged_in_client.post(reverse("wizard:adjudication_next"))
        assert resp.status_code == 302
        assert resp.url == reverse("wizard:adjudication")
        assert logged_in_client.session["wizard_adj_index"] == 1

    @pytest.mark.django_db(transaction=True)
    def test_next_past_last_redirects_complete(self, logged_in_client):
        proposals = _proposals(2)
        _setup_proposals(logged_in_client, proposals, adj_index=1)
        session = logged_in_client.session
        session["wizard_assignments"] = {
            "1": {
                "identity_id": str(proposals[1].proposed_matches[0].identity_id),
                "display_name": "Test",
                "is_new": False,
            },
        }
        session.save()
        resp = logged_in_client.post(reverse("wizard:adjudication_next"))
        assert resp.status_code == 302
        assert resp.url == reverse("wizard:complete")
        # Verify _persist was called: summary stashed, wizard state cleared
        assert logged_in_client.session["wizard_summary"]["total_clusters"] == 2
        assert "wizard_assignments" not in logged_in_client.session

    @pytest.mark.django_db(transaction=True)
    def test_prev_goes_back(self, logged_in_client):
        _setup_proposals(logged_in_client, _proposals(3), adj_index=2)
        logged_in_client.post(reverse("wizard:adjudication_prev"))
        assert logged_in_client.session["wizard_adj_index"] == 1

    @pytest.mark.django_db(transaction=True)
    def test_prev_at_zero_stays(self, logged_in_client):
        _setup_proposals(logged_in_client, _proposals(3), adj_index=0)
        logged_in_client.post(reverse("wizard:adjudication_prev"))
        assert logged_in_client.session["wizard_adj_index"] == 0

    @pytest.mark.django_db(transaction=True)
    def test_assign_sets_assignment_and_advances(self, logged_in_client):
        proposals = _proposals(3)
        identity_id = proposals[0].proposed_matches[0].identity_id
        _setup_proposals(logged_in_client, proposals, adj_index=0)

        resp = logged_in_client.post(reverse("wizard:assign"), {"identity_id": str(identity_id)})
        assert resp.status_code == 302
        assert resp.url == reverse("wizard:adjudication")
        assert logged_in_client.session["wizard_adj_index"] == 1
        assert "0" in logged_in_client.session["wizard_assignments"]
        assert logged_in_client.session["wizard_assignments"]["0"]["identity_id"] == str(identity_id)

    @pytest.mark.django_db(transaction=True)
    def test_assign_last_redirects_complete(self, logged_in_client):
        proposals = _proposals(2)
        identity_id = proposals[1].proposed_matches[0].identity_id
        _setup_proposals(logged_in_client, proposals, adj_index=1)

        resp = logged_in_client.post(reverse("wizard:assign"), {"identity_id": str(identity_id)})
        assert resp.status_code == 302
        assert resp.url == reverse("wizard:complete")
        # End-of-wizard commit ran: wizard state cleared, summary stashed.
        assert "wizard_assignments" not in logged_in_client.session
        assert logged_in_client.session["wizard_summary"]["total_clusters"] == 2

    @pytest.mark.django_db(transaction=True)
    def test_assign_with_registry_identity(self, logged_in_client):
        """Assigning to a session-only identity resolves display_name from registry and is_new=True."""
        proposals = _proposals(3)
        _setup_proposals(logged_in_client, proposals, adj_index=0)

        registry_id = str(uuid.uuid4())
        session = logged_in_client.session
        session["wizard_new_identities"] = {registry_id: "Session Person"}
        session.save()

        resp = logged_in_client.post(reverse("wizard:assign"), {"identity_id": registry_id})
        assert resp.status_code == 302
        assert resp.url == reverse("wizard:adjudication")

        assignment = logged_in_client.session["wizard_assignments"]["0"]
        assert assignment["identity_id"] == registry_id
        assert assignment["display_name"] == "Session Person"
        assert assignment["is_new"] is True

    @pytest.mark.django_db(transaction=True)
    def test_assign_with_db_identity_fallback(self, logged_in_client):
        """Assigning to a DB identity not in proposed_matches resolves display_name via DB lookup."""
        proposals = _proposals(3)
        _setup_proposals(logged_in_client, proposals, adj_index=0)

        identity = Identity.objects.create(display_name="DB Person")

        resp = logged_in_client.post(reverse("wizard:assign"), {"identity_id": str(identity.pk)})
        assert resp.status_code == 302

        assignment = logged_in_client.session["wizard_assignments"]["0"]
        assert assignment["identity_id"] == str(identity.pk)
        assert assignment["display_name"] == "DB Person"
        assert assignment["is_new"] is False

    @pytest.mark.django_db(transaction=True)
    def test_assign_garbage_collects_previous_new_identity(self, logged_in_client):
        """Reassigning a cluster pops the old is_new identity from the registry."""
        proposals = _proposals(3)
        _setup_proposals(logged_in_client, proposals, adj_index=0)

        old_id = str(uuid.uuid4())
        session = logged_in_client.session
        session["wizard_assignments"] = {
            "0": {"identity_id": old_id, "display_name": "Old Person", "is_new": True},
        }
        session["wizard_new_identities"] = {old_id: "Old Person"}
        session.save()

        new_id = str(proposals[0].proposed_matches[0].identity_id)
        resp = logged_in_client.post(reverse("wizard:assign"), {"identity_id": new_id})
        assert resp.status_code == 302

        assert old_id not in logged_in_client.session["wizard_new_identities"]

    @pytest.mark.django_db(transaction=True)
    def test_assign_marks_existing_as_not_new(self, logged_in_client):
        """Assigning a proposed match that already exists in DB sets is_new=False."""
        proposals = _proposals(3)
        _setup_proposals(logged_in_client, proposals, adj_index=0)

        match = proposals[0].proposed_matches[0]
        Identity.objects.create(pk=match.identity_id, display_name=match.display_name)

        resp = logged_in_client.post(reverse("wizard:assign"), {"identity_id": str(match.identity_id)})
        assert resp.status_code == 302

        assert logged_in_client.session["wizard_assignments"]["0"]["is_new"] is False

    @pytest.mark.django_db(transaction=True)
    def test_new_identity_is_session_only(self, logged_in_client):
        """new_identity does not create a DB record — identity is stored in session registry."""
        _setup_proposals(logged_in_client, _proposals(3), adj_index=0)

        resp = logged_in_client.post(reverse("wizard:new_identity"), {"display_name": "New Person"})
        assert resp.status_code == 302
        assert resp.url == reverse("wizard:adjudication")
        assert logged_in_client.session["wizard_adj_index"] == 1

        assert not Identity.objects.filter(display_name="New Person").exists()

        assignment = logged_in_client.session["wizard_assignments"]["0"]
        assert assignment["is_new"] is True
        assert assignment["display_name"] == "New Person"
        assert assignment["identity_id"] in logged_in_client.session["wizard_new_identities"]

    @pytest.mark.django_db(transaction=True)
    def test_new_identity_garbage_collects_previous(self, logged_in_client):
        """Creating a new identity for a previously-assigned cluster cleans up old registry entry."""
        proposals = _proposals(3)
        _setup_proposals(logged_in_client, proposals, adj_index=0)

        old_id = str(uuid.uuid4())
        session = logged_in_client.session
        session["wizard_assignments"] = {
            "0": {"identity_id": old_id, "display_name": "Old Person", "is_new": True},
        }
        session["wizard_new_identities"] = {old_id: "Old Person"}
        session.save()

        resp = logged_in_client.post(reverse("wizard:new_identity"), {"display_name": "Replacement"})
        assert resp.status_code == 302

        assert old_id not in logged_in_client.session["wizard_new_identities"]

    @pytest.mark.django_db(transaction=True)
    def test_search_returns_results(self, logged_in_client):
        Identity.objects.create(display_name="Alice")
        Identity.objects.create(display_name="Bob")
        Identity.objects.create(display_name="Albert")

        resp = logged_in_client.get(reverse("wizard:search"), {"q": "Ali"})
        assert resp.status_code == 200
        content = resp.content.decode()
        assert "Alice" in content
        assert "Bob" not in content

    @pytest.mark.django_db(transaction=True)
    def test_search_empty_query(self, logged_in_client):
        resp = logged_in_client.get(reverse("wizard:search"), {"q": ""})
        assert resp.status_code == 200
        assert b"Type to search" in resp.content

    @pytest.mark.django_db(transaction=True)
    def test_search_no_results(self, logged_in_client):
        resp = logged_in_client.get(reverse("wizard:search"), {"q": "NonexistentXYZ"})
        assert resp.status_code == 200
        assert b"No identities found" in resp.content

    @pytest.mark.django_db(transaction=True)
    def test_search_escapes_html_in_display_name(self, logged_in_client):
        Identity.objects.create(display_name='<script>alert(1)</script> "x"')

        resp = logged_in_client.get(reverse("wizard:search"), {"q": "script"})
        content = resp.content.decode()
        assert "<script>" not in content
        assert "&lt;script&gt;" in content
        assert 'alert(1)' in content

    @pytest.mark.django_db(transaction=True)
    def test_search_includes_session_identities(self, logged_in_client):
        Identity.objects.create(display_name="Alice")

        session = logged_in_client.session
        session["wizard_new_identities"] = {
            str(uuid.uuid4()): "Session Person",
            str(uuid.uuid4()): "Another Session",
        }
        session.save()

        resp = logged_in_client.get(reverse("wizard:search"), {"q": "Session"})
        content = resp.content.decode()
        assert "Session Person" in content
        assert "Another Session" in content
        assert '(new)' in content

    @pytest.mark.django_db(transaction=True)
    def test_search_deduplicates_session_and_db(self, logged_in_client):
        Identity.objects.create(display_name="Alice")

        session = logged_in_client.session
        session["wizard_new_identities"] = {str(uuid.uuid4()): "Alice"}
        session.save()

        resp = logged_in_client.get(reverse("wizard:search"), {"q": "Ali"})
        content = resp.content.decode()
        assert content.count("Alice") == 1

    @pytest.mark.django_db(transaction=True)
    def test_search_still_shows_no_results_when_only_db_empty(self, logged_in_client):
        """If DB has no results and session registry has no matches, show no results."""
        session = logged_in_client.session
        session["wizard_new_identities"] = {str(uuid.uuid4()): "Alice"}
        session.save()

        resp = logged_in_client.get(reverse("wizard:search"), {"q": "NonexistentXYZ"})
        assert resp.status_code == 200
        assert b"No identities found" in resp.content


# ---------------------------------------------------------------------------
# Complete
# ---------------------------------------------------------------------------


class TestWizardComplete:
    @pytest.mark.django_db(transaction=True)
    def test_complete_requires_post(self, logged_in_client):
        resp = logged_in_client.post(reverse("wizard:complete"))
        assert resp.status_code == 405

    @pytest.mark.django_db(transaction=True)
    def test_complete_without_summary_redirects_upload(self, logged_in_client):
        resp = logged_in_client.get(reverse("wizard:complete"))
        assert resp.status_code == 302
        assert resp.url == reverse("wizard:upload")

    @pytest.mark.django_db(transaction=True)
    def test_complete_shows_summary(self, logged_in_client):
        session = logged_in_client.session
        session["wizard_summary"] = {
            "wizard_step": "complete",
            "total_clusters": 2,
            "assigned": 2,
            "new_identities": 1,
        }
        session.save()

        resp = logged_in_client.get(reverse("wizard:complete"))
        assert resp.status_code == 200
        assert b"Complete" in resp.content

    @pytest.mark.django_db(transaction=True)
    def test_complete_persists_new_identities(self, logged_in_client):
        """The end-of-wizard commit (in _persist) creates new Identity rows."""
        proposals = _proposals(1)
        _setup_proposals(logged_in_client, proposals, adj_index=0)

        resp = logged_in_client.post(reverse("wizard:new_identity"), {"display_name": "New Person"})
        assert resp.status_code == 302
        assert resp.url == reverse("wizard:complete")

        resp = logged_in_client.get(reverse("wizard:complete"))
        assert resp.status_code == 200
        assert Identity.objects.filter(display_name="New Person").exists()

    @pytest.mark.django_db(transaction=True)
    def test_complete_skips_deleted_identities(self, logged_in_client):
        """An is_new=False assignment whose identity was deleted is skipped, not created."""
        proposals = _proposals(2)
        _setup_proposals(logged_in_client, proposals, adj_index=1)

        deleted_id = str(uuid.uuid4())
        existing = Identity.objects.create(display_name="Existing")
        session = logged_in_client.session
        session["wizard_assignments"] = {
            "0": {"identity_id": deleted_id, "display_name": "Ghost", "is_new": False},
        }
        session.save()

        resp = logged_in_client.post(reverse("wizard:assign"), {"identity_id": str(existing.pk)})
        assert resp.status_code == 302
        assert resp.url == reverse("wizard:complete")

        resp = logged_in_client.get(reverse("wizard:complete"))
        assert resp.status_code == 200
        assert not Identity.objects.filter(pk=deleted_id).exists()

    @pytest.mark.django_db(transaction=True)
    def test_complete_clears_session(self, logged_in_client):
        proposals = _proposals(2)
        _setup_proposals(logged_in_client, proposals, adj_index=1)
        existing = Identity.objects.create(display_name="Alice")
        session = logged_in_client.session
        session["wizard_assignments"] = {
            "0": {"identity_id": str(existing.pk), "display_name": "Alice", "is_new": False},
        }
        session.save()

        logged_in_client.post(reverse("wizard:assign"), {"identity_id": str(existing.pk)})
        logged_in_client.get(reverse("wizard:complete"))

        assert "wizard_proposals" not in logged_in_client.session
        assert "wizard_assignments" not in logged_in_client.session
        assert "wizard_adj_index" not in logged_in_client.session
        assert "wizard_cluster_result" not in logged_in_client.session
        assert "wizard_summary" in logged_in_client.session
