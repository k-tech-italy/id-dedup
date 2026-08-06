from django.contrib import admin

from .models import OutboxMessage


@admin.register(OutboxMessage)
class OutboxMessageAdmin(admin.ModelAdmin):
    """Read-only view of the outbox so dead-lettered dispatches surface loudly."""

    list_display = ("task_name", "created_at", "attempts", "max_attempts", "dispatched_at", "dead_lettered_at")
    list_filter = ("task_name", "dispatched_at", "dead_lettered_at")
    readonly_fields = [f.name for f in OutboxMessage._meta.fields]  # noqa: SLF001
    search_fields = ("task_name", "last_error")
