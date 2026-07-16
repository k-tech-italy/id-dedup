import uuid
from typing import TYPE_CHECKING, override

import numpy as np
from django.db import models
from pgvector import django as djvector

from .pipeline import normalised_mean

if TYPE_CHECKING:
    from django.db.models.query import ValuesQuerySet


class Identity(models.Model):
    """
    Identity of a person.

    :param id: a UUID
    :param display_name: a human-readable name
    :param centroid: L2-normalised mean of all assigned image embeddings; null until first image is assigned.
    :param image_count: denormalised count of assigned images, updated alongside centroid.
    :param created_at:
    :param updated_at:
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    display_name = models.CharField(max_length=255, blank=True)
    centroid = djvector.VectorField(dimensions=512, null=True)
    image_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:  # noqa: D106
        indexes = [djvector.HnswIndex(name="identity_centroid_idx", fields=["centroid"], opclasses=["vector_cosine_ops"])]

    def update_centroid(self, embeddings: "ValuesQuerySet[Image, list[float]]") -> None:
        self.image_count = embeddings.count()
        self.centroid = normalised_mean(np.stack(list(embeddings))).tolist()
        self.save(update_fields=["centroid", "image_count", "updated_at"])

    @override
    def __str__(self):
        return self.display_name


class Image(models.Model):
    """
    An image related to an identity.

    :param id: a UUID
    :param identity: the related identity. Can be `None`.
    :param embedding: the computed face embedding used for deduplication.
    :param source_image: the image file proper.
    :param created_at:
    :param updated_at:
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    identity = models.ForeignKey(
        Identity,
        null=True,
        on_delete=models.SET_NULL,
        related_name="images",
    )  # null = unassigned
    embedding = djvector.VectorField(dimensions=512)  # pgvector
    source_image = models.FileField(upload_to="images")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:  # noqa: D106
        indexes = [djvector.HnswIndex(name="embedding_idx", fields=["embedding"], opclasses=["vector_cosine_ops"])]

    @override
    def __str__(self):
        return self.source_image.name
