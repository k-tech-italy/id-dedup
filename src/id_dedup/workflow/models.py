import uuid
from typing import override
import numpy as np
from django.conf import settings
from django.db import models
from django.db.models import Avg, Count
from django.db.models.signals import post_delete
from django.dispatch import receiver
from pgvector import django as djvector
from pgvector.django import HnswIndex


class Batch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    created_at = models.DateTimeField(auto_now_add=True)


class ConversationQuerySet(models.QuerySet):
    def pending(self):
        return self.filter(ended_at__isnull=True, error_message="")

    def completed(self):
        return self.filter(ended_at__isnull=False, error_message="")

    def errored(self):
        return self.exclude(error_message="")


class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name="conversations")
    parent = models.ForeignKey("self", null=True, on_delete=models.SET_NULL, related_name="children")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)

    class Trigger(models.TextChoices):
        UPLOAD = "upload"
        CLUSTER_REVIEW = "cluster_review"
        ADJUDICATION = "adjudication"

    trigger = models.CharField(max_length=20, choices=Trigger.choices)

    summary = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    objects = ConversationQuerySet.as_manager()


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
