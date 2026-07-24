from django.urls import path

from . import views

app_name = "workflow"

urlpatterns = [
    path("tickets/", views.ticket_list, name="ticket_list"),
]
