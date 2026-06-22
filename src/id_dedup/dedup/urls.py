from django.urls import path

from . import views

app_name = "wizard"

urlpatterns = [
    path("upload/", views.upload, name="upload"),
    path("review/", views.review, name="review"),
    path("review/split/", views.split, name="split"),
    path("review/image/<path:path>", views.review_image, name="review_image"),
    path("review/save/", views.review_save, name="review_save"),
    path("adjudication/", views.adjudication, name="adjudication"),
    path("adjudication/next/", views.adjudication_next, name="adjudication_next"),
    path("adjudication/prev/", views.adjudication_prev, name="adjudication_prev"),
    path("adjudication/assign/", views.assign, name="assign"),
    path("adjudication/new-identity/", views.new_identity, name="new_identity"),
    path("adjudication/search/", views.search, name="search"),
    path("complete/", views.complete, name="complete"),
]
