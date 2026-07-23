from django.urls import path

from id_dedup.workflow import views

app_name = "workflow"

urlpatterns = [
    path("", views.index, name="index"),
]
