import uuid
from typing import override
import numpy as np
from django.db import models
from django.db.models import Avg, Count
from django.db.models.signals import post_delete
from django.dispatch import receiver
from pgvector import django as djvector
from pgvector.django import HnswIndex


class Identity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    centroid = djvector.VectorField(dimensions=512, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [HnswIndex(name="wf_identity_centroid_idx", fields=["centroid"], opclasses=["vector_cosine_ops"])]

    def update_centroid(self) -> None:
        result = Image.objects.filter(identity=self, embedding__isnull=False).aggregate(
            avg_embedding=Avg("embedding"),
            count=Count("id"),
        )
        avg, count = result["avg_embedding"], result["count"]
        if avg is None:
            self.centroid = None
        else:
            arr = np.array(avg)
            self.centroid = (arr / np.linalg.norm(arr)).tolist()
        self.save(update_fields=["centroid", "updated_at"])

    @override
    def __str__(self):
        return str(self.id)


class Image(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    identity = models.ForeignKey(
        Identity,
        null=True,
        on_delete=models.SET_NULL,
        related_name="images",
    )
    embedding = djvector.VectorField(dimensions=512, null=True)
    source_image = models.FileField(upload_to="images")
    discarded = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [HnswIndex(name="wf_embedding_idx", fields=["embedding"], opclasses=["vector_cosine_ops"])]

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
        return
    identity.update_centroid()
