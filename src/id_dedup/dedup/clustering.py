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


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


@dataclass
class ClusterMember:
    file: pathlib.Path
    embedding: np.ndarray  # L2-normalised, matches Image.embedding / pgvector cosine index


@dataclass
class ClusterResult:
    # Keys: DBSCAN label (-1 = singletons/noise, 0+ = groups)
    clusters: dict[int, list[ClusterMember]] = field(default_factory=dict)
    failed: list[pathlib.Path] = field(default_factory=list)

    @property
    def singletons(self) -> list[ClusterMember]:
        """Images where no second face was close enough to form a group."""
        return self.clusters.get(-1, [])

    @property
    def groups(self) -> dict[int, list[ClusterMember]]:
        """Confirmed clusters — each likely represents one Identity."""
        return {k: v for k, v in self.clusters.items() if k != -1}


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
    """Extract embeddings for a batch of images and cluster them.

    Each group in the result is a candidate for a single Identity record.
    Singletons and failed images still have embeddings/files available
    for manual review or later re-clustering.
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
