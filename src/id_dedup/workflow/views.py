from typing import cast

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST, require_safe

from id_dedup.typing.request import AuthenticatedHttpRequest
from id_dedup.workflow import service
from id_dedup.workflow.models import ClusterReviewTicket, ClusterReviewTicketQuerySet


@require_safe
@login_required
def ticket_list(request: HttpRequest) -> HttpResponse:
    """Render the ticket list page filtered by open/closed status."""
    status = request.GET.get("status", "open")
    if status not in {"open", "closed"}:
        status = "open"

    tickets = cast(
        "ClusterReviewTicketQuerySet",
        ClusterReviewTicket.objects.select_related("batch").order_by("-created_at"),
    )
    tickets = tickets.closed() if status == "closed" else tickets.open()
    return render(request, "workflow/ticket_list.html", {"tickets": tickets, "status": status})


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
    """Close the ticket and dispatch the next-stage task for kept images."""
    ticket = get_object_or_404(ClusterReviewTicket.objects, pk=pk)
    if ticket.is_closed:
        reviewed_by = ticket.reviewed_by
        msg = f"Cluster {ticket.cluster_label} was already reviewed"
        if reviewed_by:
            msg += f" by {reviewed_by.username}"
        msg += "."
        messages.info(request, msg)
        return redirect("workflow:ticket_list")
    service.submit_ticket_review(ticket, user=request.user, kept_ids=request.POST.getlist("keep"))
    return redirect("workflow:ticket_list")
