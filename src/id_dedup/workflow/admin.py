from django.contrib import admin

from id_dedup.workflow.models import Batch, ClusterReviewTicket, Conversation, Identity, Image, OutboxMessage


@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    """Admin interface for Batch model."""

    list_display = ("id", "created_at")


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    """Admin interface for Conversation model."""

    list_display = ("id", "user", "trigger", "created_at", "ended_at")
    list_filter = ("trigger",)


@admin.register(Identity)
class IdentityAdmin(admin.ModelAdmin):
    """Admin interface for Identity model."""

    readonly_fields = ("centroid",)
    list_display = ("id", "created_at")


@admin.register(ClusterReviewTicket)
class ClusterReviewTicketAdmin(admin.ModelAdmin):
    """Admin interface for ClusterReviewTicket model."""

    list_display = ("id", "batch", "cluster_label", "reviewed_by", "created_at", "closed_at")
    list_filter = ("batch", "closed_at")


@admin.register(Image)
class ImageAdmin(admin.ModelAdmin):
    """Admin interface for Image model."""

    readonly_fields = ("embedding",)
    list_display = ("id", "source_image", "cluster_ticket", "identity", "created_at")
    list_filter = ("identity",)


@admin.register(OutboxMessage)
class OutboxMessageAdmin(admin.ModelAdmin):
    """Read-only view of the outbox so dead-lettered dispatches surface loudly."""

    list_display = ("task_name", "created_at", "attempts", "max_attempts", "dispatched_at", "dead_lettered_at")
    list_filter = ("task_name", "dispatched_at", "dead_lettered_at")
    readonly_fields = [f.name for f in OutboxMessage._meta.fields]  # noqa: SLF001
    search_fields = ("task_name", "last_error")
