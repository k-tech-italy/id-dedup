from __future__ import annotations

import pathlib

import numpy as np
import pytest

from id_dedup.dedup.pipeline import extract_embedding, process_images


# ---------------------------------------------------------------------------
# extract_embedding
# ---------------------------------------------------------------------------

def test_extract_embedding_returns_512d_array(image_path):
    emb = extract_embedding(image_path)
    assert emb is not None
    assert emb.shape == (512,)


def test_extract_embedding_values_are_finite(image_path):
    emb = extract_embedding(image_path)
    assert emb is not None
    assert np.all(np.isfinite(emb))


def test_extract_embedding_missing_file_returns_none():
    assert extract_embedding(pathlib.Path("nonexistent.jpg")) is None


# ---------------------------------------------------------------------------
# process_images
# ---------------------------------------------------------------------------

def test_same_person_images_in_one_cluster(person_images, cluster_result):
    """All photos of the same person must land in the same cluster label."""
    if len(person_images) < 2:
        pytest.skip("need at least 2 photos per person to test grouping")

    file_to_label = {
        m.file: label
        for label, members in cluster_result.clusters.items()
        for m in members
    }
    labels = {file_to_label[p] for p in person_images if p in file_to_label}
    assert len(labels) == 1, f"Photos of one person spread across clusters {labels}"


def test_different_people_in_different_clusters(cluster_result):
    """No two people's images may share a cluster label."""
    person_to_labels: dict[str, set[int]] = {}
    for label, members in cluster_result.clusters.items():
        for m in members:
            person = m.file.parent.name
            person_to_labels.setdefault(person, set()).add(label)

    persons = list(person_to_labels.items())
    for i, (name_a, labels_a) in enumerate(persons):
        for name_b, labels_b in persons[i + 1:]:
            shared = labels_a & labels_b
            assert not shared, f"{name_a} and {name_b} share cluster(s) {shared}"


def test_all_valid_images_accounted_for(all_image_paths, cluster_result):
    """Every input image appears in either a cluster or the failed list."""
    cluster_files = {m.file for members in cluster_result.clusters.values() for m in members}
    assert cluster_files | set(cluster_result.failed) == set(all_image_paths)


def test_failed_images_not_in_clusters(all_image_paths):
    bad = pathlib.Path("nonexistent.jpg")
    result = process_images([*all_image_paths, bad])
    cluster_files = {m.file for members in result.clusters.values() for m in members}
    assert bad in result.failed
    assert bad not in cluster_files


def test_empty_input_returns_empty_result():
    result = process_images([])
    assert result.clusters == {}
    assert result.failed == []


def test_embeddings_are_l2_normalised(cluster_result):
    """Stored embeddings must be unit vectors — required for pgvector cosine index."""
    for members in cluster_result.clusters.values():
        for m in members:
            norm = float(np.linalg.norm(m.embedding))
            assert abs(norm - 1.0) < 1e-5, f"{m.file} embedding norm is {norm}"


# ---------------------------------------------------------------------------
# ClusterResult.split
# ---------------------------------------------------------------------------

def test_split_single_file_joins_singletons(splittable_result):
    target = splittable_result.clusters[0][0].file
    new_label = splittable_result.split(0, {target})
    assert new_label == -1
    assert any(m.file == target for m in splittable_result.clusters[-1])


def test_split_multiple_files_gets_new_positive_label(splittable_result):
    files = {m.file for m in splittable_result.clusters[0][:2]}
    new_label = splittable_result.split(0, files)
    assert new_label > 1
    assert len(splittable_result.clusters[new_label]) == 2


def test_split_remainder_stays_in_source_cluster(splittable_result):
    source = list(splittable_result.clusters[0])
    splittable_result.split(0, {source[0].file})
    remaining_files = {m.file for m in splittable_result.clusters[0]}
    assert remaining_files == {m.file for m in source[1:]}


def test_split_fully_emptied_source_is_removed(splittable_result):
    all_files = {m.file for m in splittable_result.clusters[1]}
    new_label = splittable_result.split(1, all_files)
    assert 1 not in splittable_result.clusters
    assert new_label in splittable_result.clusters


def test_split_new_label_never_recycles_deleted_label(splittable_result):
    """new_label must be computed before source deletion to avoid reuse."""
    all_files = {m.file for m in splittable_result.clusters[1]}
    new_label = splittable_result.split(1, all_files)
    assert new_label >= 2


def test_split_nonexistent_label_raises(splittable_result):
    with pytest.raises(ValueError, match="No cluster with label"):
        splittable_result.split(99, {pathlib.Path("any.jpg")})


def test_split_no_matching_files_raises(splittable_result):
    with pytest.raises(ValueError, match="None of the specified files"):
        splittable_result.split(0, {pathlib.Path("ghost.jpg")})


def test_split_single_appends_to_existing_singletons(splittable_result):
    """Second single-file split must append to the -1 bucket, not replace it."""
    first = splittable_result.clusters[0][0].file
    second = splittable_result.clusters[1][0].file
    splittable_result.split(0, {first})
    splittable_result.split(1, {second})
    singleton_files = {m.file for m in splittable_result.clusters[-1]}
    assert first in singleton_files
    assert second in singleton_files


def test_split_does_not_recompute_embeddings(splittable_result):
    """Split is a pointer move — embedding arrays must be the same objects."""
    original = splittable_result.clusters[0][0]
    splittable_result.split(0, {original.file})
    moved = next(m for m in splittable_result.clusters[-1] if m.file == original.file)
    assert moved.embedding is original.embedding
