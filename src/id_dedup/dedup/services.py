from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from pgvector.django import CosineDistance

from .pipeline import ClusterMember, ClusterResult
from .models import Image


@dataclass
class IdentityMatch:
    identity_id: object  # UUID PK — avoids loading Identity objects unless needed
    display_name: str
    similarity: float  # cosine similarity in [0, 1]; higher = better
    matched_image_count: int  # how many of this identity's images were near the centroid


@dataclass
class ClusterProposal:
    """Proposed identity matches for a single cluster (or singleton).

    proposed_matches is ranked descending by similarity.
    An empty list means no identity in the DB crossed min_similarity — likely a new person.
    No DB records are modified by this layer; proposals are for manual review only.
    """

    members: list[ClusterMember]
    centroid: np.ndarray
    proposed_matches: list[IdentityMatch]

    @property
    def is_new_identity(self) -> bool:
        return len(self.proposed_matches) == 0

    @property
    def best_match(self) -> IdentityMatch | None:
        return self.proposed_matches[0] if self.proposed_matches else None


def _centroid(members: list[ClusterMember]) -> np.ndarray:
    """Mean of L2-normalised embeddings, re-normalised — stable centroid in cosine space."""
    if len(members) == 1:
        return members[0].embedding
    vecs = np.stack([m.embedding for m in members])
    mean = vecs.mean(axis=0)
    return mean / np.linalg.norm(mean)


def _query_candidates(
    centroid: np.ndarray,
    top_k: int,
    min_similarity: float,
    similarity_band: float,
) -> list[IdentityMatch]:
    """Find the top_k closest existing identities to a centroid.

    Fetches all assigned images within min_similarity, collapses to one entry
    per identity (best similarity + count of matching images), then applies
    the similarity_band filter to drop alternatives that aren't competitive
    with the best match.
    """
    max_distance = 1.0 - min_similarity

    rows = (
        Image.objects.filter(identity__isnull=False)
        .annotate(distance=CosineDistance("embedding", centroid.tolist()))
        .filter(distance__lte=max_distance)
        .select_related("identity")
        .order_by("distance")
    )

    # Collapse to one entry per identity: highest similarity + count of close images
    best: dict[object, IdentityMatch] = {}
    for img in rows:
        sim = 1.0 - float(img.distance)
        pk = img.identity_id
        if pk not in best:
            best[pk] = IdentityMatch(
                identity_id=pk,
                display_name=img.identity.display_name,
                similarity=sim,
                matched_image_count=1,
            )
        else:
            entry = best[pk]
            entry.matched_image_count += 1
            if sim > entry.similarity:
                entry.similarity = sim

    ranked = sorted(best.values(), key=lambda m: m.similarity, reverse=True)[:top_k]

    # Drop alternatives that aren't competitive with the best match
    if ranked and similarity_band > 0:
        threshold = ranked[0].similarity - similarity_band
        ranked = [m for m in ranked if m.similarity >= threshold]

    return ranked


def propose_for_members(
    members: list[ClusterMember],
    top_k: int = 5,
    min_similarity: float = 0.6,
    similarity_band: float = 0.1,
) -> ClusterProposal:
    """Propose identity matches for an arbitrary set of ClusterMembers.

    This is the primary entry point for both automatic clustering results and
    manual re-groupings (e.g. when the user splits a cluster in the UI and
    needs proposals for each new sub-group).

    similarity_band: only identities within this much of the best match are
    shown. Set to 0 to disable filtering and always return up to top_k.
    """
    centroid = _centroid(members)
    matches = _query_candidates(centroid, top_k, min_similarity, similarity_band)
    return ClusterProposal(members=members, centroid=centroid, proposed_matches=matches)


def propose_matches(
    result: ClusterResult,
    top_k: int = 5,
    min_similarity: float = 0.6,
    similarity_band: float = 0.1,
) -> list[ClusterProposal]:
    """Stage 2 — match every confirmed cluster against existing DB identities.

    Call this once, after the user has finished editing the ClusterResult.
    Each group is queried as a unit (one DB call per cluster).
    Singletons are each queried individually.
    Returns proposals: groups first (ascending by label), then singletons.
    """
    proposals: list[ClusterProposal] = []

    for _label, members in sorted(result.groups.items()):
        proposals.append(propose_for_members(members, top_k, min_similarity, similarity_band))

    for member in result.singletons:
        proposals.append(propose_for_members([member], top_k, min_similarity, similarity_band))

    return proposals