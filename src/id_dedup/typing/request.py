from django.contrib.auth.models import User
from django.http import HttpRequest


class AuthenticatedHttpRequest(HttpRequest):
    """Type checked authenticated request with a `User`."""

    user: User
