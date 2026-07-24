import uuid
from typing import Any, Self, override

import numpy as np
from django.conf import settings
from django.db import models
from django.db.models import Avg, Count
from django.db.models.signals import post_delete
from django.dispatch import receiver
from pgvector import django as djvector
from pgvector.django import HnswIndex


class Batch(models.Model):
    """A grouping of image uploads."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    created_at = models.DateTimeField(auto_now_add=True)

    @override
    def __str__(self) -> str:
        return str(self.id)


class ConversationQuerySet(models.QuerySet):
    """Custom queryset providing convenience filters for conversation lifecycle states."""

    def pending(self) -> Self:
        """Return the pending conversations in this queryset."""
        return self.filter(ended_at__isnull=True, error_message="")

    def completed(self) -> Self:
        """Return the successfully completed conversations in this queryset."""
        return self.filter(ended_at__isnull=False, error_message="")

    def errored(self) -> Self:
        """Return the conversations in this queryset that resulted in an error."""
        return self.exclude(error_message="")


ConversationManager = models.Manager.from_queryset(ConversationQuerySet)


class Trigger(models.TextChoices):
    """Enum of events that start a new conversation."""

    UPLOAD = "upload"
    CLUSTER_REVIEW = "cluster review"
    ADJUDICATION = "adjudication"


class Conversation(models.Model):
    """Tracks the lifecycle of an image batch."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name="conversations")
    parent = models.ForeignKey("self", null=True, on_delete=models.SET_NULL, related_name="children")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="conversations",
    )

    trigger = models.CharField(max_length=20, choices=Trigger.choices)

    summary = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    objects = ConversationManager()

    @override
    def __str__(self) -> str:
        return str(self.id)


class Identity(models.Model):
    """A person represented by a centroid vector computed from its associated images."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    centroid = djvector.VectorField(dimensions=512, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """HNSW index on centroid for fast cosine-similarity lookups."""

        indexes = [HnswIndex(name="wf_identity_centroid_idx", fields=["centroid"], opclasses=["vector_cosine_ops"])]

    @override
    def __str__(self) -> str:
        return str(self.id)

    def update_centroid(self) -> None:
        """Recompute this identity's centroid based on the available images."""
        result = Image.objects.filter(identity=self, embedding__isnull=False).aggregate(
            avg_embedding=Avg("embedding"),
            count=Count("id"),
        )
        avg = result["avg_embedding"]
        if avg is None:
            self.centroid = None
        else:
            arr = np.array(avg)
            self.centroid = (arr / np.linalg.norm(arr)).tolist()
        self.save(update_fields=["centroid", "updated_at"])


class Image(models.Model):
    """An uploaded image that may be assigned to an identity and stores a 512-d embedding."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    batch = models.ForeignKey(Batch, null=True, on_delete=models.SET_NULL, related_name="images")
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
        """HNSW index on embedding for fast cosine-similarity lookups."""

        indexes = [HnswIndex(name="wf_embedding_idx", fields=["embedding"], opclasses=["vector_cosine_ops"])]

    @override
    def __str__(self) -> str:
        return self.source_image.name


@receiver(post_delete, sender=Image)
def _refresh_centroid_on_image_delete(
    sender: type[Image],
    instance: Image,
    **kwargs: Any,  # noqa: ANN401 - kwargs required by Django signals even if unused
) -> None:
    """After an image is deleted, recompute the centroid for the identity it belonged to."""
    if instance.identity is None:
        return
    try:
        identity = Identity.objects.get(pk=instance.identity.pk)
    except Identity.DoesNotExist:
        return
    identity.update_centroid()
