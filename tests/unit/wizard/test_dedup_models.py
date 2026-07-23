from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import numpy as np

from tests.unit.wizard.helpers import unit_vector


def test_identity_update_centroid_sets_image_count():
    from id_dedup.dedup.models import Identity

    identity = Identity.__new__(Identity)
    emb = unit_vector(seed=42)

    with patch("id_dedup.dedup.models.Image") as MockImage, patch.object(Identity, "save"):
        MockImage.objects.filter.return_value.aggregate.return_value = {"avg_embedding": emb, "count": 1}
        identity.update_centroid()

    assert identity.image_count == 1


def test_identity_update_centroid_produces_unit_vector():
    from id_dedup.dedup.models import Identity

    identity = Identity.__new__(Identity)
    emb = unit_vector(seed=10)

    with patch("id_dedup.dedup.models.Image") as MockImage, patch.object(Identity, "save"):
        MockImage.objects.filter.return_value.aggregate.return_value = {"avg_embedding": emb, "count": 2}
        identity.update_centroid()

    assert abs(np.linalg.norm(np.array(identity.centroid)) - 1.0) < 1e-5


def test_identity_update_centroid_calls_save_with_correct_update_fields():
    from id_dedup.dedup.models import Identity

    identity = Identity.__new__(Identity)

    with patch("id_dedup.dedup.models.Image") as MockImage, patch.object(Identity, "save") as mock_save:
        MockImage.objects.filter.return_value.aggregate.return_value = {"avg_embedding": unit_vector(seed=0), "count": 1}
        identity.update_centroid()

    mock_save.assert_called_once()
    update_fields = mock_save.call_args[1]["update_fields"]
    assert "centroid" in update_fields
    assert "image_count" in update_fields
    assert "updated_at" in update_fields


def test_identity_update_centroid_zeros_out_when_no_images():
    from id_dedup.dedup.models import Identity

    identity = Identity.__new__(Identity)
    identity.centroid = [0.1] * 512
    identity.image_count = 3

    with patch("id_dedup.dedup.models.Image") as MockImage, patch.object(Identity, "save") as mock_save:
        MockImage.objects.filter.return_value.aggregate.return_value = {"avg_embedding": None, "count": 0}
        identity.update_centroid()

    assert identity.centroid is None
    assert identity.image_count == 0
    mock_save.assert_called_once()


def test_image_post_delete_refreshes_identity_centroid():
    from django.db.models.signals import post_delete

    from id_dedup.dedup.models import Identity, Image

    identity_id = uuid.uuid4()
    mock_identity = MagicMock()

    fake_image = MagicMock(spec=Image)
    fake_image.identity_id = identity_id

    with patch.object(Identity.objects, "get", return_value=mock_identity):
        post_delete.send(sender=Image, instance=fake_image, using="default")

    mock_identity.update_centroid.assert_called_once_with()


def test_image_post_delete_skips_when_identity_is_none():
    from django.db.models.signals import post_delete

    from id_dedup.dedup.models import Identity, Image

    fake_image = MagicMock(spec=Image)
    fake_image.identity_id = None

    with patch.object(Identity.objects, "get") as mock_get:
        post_delete.send(sender=Image, instance=fake_image, using="default")

    mock_get.assert_not_called()
