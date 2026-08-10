from typing import cast

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.decorators.http import require_POST, require_safe

from id_dedup.typing.request import AuthenticatedHttpRequest
from id_dedup.workflow import service
from id_dedup.workflow.models import ClusterReviewTicket, ClusterReviewTicketQuerySet, TicketAlreadyClosed


class UploadView(LoginRequiredMixin, View):
    """Render the upload form (GET) and register uploads for async processing (POST)."""

    def get(self, request: AuthenticatedHttpRequest) -> HttpResponse:
        """Render the upload form."""
        return render(request, "workflow/upload.html")

    def post(self, request: AuthenticatedHttpRequest) -> HttpResponse:
        """Register uploaded images for async processing."""
        files = request.FILES.getlist("images")
        try:
            batch = service.register_upload(files, user_id=request.user.pk)
        except service.EmptyBatch as exc:
            return render(request, "workflow/upload.html", {"error": str(exc)})
        if batch.skipped_files:
            n = len(batch.skipped_files)
            messages.warning(
                request,
                f"Skipped {n} file{'s' if n != 1 else ''} — not supported image types.",
            )
        messages.success(request, "Upload received — clustering will start shortly.")
        return redirect("home")


@require_safe
@login_required
def ticket_list(request: HttpRequest) -> HttpResponse:
    """Render the ticket list page filtered by open/closed status."""
    status = request.GET.get("status", "open")
    page = request.GET.get("page", "1")
    page_size = request.GET.get("page_size", "10")
    if status not in {"open", "closed", "all"}:
        status = "open"
    if page_size not in {"10", "20"}:
        page_size = "10"

    tickets = cast(
        "ClusterReviewTicketQuerySet",
        ClusterReviewTicket.objects.select_related("batch").order_by("-created_at"),
    )
    if status == "closed":
        tickets = tickets.closed()
    elif status == "open":
        tickets = tickets.open()
    else:
        tickets = tickets.all()

    paginator = Paginator(tickets, page_size)
    page_obj = paginator.get_page(page)
    page_range = list(paginator.get_elided_page_range(page_obj.number, on_each_side=1, on_ends=1))
    return render(
        request,
        "workflow/ticket_list.html",
        {
            "tickets": page_obj,
            "page_obj": page_obj,
            "page_range": page_range,
            "status": status,
            "page_size": page_size,
        },
    )


@require_safe
@login_required
def ticket_detail(request: HttpRequest, pk: str) -> HttpResponse:
    """Render the detail page for a single cluster review ticket."""
    ticket = get_object_or_404(
        ClusterReviewTicket.objects.select_related("batch").prefetch_related("images"),
        pk=pk,
    )
    return render(
        request,
        "workflow/ticket_detail.html",
        {"ticket": ticket, "kept_ids": service.get_kept_image_ids(ticket)},
    )


@require_POST
@login_required
def submit_review(request: AuthenticatedHttpRequest, pk: str) -> HttpResponse:
    """Close the ticket and write the durable outbox dispatch for kept images."""
    ticket = get_object_or_404(ClusterReviewTicket.objects, pk=pk)
    if ticket.is_closed:
        reviewed_by = ticket.reviewed_by
        msg = f"Cluster {ticket.cluster_label} was already reviewed"
        if reviewed_by:
            msg += f" by {reviewed_by.username}"
        msg += "."
        messages.info(request, msg)
        return redirect("workflow:ticket_list")
    try:
        service.submit_ticket_review(ticket, user=request.user, kept_ids=request.POST.getlist("keep"))
    except TicketAlreadyClosed as exc:
        messages.info(request, str(exc))
    return redirect("workflow:ticket_list")
