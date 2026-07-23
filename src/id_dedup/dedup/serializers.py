from __future__ import annotations

import base64
import pathlib
import uuid

import numpy as np

from id_dedup.ml.pipeline import ClusterMember, ClusterResult
from .service.proposals import ClusterProposal, IdentityMatch


def serialize_member(m: ClusterMember) -> dict:
    """Convert a ClusterMember to a JSON-safe dict (file path + base64 embedding)."""
    return {"file": str(m.file), "embedding": base64.b64encode(m.embedding.tobytes()).decode()}


def deserialize_member(d: dict) -> ClusterMember:
    """Restore a ClusterMember from a dict produced by ``serialize_member``."""
    return ClusterMember(
        file=pathlib.Path(d["file"]),
        embedding=np.frombuffer(base64.b64decode(d["embedding"]), dtype=np.float32).copy(),
    )


def serialize_result(result: ClusterResult) -> dict:
    """Convert a ClusterResult to a JSON-safe dict for session storage."""
    return {
        "clusters": {str(label): [serialize_member(m) for m in members] for label, members in result.clusters.items()},
        "failed": [str(f) for f in result.failed],
    }


def deserialize_result(data: dict) -> ClusterResult:
    """Restore a ClusterResult from a dict produced by ``serialize_result``."""
    result = ClusterResult()
    for label_str, members in data.get("clusters", {}).items():
        label = int(label_str)
        result.clusters[label] = [deserialize_member(m) for m in members]
    result.failed = [pathlib.Path(f) for f in data.get("failed", [])]
    return result


def serialize_identity_match(m: IdentityMatch) -> dict:
    """Convert an IdentityMatch to a JSON-safe dict."""
    return {
        "identity_id": str(m.identity_id),
        "display_name": m.display_name,
        "similarity": m.similarity,
        "matched_image_count": m.matched_image_count,
        "image_url": m.image_url,
    }


def deserialize_identity_match(d: dict) -> IdentityMatch:
    """Restore an IdentityMatch from a dict produced by ``serialize_identity_match``."""
    return IdentityMatch(
        identity_id=uuid.UUID(d["identity_id"]),
        display_name=d["display_name"],
        similarity=d["similarity"],
        matched_image_count=d["matched_image_count"],
        image_url=d.get("image_url"),
    )


def serialize_proposal(p: ClusterProposal) -> dict:
    """Convert a ClusterProposal to a JSON-safe dict (members + centroid + matches)."""
    return {
        "members": [serialize_member(m) for m in p.members],
        "centroid": base64.b64encode(p.centroid.tobytes()).decode(),
        "proposed_matches": [serialize_identity_match(m) for m in p.proposed_matches],
    }


def deserialize_proposal(d: dict) -> ClusterProposal:
    """Restore a ClusterProposal from a dict produced by ``serialize_proposal``."""
    return ClusterProposal(
        members=[deserialize_member(m) for m in d["members"]],
        centroid=np.frombuffer(base64.b64decode(d["centroid"]), dtype=np.float32).copy(),
        proposed_matches=[deserialize_identity_match(m) for m in d.get("proposed_matches", [])],
    )
