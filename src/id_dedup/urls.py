from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from id_dedup.views import home

urlpatterns = [
    path("", home, name="home"),
    path("admin/", admin.site.urls),
    path("accounts/login/", auth_views.LoginView.as_view(redirect_authenticated_user=True), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("wizard/", include("id_dedup.dedup.urls")),
    path("workflow/", include("id_dedup.workflow.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
