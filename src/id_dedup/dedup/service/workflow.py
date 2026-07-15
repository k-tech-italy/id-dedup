from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from django.core.files import File
from django.db import transaction

from .. import pipeline
from ..models import Identity, Image
from ..pipeline import normalised_mean

if TYPE_CHECKING:
    from collections.abc import Iterable

    from django.core.files.uploadedfile import UploadedFile

    from ..pipeline import ClusterResult
    from .proposals import ClusterProposal


_JPEG = b'\xff\xd8\xff'
_PNG = b'\x89PNG\r\n\x1a\n'


def _is_allowed_image(f) -> bool:
    header = f.read(12)
    f.seek(0)
    if header[:3] == _JPEG:
        return True
    if header[:8] == _PNG:
        return True
    if header[:4] == b'RIFF' and header[8:12] == b'WEBP':
        return True
    return False


def process_uploads(
    uploads: Iterable[UploadedFile] | None,
    tmpdir: str | Path,
    default_file_name: str = "image",
) -> ClusterResult:
    uploads = list(uploads or [])

    invalid = [f.name for f in uploads if not _is_allowed_image(f)]
    if invalid:
        raise ValueError(
            f"Unsupported file type(s): {', '.join(invalid)}. Only JPG, PNG, and WEBP are accepted."
        )

    tmpdir = Path(tmpdir)
    saved: list[Path] = []
    for file in uploads:
        path = Path(file.name or default_file_name)
        unique_name = f"{path.stem}_{uuid.uuid4().hex[:8]}{path.suffix}"
        dest = tmpdir / unique_name
        with open(dest, "wb") as out:
            out.writelines(file.chunks())
        saved.append(dest)

    return pipeline.process_images(saved)


def apply_split(
    result: ClusterResult,
    cluster_label: int,
    filenames: list[str] | None = None,
    file_path: str | None = None,
    to_cluster: int | None = None,
) -> ClusterResult:
    """
    Move files out of a cluster, then return the updated result.

    Exactly one of `filenames` or `file_path` must be provided.
    Providing both raises a `ValueError`.
    """
    if filenames is not None and file_path:
        raise ValueError("Provide either filenames or file_path, not both")

    file_set: set[Path] = set()

    if filenames is not None:
        for fname in filenames:
            for member in result.clusters.get(cluster_label, []):
                if member.file.name == fname or str(member.file) == fname:
                    file_set.add(member.file)
                    break
    elif file_path:
        fp = Path(file_path)
        for member in result.clusters.get(cluster_label, []):
            if member.file == fp or member.file.name == file_path:
                file_set.add(member.file)
                break

    if not file_set:
        raise ValueError("No matching files found in cluster")

    result.split(cluster_label, file_set, to_cluster=to_cluster)
    return result


def create_assignment(
    proposal: ClusterProposal,
    identity_id: str,
    assignments: dict,
    registry: dict,
    adj_index: int,
) -> tuple[dict, dict]:
    """Resolve identity info, create assignment entry, garbage-collect previous is_new."""
    prev = assignments.get(str(adj_index))
    if prev and prev.get("is_new") and prev["identity_id"] != identity_id:
        registry.pop(prev["identity_id"], None)

    display_name = next(
        (m.display_name for m in proposal.proposed_matches if m.identity_id == uuid.UUID(identity_id)),
        None,
    )

    if display_name is not None:
        is_new = not Identity.objects.filter(pk=identity_id).exists()
    elif identity_id in registry:
        display_name = registry[identity_id]
        is_new = True
    else:
        try:
            identity = Identity.objects.get(pk=identity_id)
            display_name = identity.display_name
            is_new = False
        except Identity.DoesNotExist:
            display_name = "Unknown"
            is_new = True

    assignments[str(adj_index)] = {
        "identity_id": identity_id,
        "display_name": display_name,
        "is_new": is_new,
    }

    return assignments, registry


def create_new_identity_assignment(
    display_name: str,
    registry: dict,
    assignments: dict,
    adj_index: int,
) -> tuple[str, dict, dict]:
    """Create a new identity assignment. Returns (identity_id, registry, assignments)."""
    identity_id = str(uuid.uuid4())

    prev = assignments.get(str(adj_index))
    if prev and prev.get("is_new") and prev["identity_id"] != identity_id:
        registry.pop(prev["identity_id"], None)

    registry[identity_id] = display_name

    assignments[str(adj_index)] = {
        "identity_id": identity_id,
        "display_name": display_name,
        "is_new": True,
    }

    return identity_id, registry, assignments


def search_identities(
    query: str,
    registry: dict[str, str],
    limit: int = 10,
) -> list[dict]:
    """
    Search identities by display name, merging DB results with session registry.

    Returns a list of dicts with keys: identity_id, display_name, is_new.
    DB results appear first (is_new=False), then registry-only entries (is_new=True).
    Deduplicated by display_name (case-insensitive) — if a registry entry has the
    same display name as a DB entry, only the DB entry is returned.
    """
    identities = list(Identity.objects.filter(display_name__icontains=query)[:limit])
    seen_names = {i.display_name.lower() for i in identities}

    results = [{"identity_id": str(i.pk), "display_name": i.display_name, "is_new": False} for i in identities]

    for rid, rname in registry.items():
        if query.lower() in rname.lower() and rname.lower() not in seen_names:
            seen_names.add(rname.lower())
            results.append({"identity_id": rid, "display_name": rname, "is_new": True})

    return results


@transaction.atomic
def persist_assignments(
    assignments: dict,
    proposals: list[ClusterProposal],
    tmpdir_name: str | None = None,
) -> dict:
    """Persist all assignments to the DB, clean up temp dir. Returns summary dict."""
    total = len(proposals)
    assigned = len(assignments)
    new_ids = 0

    for adj_index_str, assignment in assignments.items():
        adj_index = int(adj_index_str)
        if assignment.get("is_new"):
            new_ids += 1
            identity, _ = Identity.objects.get_or_create(
                pk=assignment["identity_id"],
                defaults={"display_name": assignment["display_name"]},
            )
        else:
            try:
                identity = Identity.objects.get(pk=assignment["identity_id"])
            except Identity.DoesNotExist:
                continue

        if adj_index >= len(proposals):
            continue

        proposal = proposals[adj_index]
        for member in proposal.members:
            if not member.file.exists():
                continue
            ext = "".join(member.file.suffixes) or ".jpg"
            dest_name = f"{uuid.uuid4()}{ext}"
            with open(member.file, "rb") as f:
                Image.objects.create(
                    identity=identity,
                    embedding=member.embedding,
                    source_image=File(f, name=dest_name),
                )

        all_embeddings = list(Image.objects.filter(identity=identity).values_list("embedding", flat=True))
        if all_embeddings:
            identity.centroid = normalised_mean(np.stack(all_embeddings)).tolist()
            identity.image_count = len(all_embeddings)
            identity.save(update_fields=["centroid", "image_count", "updated_at"])

    if tmpdir_name:
        tmpdir_path = Path(tmpdir_name)
        if tmpdir_path.exists():
            shutil.rmtree(tmpdir_path, ignore_errors=True)

    return {
        "wizard_step": "complete",
        "total_clusters": total,
        "assigned": assigned,
        "new_identities": new_ids,
    }
