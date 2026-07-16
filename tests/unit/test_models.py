from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from tests.unit.helpers import unit_vector


def _mock_embedding_qs(embeddings: list) -> MagicMock:
    qs = MagicMock()
    qs.count.return_value = len(embeddings)
    qs.__iter__ = lambda self: iter(embeddings)
    return qs


def test_identity_update_centroid_sets_image_count():
    from id_dedup.dedup.models import Identity

    identity = Identity.__new__(Identity)
    emb = unit_vector(seed=42)

    with patch.object(Identity, "save"):
        identity.update_centroid(_mock_embedding_qs([emb]))

    assert identity.image_count == 1


def test_identity_update_centroid_produces_unit_vector():
    from id_dedup.dedup.models import Identity

    identity = Identity.__new__(Identity)
    emb1, emb2 = unit_vector(seed=10), unit_vector(seed=11)

    with patch.object(Identity, "save"):
        identity.update_centroid(_mock_embedding_qs([emb1, emb2]))

    assert abs(np.linalg.norm(np.array(identity.centroid)) - 1.0) < 1e-5


def test_identity_update_centroid_calls_save_with_correct_update_fields():
    from id_dedup.dedup.models import Identity

    identity = Identity.__new__(Identity)

    with patch.object(Identity, "save") as mock_save:
        identity.update_centroid(_mock_embedding_qs([unit_vector(seed=0)]))

    mock_save.assert_called_once()
    update_fields = mock_save.call_args[1]["update_fields"]
    assert "centroid" in update_fields
    assert "image_count" in update_fields
    assert "updated_at" in update_fields
