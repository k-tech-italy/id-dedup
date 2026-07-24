from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_safe

from .models import ClusterReviewTicket


@require_safe
@login_required
def ticket_list(request: HttpRequest) -> HttpResponse:
    status = request.GET.get("status", "open")
    if status == "closed":
        tickets = ClusterReviewTicket.objects.closed().select_related("batch").order_by("-created_at")
    else:
        tickets = ClusterReviewTicket.objects.open().select_related("batch").order_by("-created_at")
    return render(request, "workflow/ticket_list.html", {"tickets": tickets, "status": status})
