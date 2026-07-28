from django.urls import path

from . import views

app_name = "workflow"

urlpatterns = [
    path("tickets/", views.ticket_list, name="ticket_list"),
    path("tickets/<uuid:pk>/", views.ticket_detail, name="ticket_detail"),
    path("tickets/<uuid:pk>/submit/", views.submit_review, name="submit_review"),
]
