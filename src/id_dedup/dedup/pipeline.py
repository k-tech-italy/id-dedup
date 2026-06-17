from __future__ import annotations

import pathlib
from dataclasses import dataclass, field

import cv2
import numpy as np
from insightface.app import FaceAnalysis
from sklearn.cluster import DBSCAN

# Lazy singleton — avoids downloading/loading the model at import time
_app: FaceAnalysis | None = None


def _get_app() -> FaceAnalysis:
    global _app
    if _app is None:
        _app = FaceAnalysis(providers=["CPUExecutionProvider"])
        _app.prepare(ctx_id=0, det_size=(640, 640))
    return _app


def extract_embedding(image_path: str | pathlib.Path) -> np.ndarray | None:
    """Return the 512-d face embedding for the most prominent face in the image, or None."""
    img = cv2.imread(str(image_path))
    if img is None:
        return None
    faces = _get_app().get(img)
    if not faces:
        return None
    face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
    return face.embedding  # shape (512,), not yet L2-normalised


@dataclass
class ClusterMember:
    file: pathlib.Path
    embedding: np.ndarray  # L2-normalised, matches Image.embedding / pgvector cosine index


@dataclass
class ClusterResult:
    """Stage-1 result: embeddings and DBSCAN groupings, no DB interaction.

    Edit clusters freely (via split()) before passing to services.propose_matches(),
    which is the only place DB queries are made.

    Keys: DBSCAN label (-1 = noise/singletons, 0+ = confirmed groups).
    After split(), new groups are assigned labels above the current maximum.
    """

    clusters: dict[int, list[ClusterMember]] = field(default_factory=dict)
    failed: list[pathlib.Path] = field(default_factory=list)

    @property
    def singletons(self) -> list[ClusterMember]:
        """Images DBSCAN could not group — no second face was close enough."""
        return self.clusters.get(-1, [])

    @property
    def groups(self) -> dict[int, list[ClusterMember]]:
        """All clusters except DBSCAN noise — each is a candidate Identity."""
        return {k: v for k, v in self.clusters.items() if k != -1}

    def split(self, label: int, move: set[pathlib.Path]) -> int:
        """Move a subset of files out of cluster `label` into a new cluster.

        Use this during stage-1 review when a cluster appears to contain more
        than one person. The split is pure Python — no embeddings are recomputed
        and no DB queries are made.

        If `move` contains a single file it is appended to the -1 (singletons)
        bucket; two or more files are assigned a new positive label. Either way
        both groups receive individual ClusterProposals at stage 2.

        Returns the label assigned to the split-off group (-1 or a new positive int).
        Raises ValueError if `label` doesn't exist or none of `move` are in it.
        """
        if label not in self.clusters:
            raise ValueError(f"No cluster with label {label}")

        source = self.clusters[label]
        moving = [m for m in source if m.file in move]
        if not moving:
            raise ValueError(f"None of the specified files are in cluster {label}")

        remaining = [m for m in source if m.file not in move]

        # Compute new_label before mutating clusters so a full-empty split
        # doesn't recycle the just-deleted label as the new one.
        new_label = max((k for k in self.clusters if k >= 0), default=-1) + 1

        if remaining:
            self.clusters[label] = remaining
        else:
            del self.clusters[label]

        if len(moving) == 1:
            self.clusters.setdefault(-1, []).append(moving[0])
            return -1

        self.clusters[new_label] = moving
        return new_label


def cluster_dbscan(
    embeddings: np.ndarray,
    eps: float = 0.4,
    min_samples: int = 2,
) -> tuple[np.ndarray, np.ndarray]:
    """L2-normalise embeddings then run DBSCAN with cosine metric.

    Returns (labels, normalised_embeddings). normalised_embeddings are
    ready to store directly in Image.embedding (pgvector cosine index).
    """
    normalized = embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)
    labels = DBSCAN(
        eps=eps,
        min_samples=min_samples,
        metric="cosine",
        algorithm="brute",
        n_jobs=-1,
    ).fit_predict(normalized)
    return labels, normalized


def process_images(
    image_paths: list[pathlib.Path],
    eps: float = 0.4,
    min_samples: int = 2,
) -> ClusterResult:
    """Stage 1 — extract embeddings and group by face similarity. No DB access.

    Call ClusterResult.split() to adjust groupings before moving to stage 2.
    Pass the finalised ClusterResult to services.propose_matches() to run DB matching.
    """
    raw_pairs = [(f, extract_embedding(f)) for f in image_paths]
    valid_pairs = [(f, emb) for f, emb in raw_pairs if emb is not None]
    failed = [f for f, emb in raw_pairs if emb is None]

    result = ClusterResult(failed=failed)

    if not valid_pairs:
        return result

    valid_files, raw_embeddings = zip(*valid_pairs)
    embeddings = np.stack(raw_embeddings)

    labels, normalized = cluster_dbscan(embeddings, eps=eps, min_samples=min_samples)

    for file, emb, label in zip(valid_files, normalized, labels):
        result.clusters.setdefault(label, []).append(ClusterMember(file=file, embedding=emb))

    return result
