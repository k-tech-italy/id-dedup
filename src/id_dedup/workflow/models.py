from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Self, override

import numpy as np
from django.conf import settings
from django.db import models
from django.db.models import Avg, Count
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone
from pgvector import django as djvector
from pgvector.django import HnswIndex

if TYPE_CHECKING:
    from django.contrib.auth.models import User


def _default_max_attempts() -> int:
    return getattr(settings, "OUTBOX_MAX_ATTEMPTS", 5)


class OutboxMessage(models.Model):
    """Durable record of a Celery dispatch that must not be lost."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    task_name = models.CharField(max_length=128)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default="")
    max_attempts = models.PositiveIntegerField(default=_default_max_attempts)
    dead_lettered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        """Index serving the sweep; a cap-less row is rejected by the check constraint."""

        indexes = [
            models.Index(
                fields=["dispatched_at", "dead_lettered_at", "attempts", "created_at"],
                name="outbox_pending_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(condition=models.Q(max_attempts__gte=1), name="outbox_max_attempts_positive"),
        ]

    @override
    def __str__(self) -> str:
        return str(self.id)


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

        indexes = [
            HnswIndex(name="workflow_identity_centroid_idx", fields=["centroid"], opclasses=["vector_cosine_ops"]),
        ]

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


class ClusterReviewTicketQuerySet(models.QuerySet):
    """Custom queryset providing convenience filters for ticket lifecycle states."""

    def open(self) -> Self:
        """Return the open (unclosed) tickets in this queryset."""
        return self.filter(closed_at__isnull=True)

    def closed(self) -> Self:
        """Return the closed tickets in this queryset."""
        return self.filter(closed_at__isnull=False)


ClusterReviewTicketManager = models.Manager.from_queryset(ClusterReviewTicketQuerySet)


class ClusterReviewTicket(models.Model):
    """Workflow ticket representing a single cluster awaiting user review and adjudication."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name="cluster_tickets")
    cluster_label = models.IntegerField()
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.PROTECT,
        related_name="reviewed_tickets",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    objects = ClusterReviewTicketManager()

    if TYPE_CHECKING:
        images: models.Manager[Image]

    @override
    def __str__(self) -> str:
        return f"Ticket {self.id} (cluster {self.cluster_label})"

    def close(self, user: User | None = None) -> None:
        """
        Mark the ticket as closed at the current timestamp.

        Persists the closing user via *reviewed_by* to preserve
        accountability even after the ticket is closed.

        Uses an atomic conditional ``UPDATE`` so concurrent callers
        never overwrite unrelated fields — the DB-level ``WHERE
        closed_at IS NULL`` guard makes the check-and-write atomic.
        """
        if self.is_closed:
            return

        # NOTE: this mitigates a race condition where two users are
        # trying to close the same ticket at the same time.
        ClusterReviewTicket.objects.filter(pk=self.pk, closed_at__isnull=True).update(
            closed_at=timezone.now(),
            reviewed_by=user if user is not None else self.reviewed_by,
        )

        self.refresh_from_db()

    @property
    def is_closed(self) -> bool:
        """Return whether the ticket has been closed."""
        return self.closed_at is not None


class Image(models.Model):
    """An uploaded image that may be assigned to an identity and stores a 512-d embedding."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    batch = models.ForeignKey(Batch, null=True, on_delete=models.SET_NULL, related_name="images")
    cluster_ticket = models.ForeignKey(ClusterReviewTicket, null=True, on_delete=models.SET_NULL, related_name="images")
    identity = models.ForeignKey(
        Identity,
        null=True,
        on_delete=models.SET_NULL,
        related_name="images",
    )
    embedding = djvector.VectorField(dimensions=512, null=True)
    source_image = models.FileField(upload_to="images")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """HNSW index on embedding for fast cosine-similarity lookups; source_image must be unique."""

        indexes = [HnswIndex(name="workflow_embedding_idx", fields=["embedding"], opclasses=["vector_cosine_ops"])]
        constraints = [
            models.UniqueConstraint(fields=["source_image"], name="image_source_image_unique"),
        ]

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
