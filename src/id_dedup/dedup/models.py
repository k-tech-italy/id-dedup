import uuid
from typing import override

import numpy as np
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from pgvector import django as djvector

from .pipeline import normalised_mean


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

    def update_centroid(self) -> None:
        # TODO: replace with pgvector Avg aggregate once the library exports it
        # publicly — will shift mean computation to the DB and eliminate the
        # O(N) embedding transfer. Tracked: pgvector/pgvector-python#Avg.
        embeddings = list(
            Image.objects.filter(identity=self).values_list("embedding", flat=True)
        )
        if not embeddings:
            self.centroid = None
            self.image_count = 0
        else:
            self.image_count = len(embeddings)
            self.centroid = normalised_mean(np.stack(embeddings)).tolist()
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


@receiver(post_delete, sender=Image)
def _refresh_centroid_on_image_delete(sender, instance, **kwargs):
    if instance.identity_id is None:
        return
    try:
        identity = Identity.objects.get(pk=instance.identity_id)
    except Identity.DoesNotExist:
        return  # identity was also deleted
    identity.update_centroid()
