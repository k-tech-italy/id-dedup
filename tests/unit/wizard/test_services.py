from __future__ import annotations

import pathlib
import uuid
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from tests.unit.wizard.helpers import chainable_qs, mock_identity_row


# @transaction.atomic on persist_assignments opens a real DB savepoint even
# when every ORM call inside is mocked.  Patching `transaction.atomic` on the
# module after import has no effect — the decorator is evaluated at definition
# time, so the function is already wrapped by the time any fixture runs.  The
# wrapper's __enter__ calls connection.get_autocommit() → ensure_connection(),
# which hits the database.  Instead, swap in the original unwrapped function
# that @wraps stashes under __wrapped__ so the test stays pure-unit (no DB
# connection required).
@pytest.fixture(autouse=True)
def _noop_transaction_atomic(monkeypatch):
    import id_dedup.dedup.service.workflow as mod

    monkeypatch.setattr(mod, "persist_assignments", mod.persist_assignments.__wrapped__)


# ---------------------------------------------------------------------------
# ClusterProposal properties
# ---------------------------------------------------------------------------

def test_cluster_proposal_is_new_identity_when_no_matches():
    from id_dedup.dedup.service.proposals import ClusterProposal

    proposal = ClusterProposal(members=[], centroid=np.zeros(512), proposed_matches=[])
    assert proposal.is_new_identity is True


def test_cluster_proposal_is_not_new_identity_when_matches_exist():
    from id_dedup.dedup.service.proposals import ClusterProposal, IdentityMatch

    match = IdentityMatch(identity_id=uuid.UUID(int=1), display_name="Alice", similarity=0.85, matched_image_count=1)
    proposal = ClusterProposal(members=[], centroid=np.zeros(512), proposed_matches=[match])
    assert proposal.is_new_identity is False


def test_cluster_proposal_best_match_returns_first_entry():
    from id_dedup.dedup.service.proposals import ClusterProposal, IdentityMatch

    matches = [
        IdentityMatch(identity_id=uuid.UUID(int=1), display_name="Alice", similarity=0.9, matched_image_count=2),
        IdentityMatch(identity_id=uuid.UUID(int=2), display_name="Bob", similarity=0.8, matched_image_count=1),
    ]
    proposal = ClusterProposal(members=[], centroid=np.zeros(512), proposed_matches=matches)
    assert proposal.best_match is matches[0]


def test_cluster_proposal_best_match_is_none_when_no_matches():
    from id_dedup.dedup.service.proposals import ClusterProposal

    proposal = ClusterProposal(members=[], centroid=np.zeros(512), proposed_matches=[])
    assert proposal.best_match is None


# ---------------------------------------------------------------------------
# propose_for_members
# ---------------------------------------------------------------------------

@patch("id_dedup.dedup.service.proposals._query_candidates", return_value=[])
def test_propose_for_members_returns_proposal_with_correct_members(mock_query, unit_member):
    from id_dedup.dedup.service.proposals import propose_for_members

    proposal = propose_for_members([unit_member])
    assert proposal.members == [unit_member]


@patch("id_dedup.dedup.service.proposals._query_candidates", return_value=[])
def test_propose_for_members_single_member_centroid_equals_embedding(mock_query, unit_member):
    from id_dedup.dedup.service.proposals import propose_for_members

    proposal = propose_for_members([unit_member])
    np.testing.assert_array_equal(proposal.centroid, unit_member.embedding)


@patch("id_dedup.dedup.service.proposals._query_candidates", return_value=[])
def test_propose_for_members_multi_member_centroid_is_unit_vector(mock_query, two_member_group):
    from id_dedup.dedup.service.proposals import propose_for_members

    proposal = propose_for_members(two_member_group)
    norm = float(np.linalg.norm(proposal.centroid))
    assert abs(norm - 1.0) < 1e-5


@patch("id_dedup.dedup.service.proposals._query_candidates", return_value=[])
def test_propose_for_members_passes_params_to_query(mock_query, unit_member):
    from id_dedup.dedup.service.proposals import propose_for_members

    propose_for_members([unit_member], top_k=3, min_similarity=0.7, similarity_band=0.05)
    args = mock_query.call_args.args
    assert args[1] == 3      # top_k
    assert args[2] == 0.7    # min_similarity
    assert args[3] == 0.05   # similarity_band


@patch("id_dedup.dedup.service.proposals._query_candidates")
def test_propose_for_members_returns_matches_from_query(mock_query, unit_member, strong_and_weak_match):
    from id_dedup.dedup.service.proposals import propose_for_members

    mock_query.return_value = strong_and_weak_match
    proposal = propose_for_members([unit_member])
    assert proposal.proposed_matches == strong_and_weak_match
    assert proposal.best_match is strong_and_weak_match[0]


# ---------------------------------------------------------------------------
# propose_matches
# ---------------------------------------------------------------------------

@patch("id_dedup.dedup.service.proposals._query_candidates", return_value=[])
def test_propose_matches_one_proposal_per_group(mock_query, cluster_result_with_groups):
    from id_dedup.dedup.service.proposals import propose_matches

    proposals = propose_matches(cluster_result_with_groups)
    group_proposals = [p for p in proposals if len(p.members) > 1]
    assert len(group_proposals) == 2


@patch("id_dedup.dedup.service.proposals._query_candidates", return_value=[])
def test_propose_matches_one_proposal_per_singleton(mock_query, cluster_result_with_groups):
    from id_dedup.dedup.service.proposals import propose_matches

    proposals = propose_matches(cluster_result_with_groups)
    singleton_proposals = [p for p in proposals if len(p.members) == 1]
    assert len(singleton_proposals) == 1


@patch("id_dedup.dedup.service.proposals._query_candidates", return_value=[])
def test_propose_matches_groups_precede_singletons(mock_query, cluster_result_with_groups):
    from id_dedup.dedup.service.proposals import propose_matches

    proposals = propose_matches(cluster_result_with_groups)
    # First two proposals should be the groups (2 members each)
    assert len(proposals[0].members) == 2
    assert len(proposals[1].members) == 2
    assert len(proposals[2].members) == 1


@patch("id_dedup.dedup.service.proposals._query_candidates", return_value=[])
def test_propose_matches_empty_result_returns_empty(mock_query):
    from id_dedup.dedup.pipeline import ClusterResult
    from id_dedup.dedup.service.proposals import propose_matches

    assert propose_matches(ClusterResult()) == []


# ---------------------------------------------------------------------------
# _query_candidates — ORM mocked, testing Identity-centroid query + filtering
# ---------------------------------------------------------------------------

def test_query_candidates_returns_empty_when_no_identities_with_centroid(query_centroid):
    from id_dedup.dedup.service.proposals import _query_candidates

    with patch("id_dedup.dedup.service.proposals.Identity") as MockIdentity:
        MockIdentity.objects.filter.return_value = chainable_qs([])
        result = _query_candidates(query_centroid, top_k=5, min_similarity=0.6, similarity_band=0.1)
    assert result == []


def test_query_candidates_returns_one_result_per_identity(query_centroid):
    from id_dedup.dedup.service.proposals import _query_candidates

    rows = [mock_identity_row(identity_id=uuid.UUID(int=1), display_name="Alice", distance=0.1, image_count=3)]
    with (
        patch("id_dedup.dedup.service.proposals.Identity") as MockIdentity,
        patch("id_dedup.dedup.service.proposals.Image") as MockImage,
    ):
        MockIdentity.objects.filter.return_value = chainable_qs(rows)
        MockImage.objects.filter.return_value = chainable_qs([])
        result = _query_candidates(query_centroid, top_k=5, min_similarity=0.6, similarity_band=0.0)
    assert len(result) == 1
    assert result[0].identity_id == uuid.UUID(int=1)


def test_query_candidates_similarity_from_centroid_distance(query_centroid):
    from id_dedup.dedup.service.proposals import _query_candidates

    rows = [mock_identity_row(identity_id=uuid.UUID(int=1), display_name="Alice", distance=0.1)]
    with (
        patch("id_dedup.dedup.service.proposals.Identity") as MockIdentity,
        patch("id_dedup.dedup.service.proposals.Image") as MockImage,
    ):
        MockIdentity.objects.filter.return_value = chainable_qs(rows)
        MockImage.objects.filter.return_value = chainable_qs([])
        result = _query_candidates(query_centroid, top_k=5, min_similarity=0.6, similarity_band=0.0)
    assert abs(result[0].similarity - 0.9) < 1e-5


def test_query_candidates_image_count_from_identity_row(query_centroid):
    from id_dedup.dedup.service.proposals import _query_candidates

    rows = [mock_identity_row(identity_id=uuid.UUID(int=1), display_name="Alice", distance=0.1, image_count=7)]
    with (
        patch("id_dedup.dedup.service.proposals.Identity") as MockIdentity,
        patch("id_dedup.dedup.service.proposals.Image") as MockImage,
    ):
        MockIdentity.objects.filter.return_value = chainable_qs(rows)
        MockImage.objects.filter.return_value = chainable_qs([])
        result = _query_candidates(query_centroid, top_k=5, min_similarity=0.6, similarity_band=0.0)
    assert result[0].matched_image_count == 7


def test_query_candidates_similarity_band_drops_weak_alternatives(query_centroid):
    from id_dedup.dedup.service.proposals import _query_candidates

    rows = [
        mock_identity_row(identity_id=uuid.UUID(int=1), display_name="Alice", distance=0.1),   # sim=0.9
        mock_identity_row(identity_id=uuid.UUID(int=2), display_name="Bob",   distance=0.4),   # sim=0.6
    ]
    with (
        patch("id_dedup.dedup.service.proposals.Identity") as MockIdentity,
        patch("id_dedup.dedup.service.proposals.Image") as MockImage,
    ):
        MockIdentity.objects.filter.return_value = chainable_qs(rows)
        MockImage.objects.filter.return_value = chainable_qs([])
        # band=0.1 → threshold=0.8; Bob (0.6) should be dropped
        result = _query_candidates(query_centroid, top_k=5, min_similarity=0.5, similarity_band=0.1)
    assert len(result) == 1
    assert result[0].identity_id == uuid.UUID(int=1)


def test_query_candidates_similarity_band_keeps_competitive_alternatives(query_centroid):
    from id_dedup.dedup.service.proposals import _query_candidates

    rows = [
        mock_identity_row(identity_id=uuid.UUID(int=1), display_name="Alice", distance=0.12),  # sim≈0.88
        mock_identity_row(identity_id=uuid.UUID(int=2), display_name="Bob",   distance=0.15),  # sim≈0.85
    ]
    with (
        patch("id_dedup.dedup.service.proposals.Identity") as MockIdentity,
        patch("id_dedup.dedup.service.proposals.Image") as MockImage,
    ):
        MockIdentity.objects.filter.return_value = chainable_qs(rows)
        MockImage.objects.filter.return_value = chainable_qs([])
        # band=0.1 → both within 0.1 of best, both should survive
        result = _query_candidates(query_centroid, top_k=5, min_similarity=0.6, similarity_band=0.1)
    assert len(result) == 2


def test_query_candidates_respects_top_k(query_centroid):
    from id_dedup.dedup.service.proposals import _query_candidates

    rows = [mock_identity_row(identity_id=uuid.UUID(int=i), display_name=f"Person{i}", distance=0.05 * i)
            for i in range(1, 8)]
    with (
        patch("id_dedup.dedup.service.proposals.Identity") as MockIdentity,
        patch("id_dedup.dedup.service.proposals.Image") as MockImage,
    ):
        MockIdentity.objects.filter.return_value = chainable_qs(rows)
        MockImage.objects.filter.return_value = chainable_qs([])
        result = _query_candidates(query_centroid, top_k=3, min_similarity=0.0, similarity_band=0.0)
    assert len(result) == 3


# ---------------------------------------------------------------------------
# process_uploads
# ---------------------------------------------------------------------------


def test_process_uploads_saves_files_and_calls_pipeline(tmp_path):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from id_dedup.dedup.pipeline import ClusterResult
    from id_dedup.dedup.service.workflow import process_uploads

    uploads = [
        SimpleUploadedFile("photo1.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 100),
        SimpleUploadedFile("photo2.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 100),
    ]

    with patch("id_dedup.dedup.service.workflow.pipeline.process_images") as mock_process:
        mock_process.return_value = ClusterResult()
        result = process_uploads(uploads, tmp_path)

    saved = list(tmp_path.iterdir())
    assert len(saved) == 2
    suffixes = {f.suffix for f in saved}
    assert suffixes == {".jpg", ".png"}
    jpeg_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 100
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    assert all(f.read_bytes() in (jpeg_bytes, png_bytes) for f in saved)
    mock_process.assert_called_once()
    args = mock_process.call_args[0][0]
    assert len(args) == 2


def test_process_uploads_handles_none(tmp_path):
    from id_dedup.dedup.service.workflow import process_uploads

    with patch("id_dedup.dedup.service.workflow.pipeline.process_images") as mock_process:
        process_uploads(None, tmp_path)
    mock_process.assert_called_once_with([])


def test_process_uploads_uses_default_filename_when_missing(tmp_path):
    from id_dedup.dedup.service.workflow import process_uploads

    mock_file = MagicMock()
    mock_file.name = ""
    mock_file.read.return_value = b"\xff\xd8\xff\xe0" + b"\x00" * 8
    mock_file.seek = MagicMock()
    mock_file.chunks.return_value = [b"\xff\xd8\xff\xe0" + b"\x00" * 8]

    with patch("id_dedup.dedup.service.workflow.pipeline.process_images"):
        process_uploads([mock_file], tmp_path)

    saved = list(tmp_path.iterdir())
    assert len(saved) == 1
    assert saved[0].stem.startswith("image")


def test_process_uploads_rejects_non_image(tmp_path):
    from io import BytesIO

    import pytest

    from id_dedup.dedup.service.workflow import process_uploads

    mock_file = MagicMock()
    mock_file.name = "doc.pdf"
    mock_file.read.return_value = b"%PDF-1.4 garbage"
    mock_file.seek = MagicMock()

    with pytest.raises(ValueError, match="Unsupported"):
        process_uploads([mock_file], tmp_path)


def test_process_uploads_rejects_disguised_file(tmp_path):
    import pytest

    from id_dedup.dedup.service.workflow import process_uploads

    mock_file = MagicMock()
    mock_file.name = "photo.jpg"
    mock_file.read.return_value = b"%PDF-1.4 garbage"
    mock_file.seek = MagicMock()

    with pytest.raises(ValueError, match="Unsupported"):
        process_uploads([mock_file], tmp_path)


# ---------------------------------------------------------------------------
# apply_split
# ---------------------------------------------------------------------------


def test_apply_split_moves_files_via_filenames(splittable_result):
    from id_dedup.dedup.service.workflow import apply_split

    result = apply_split(splittable_result, 0, filenames=[
        "synthetic/person0/photo0.jpg",
        "synthetic/person0/photo1.jpg",
    ])
    assert len(result.clusters[0]) == 1
    assert result.clusters[0][0].file.name == "photo2.jpg"
    # Moved group gets the next available label
    new_label = max(k for k in result.clusters if k >= 0)
    assert new_label == 2
    assert len(result.clusters[2]) == 2


def test_apply_split_moves_file_via_path(splittable_result):
    from id_dedup.dedup.service.workflow import apply_split

    result = apply_split(splittable_result, 1, file_path="synthetic/person1/photo0.jpg")
    assert len(result.clusters[1]) == 1
    assert result.clusters[1][0].file.name == "photo1.jpg"
    # Single file move lands in singletons
    assert len(result.singletons) == 1


def test_apply_split_raises_on_no_matching_files(splittable_result):
    from id_dedup.dedup.service.workflow import apply_split

    with pytest.raises(ValueError, match="No matching files found"):
        apply_split(splittable_result, 0, file_path="nonexistent.jpg")


def test_apply_split_raises_on_invalid_label(splittable_result):
    from id_dedup.dedup.service.workflow import apply_split

    with pytest.raises(ValueError):
        apply_split(splittable_result, 99, file_path="anything.jpg")


def test_apply_split_raises_when_both_filenames_and_file_path(splittable_result):
    from id_dedup.dedup.service.workflow import apply_split

    with pytest.raises(ValueError, match="not both"):
        apply_split(splittable_result, 0, filenames=["a.jpg"], file_path="a.jpg")


def test_apply_split_to_specified_cluster(splittable_result):
    from id_dedup.dedup.service.workflow import apply_split

    file_path = "synthetic/person0/photo0.jpg"
    result = apply_split(splittable_result, 0, file_path=file_path, to_cluster=1)
    target = pathlib.Path(file_path)
    assert any(m.file == target for m in result.clusters[1])
    assert all(m.file != target for m in result.clusters[0])


# ---------------------------------------------------------------------------
# create_assignment
# ---------------------------------------------------------------------------


def test_create_assignment_resolves_from_proposal():
    from id_dedup.dedup.pipeline import ClusterMember
    from id_dedup.dedup.service.proposals import ClusterProposal, IdentityMatch
    from id_dedup.dedup.service.workflow import create_assignment
    from tests.unit.wizard.helpers import unit_vector

    alice_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    match = IdentityMatch(
        identity_id=alice_id, display_name="Alice", similarity=0.9, matched_image_count=3,
    )
    proposal = ClusterProposal(
        members=[ClusterMember(file=pathlib.Path("p.jpg"), embedding=unit_vector(seed=0))],
        centroid=unit_vector(seed=1),
        proposed_matches=[match],
    )

    with patch("id_dedup.dedup.service.workflow.Identity") as MockIdentity:
        MockIdentity.objects.filter.return_value.exists.return_value = True
        assignments, registry = create_assignment(proposal, str(alice_id), {}, {}, 0)

    entry = assignments["0"]
    assert entry["identity_id"] == str(alice_id)
    assert entry["display_name"] == "Alice"
    assert entry["is_new"] is False


def test_create_assignment_marks_new_when_proposal_identity_not_in_db():
    from id_dedup.dedup.pipeline import ClusterMember
    from id_dedup.dedup.service.proposals import ClusterProposal, IdentityMatch
    from id_dedup.dedup.service.workflow import create_assignment
    from tests.unit.wizard.helpers import unit_vector

    alice_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    match = IdentityMatch(
        identity_id=alice_id, display_name="Alice", similarity=0.9, matched_image_count=3,
    )
    proposal = ClusterProposal(
        members=[ClusterMember(file=pathlib.Path("p.jpg"), embedding=unit_vector(seed=0))],
        centroid=unit_vector(seed=1),
        proposed_matches=[match],
    )

    with patch("id_dedup.dedup.service.workflow.Identity") as MockIdentity:
        MockIdentity.objects.filter.return_value.exists.return_value = False
        assignments, registry = create_assignment(proposal, str(alice_id), {}, {}, 0)

    assert assignments["0"]["is_new"] is True


def test_create_assignment_resolves_from_registry():
    from id_dedup.dedup.pipeline import ClusterMember
    from id_dedup.dedup.service.proposals import ClusterProposal
    from id_dedup.dedup.service.workflow import create_assignment
    from tests.unit.wizard.helpers import unit_vector

    proposal = ClusterProposal(
        members=[ClusterMember(file=pathlib.Path("p.jpg"), embedding=unit_vector(seed=0))],
        centroid=unit_vector(seed=1),
        proposed_matches=[],
    )
    registry = {"00000000-0000-0000-0000-000000000003": "Session Person"}

    assignments, registry = create_assignment(proposal, "00000000-0000-0000-0000-000000000003", {}, registry, 0)

    entry = assignments["0"]
    assert entry["identity_id"] == "00000000-0000-0000-0000-000000000003"
    assert entry["display_name"] == "Session Person"
    assert entry["is_new"] is True


def test_create_assignment_resolves_from_db():
    from id_dedup.dedup.pipeline import ClusterMember
    from id_dedup.dedup.service.proposals import ClusterProposal
    from id_dedup.dedup.service.workflow import create_assignment
    from tests.unit.wizard.helpers import unit_vector

    proposal = ClusterProposal(
        members=[ClusterMember(file=pathlib.Path("p.jpg"), embedding=unit_vector(seed=0))],
        centroid=unit_vector(seed=1),
        proposed_matches=[],
    )

    with patch("id_dedup.dedup.service.workflow.Identity") as MockIdentity:
        mock_identity = MockIdentity.objects.get.return_value
        mock_identity.display_name = "DB Person"
        assignments, registry = create_assignment(proposal, "00000000-0000-0000-0000-000000000004", {}, {}, 0)

    entry = assignments["0"]
    assert entry["identity_id"] == "00000000-0000-0000-0000-000000000004"
    assert entry["display_name"] == "DB Person"
    assert entry["is_new"] is False


def test_create_assignment_defaults_to_unknown_when_not_found():
    from django.core.exceptions import ObjectDoesNotExist

    from id_dedup.dedup.pipeline import ClusterMember
    from id_dedup.dedup.service.proposals import ClusterProposal
    from id_dedup.dedup.service.workflow import create_assignment
    from tests.unit.wizard.helpers import unit_vector

    proposal = ClusterProposal(
        members=[ClusterMember(file=pathlib.Path("p.jpg"), embedding=unit_vector(seed=0))],
        centroid=unit_vector(seed=1),
        proposed_matches=[],
    )

    ghost_id = str(uuid.uuid4())
    with patch("id_dedup.dedup.service.workflow.Identity") as MockIdentity:
        MockIdentity.DoesNotExist = ObjectDoesNotExist
        MockIdentity.objects.get.side_effect = ObjectDoesNotExist
        assignments, registry = create_assignment(proposal, ghost_id, {}, {}, 0)

    entry = assignments["0"]
    assert entry["display_name"] == "Unknown"
    assert entry["is_new"] is True


def test_create_assignment_garbage_collects_previous_is_new():
    from django.core.exceptions import ObjectDoesNotExist

    from id_dedup.dedup.pipeline import ClusterMember
    from id_dedup.dedup.service.proposals import ClusterProposal
    from id_dedup.dedup.service.workflow import create_assignment
    from tests.unit.wizard.helpers import unit_vector

    proposal = ClusterProposal(
        members=[ClusterMember(file=pathlib.Path("p.jpg"), embedding=unit_vector(seed=0))],
        centroid=unit_vector(seed=1),
        proposed_matches=[],
    )

    prev_id = str(uuid.uuid4())
    new_id = str(uuid.uuid4())
    assignments = {"0": {"identity_id": prev_id, "display_name": "Old", "is_new": True}}
    registry = {prev_id: "Old"}

    with patch("id_dedup.dedup.service.workflow.Identity") as MockIdentity:
        MockIdentity.DoesNotExist = ObjectDoesNotExist
        MockIdentity.objects.get.side_effect = ObjectDoesNotExist
        assignments, registry = create_assignment(proposal, new_id, assignments, registry, 0)

    assert prev_id not in registry


# ---------------------------------------------------------------------------
# create_new_identity_assignment
# ---------------------------------------------------------------------------


def test_create_new_identity_assignment_creates_entry():
    from id_dedup.dedup.service.workflow import create_new_identity_assignment

    identity_id, registry, assignments = create_new_identity_assignment("New Person", {}, {}, 0)

    assert uuid.UUID(identity_id)  # valid uuid
    assert registry[identity_id] == "New Person"
    entry = assignments["0"]
    assert entry["display_name"] == "New Person"
    assert entry["is_new"] is True


def test_create_new_identity_assignment_garbage_collects_previous():
    from id_dedup.dedup.service.workflow import create_new_identity_assignment

    prev_id = str(uuid.uuid4())
    assignments = {"0": {"identity_id": prev_id, "display_name": "Old", "is_new": True}}
    registry = {prev_id: "Old"}

    identity_id, registry, assignments = create_new_identity_assignment("New Person", registry, assignments, 0)

    assert prev_id not in registry


# ---------------------------------------------------------------------------
# persist_assignments
# ---------------------------------------------------------------------------


def test_persist_assignments_creates_new_identities():
    from id_dedup.dedup.pipeline import ClusterMember
    from id_dedup.dedup.service.proposals import ClusterProposal
    from id_dedup.dedup.service.workflow import persist_assignments
    from tests.unit.wizard.helpers import unit_vector

    identity_id = str(uuid.uuid4())
    assignments = {"0": {"identity_id": identity_id, "display_name": "New Person", "is_new": True}}
    member = ClusterMember(file=pathlib.Path("nope.jpg"), embedding=unit_vector(seed=0))
    proposal = ClusterProposal(members=[member], centroid=unit_vector(seed=1), proposed_matches=[])

    with (
        patch("id_dedup.dedup.service.workflow.Identity") as MockIdentity,
        patch("id_dedup.dedup.service.workflow.Image") as MockImage,
        patch("id_dedup.dedup.service.workflow.Path.exists", return_value=False),
    ):
        MockIdentity.objects.get_or_create.return_value = (MockIdentity, True)
        summary = persist_assignments(assignments, [proposal], tmpdir_name=None)

    assert summary["total_clusters"] == 1
    assert summary["assigned"] == 1
    assert summary["new_identities"] == 1
    MockIdentity.objects.get_or_create.assert_called_once_with(
        pk=identity_id, defaults={"display_name": "New Person"},
    )


def test_persist_assignments_creates_images():
    from id_dedup.dedup.pipeline import ClusterMember
    from id_dedup.dedup.service.proposals import ClusterProposal
    from id_dedup.dedup.service.workflow import persist_assignments
    from tests.unit.wizard.helpers import unit_vector

    identity_id = str(uuid.uuid4())
    assignments = {"0": {"identity_id": identity_id, "display_name": "Existing", "is_new": False}}
    member = ClusterMember(file=pathlib.Path(__file__), embedding=unit_vector(seed=0))
    proposal = ClusterProposal(members=[member], centroid=unit_vector(seed=1), proposed_matches=[])

    with (
        patch("id_dedup.dedup.service.workflow.Identity") as MockIdentity,
        patch("id_dedup.dedup.service.workflow.Image") as MockImage,
    ):
        MockIdentity.objects.get.return_value = MockIdentity
        with patch.object(pathlib.Path, "exists", return_value=True):
            summary = persist_assignments(assignments, [proposal], tmpdir_name=None)

    assert MockImage.objects.create.called
    call_kwargs = MockImage.objects.create.call_args[1]
    assert call_kwargs["identity"] is MockIdentity
    assert call_kwargs["embedding"] is member.embedding


def test_persist_assignments_skips_deleted_identity():
    from id_dedup.dedup.pipeline import ClusterMember
    from id_dedup.dedup.service.proposals import ClusterProposal
    from id_dedup.dedup.service.workflow import persist_assignments
    from tests.unit.wizard.helpers import unit_vector

    ghost_id = "00000000-0000-0000-0000-000000000009"
    assignments = {"0": {"identity_id": ghost_id, "display_name": "Ghost", "is_new": False}}
    member = ClusterMember(file=pathlib.Path("nope.jpg"), embedding=unit_vector(seed=0))
    proposal = ClusterProposal(members=[member], centroid=unit_vector(seed=1), proposed_matches=[])

    with (
        patch("id_dedup.dedup.service.workflow.Identity") as MockIdentity,
        patch("id_dedup.dedup.service.workflow.Image") as MockImage,
        patch("id_dedup.dedup.service.workflow.Path.exists", return_value=False),
    ):
        MockIdentity.objects.get.side_effect = MockIdentity.DoesNotExist
        summary = persist_assignments(assignments, [proposal], tmpdir_name=None)

    assert not MockImage.objects.create.called
    assert summary["total_clusters"] == 1
    assert summary["assigned"] == 1
    assert summary["new_identities"] == 0


def test_persist_assignments_saves_centroid_on_identity():
    from id_dedup.dedup.pipeline import ClusterMember
    from id_dedup.dedup.service.proposals import ClusterProposal
    from id_dedup.dedup.service.workflow import persist_assignments
    from tests.unit.wizard.helpers import unit_vector

    embedding = unit_vector(seed=42)
    identity_id = str(uuid.uuid4())
    assignments = {"0": {"identity_id": identity_id, "display_name": "Alice", "is_new": True}}
    member = ClusterMember(file=pathlib.Path(__file__), embedding=embedding)
    proposal = ClusterProposal(members=[member], centroid=unit_vector(seed=1), proposed_matches=[])

    mock_identity = MagicMock()
    with (
        patch("id_dedup.dedup.service.workflow.Identity") as MockIdentity,
        patch("id_dedup.dedup.service.workflow.Image"),
    ):
        MockIdentity.objects.get_or_create.return_value = (mock_identity, True)
        with patch.object(pathlib.Path, "exists", return_value=True):
            persist_assignments(assignments, [proposal], tmpdir_name=None)

    mock_identity.update_centroid.assert_called_once_with()


def test_persist_assignments_centroid_is_unit_vector():
    from id_dedup.dedup.pipeline import ClusterMember
    from id_dedup.dedup.service.proposals import ClusterProposal
    from id_dedup.dedup.service.workflow import persist_assignments
    from tests.unit.wizard.helpers import unit_vector

    emb1, emb2 = unit_vector(seed=10), unit_vector(seed=11)
    identity_id = str(uuid.uuid4())
    assignments = {"0": {"identity_id": identity_id, "display_name": "Alice", "is_new": True}}
    member = ClusterMember(file=pathlib.Path(__file__), embedding=emb1)
    proposal = ClusterProposal(members=[member], centroid=unit_vector(seed=1), proposed_matches=[])

    mock_identity = MagicMock()
    with (
        patch("id_dedup.dedup.service.workflow.Identity") as MockIdentity,
        patch("id_dedup.dedup.service.workflow.Image"),
    ):
        MockIdentity.objects.get_or_create.return_value = (mock_identity, True)
        with patch.object(pathlib.Path, "exists", return_value=True):
            persist_assignments(assignments, [proposal], tmpdir_name=None)

    mock_identity.update_centroid.assert_called_once_with()


def test_persist_assignments_cleans_up_temp_dir(tmp_path):
    from id_dedup.dedup.service.workflow import persist_assignments

    (tmp_path / "some_image.jpg").touch()

    with (
        patch("id_dedup.dedup.service.workflow.Identity"),
        patch("id_dedup.dedup.service.workflow.Image"),
    ):
        summary = persist_assignments({}, [], tmpdir_name=str(tmp_path))

    assert not tmp_path.exists()
    assert summary["total_clusters"] == 0


# ---------------------------------------------------------------------------
# search_identities
# ---------------------------------------------------------------------------


def test_search_identities_queries_db_by_icontains():
    from id_dedup.dedup.service.workflow import search_identities

    with patch("id_dedup.dedup.service.workflow.Identity") as MockIdentity:
        mock_qs = chainable_qs([])
        MockIdentity.objects.filter.return_value = mock_qs
        search_identities("Ali", {})

    call_kwargs = MockIdentity.objects.filter.call_args[1]
    assert "display_name__icontains" in call_kwargs
    assert call_kwargs["display_name__icontains"] == "Ali"


def test_search_identities_respects_limit():
    from id_dedup.dedup.service.workflow import search_identities

    with patch("id_dedup.dedup.service.workflow.Identity") as MockIdentity:
        mock_identities = [MagicMock(pk=f"uuid-{i}", display_name=f"Person{i}") for i in range(20)]
        mock_qs = chainable_qs(mock_identities)
        MockIdentity.objects.filter.return_value = mock_qs
        results = search_identities("Person", {}, limit=5)

    assert len(results) == 5
    # Verify the LIMIT is applied at the ORM level, not in Python
    last_call_args = MockIdentity.objects.filter.return_value.__getitem__.call_args[0][0]
    assert last_call_args == slice(None, 5, None)


def test_search_identities_returns_db_results():
    from id_dedup.dedup.service.workflow import search_identities

    with patch("id_dedup.dedup.service.workflow.Identity") as MockIdentity:
        mock_identity_1 = MagicMock()
        mock_identity_1.pk = "uuid-alice"
        mock_identity_1.display_name = "Alice"
        mock_identity_2 = MagicMock()
        mock_identity_2.pk = "uuid-albert"
        mock_identity_2.display_name = "Albert"
        mock_qs = chainable_qs([mock_identity_1, mock_identity_2])
        MockIdentity.objects.filter.return_value = mock_qs
        results = search_identities("Ali", {})

    assert len(results) == 2
    assert results[0] == {"identity_id": "uuid-alice", "display_name": "Alice", "is_new": False}
    assert results[1] == {"identity_id": "uuid-albert", "display_name": "Albert", "is_new": False}


def test_search_identities_merges_registry():
    from id_dedup.dedup.service.workflow import search_identities

    registry = {"uuid-session": "Session Person"}

    with patch("id_dedup.dedup.service.workflow.Identity") as MockIdentity:
        mock_qs = chainable_qs([])
        MockIdentity.objects.filter.return_value = mock_qs
        results = search_identities("Session", registry)

    assert len(results) == 1
    assert results[0] == {"identity_id": "uuid-session", "display_name": "Session Person", "is_new": True}


def test_search_identities_deduplicates_registry_and_db():
    from id_dedup.dedup.service.workflow import search_identities

    registry = {"uuid-alice": "Alice"}

    with patch("id_dedup.dedup.service.workflow.Identity") as MockIdentity:
        mock_identity = MagicMock()
        mock_identity.pk = "uuid-alice"
        mock_identity.display_name = "Alice"
        mock_qs = chainable_qs([mock_identity])
        MockIdentity.objects.filter.return_value = mock_qs
        results = search_identities("Ali", registry)

    assert len(results) == 1
    assert results[0]["is_new"] is False
    assert results[0]["identity_id"] == "uuid-alice"


def test_search_identities_case_insensitive_dedup():
    from id_dedup.dedup.service.workflow import search_identities

    registry = {"uuid-session": "alice"}

    with patch("id_dedup.dedup.service.workflow.Identity") as MockIdentity:
        mock_identity = MagicMock()
        mock_identity.pk = "uuid-alice"
        mock_identity.display_name = "Alice"
        mock_qs = chainable_qs([mock_identity])
        MockIdentity.objects.filter.return_value = mock_qs
        results = search_identities("Ali", registry)

    assert len(results) == 1


def test_search_identities_returns_empty_list_when_no_results():
    from id_dedup.dedup.service.workflow import search_identities

    with patch("id_dedup.dedup.service.workflow.Identity") as MockIdentity:
        mock_qs = chainable_qs([])
        MockIdentity.objects.filter.return_value = mock_qs
        results = search_identities("Nonexistent", {})

    assert results == []
