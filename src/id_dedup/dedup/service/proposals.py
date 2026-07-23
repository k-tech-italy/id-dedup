from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from django.core.files.storage import default_storage
from pgvector.django import CosineDistance

from ..models import Identity, Image

from id_dedup.ml.pipeline import normalised_mean

if TYPE_CHECKING:
    import uuid

    from id_dedup.ml.pipeline import ClusterMember, ClusterResult


@dataclass
class IdentityMatch:
    identity_id: uuid.UUID  # avoids loading Identity objects unless needed
    display_name: str
    similarity: float  # cosine similarity in [0, 1]; higher = better
    matched_image_count: int  # total images stored for this identity
    image_url: str | None = None


@dataclass(frozen=True)
class ClusterProposal:
    """
    Proposed identity matches for a single cluster (or singleton).

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
    """Centroid of a cluster expressed as a unit vector."""
    if len(members) == 1:
        return members[0].embedding
    return normalised_mean(np.stack([m.embedding for m in members]))


def _query_candidates(
    centroid: np.ndarray,
    top_k: int,
    min_similarity: float,
    similarity_band: float,
) -> list[IdentityMatch]:
    """
    Find the top_k closest existing identities to a centroid.

    Queries Identity.centroid directly — O(M identities) with a DB-level LIMIT,
    not O(N images). Applies the similarity_band filter to drop alternatives
    that aren't competitive with the best match.
    """
    max_distance = 1.0 - min_similarity

    identity_rows = list(
        Identity.objects.filter(centroid__isnull=False)
        .annotate(distance=CosineDistance("centroid", centroid.tolist()))
        .filter(distance__lte=max_distance)
        .order_by("distance")[:top_k]
    )

    if not identity_rows:
        return []

    # Fetch one representative image URL per matched identity (bounded by top_k).
    # DISTINCT ON (identity_id) with matching ORDER BY picks the earliest image per identity
    # and avoids fetching the 512-dim embedding column entirely.
    identity_pks = [row.id for row in identity_rows]
    image_url_map: dict[uuid.UUID, str | None] = {
        iid: default_storage.url(path) if path else None
        for iid, path in Image.objects.filter(identity_id__in=identity_pks)
        .order_by("identity_id", "created_at")
        .distinct("identity_id")
        .values_list("identity_id", "source_image")
    }

    ranked = [
        IdentityMatch(
            identity_id=row.id,
            display_name=row.display_name,
            similarity=1.0 - float(row.distance),
            matched_image_count=row.image_count,
            image_url=image_url_map.get(row.id),
        )
        for row in identity_rows
    ]

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
    """
    Propose identity matches for an arbitrary set of ClusterMembers.

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
    """
    Stage 2 — match every confirmed cluster against existing DB identities.

    Call this once, after the user has finished editing the ClusterResult.
    Each group is queried as a unit (one DB call per cluster).
    Singletons are each queried individually.
    Returns proposals: groups first (ascending by label), then singletons.
    """
    proposals = [
        propose_for_members(members, top_k, min_similarity, similarity_band)
        for _label, members in sorted(result.groups.items())
    ]
    proposals.extend(
        propose_for_members([member], top_k, min_similarity, similarity_band) for member in result.singletons
    )

    return proposals
