from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


def home(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return render(request, "dashboard.html")
    return render(request, "about.html")
