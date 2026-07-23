import pytest
from django.contrib.auth.models import User


@pytest.fixture
def logged_in_client(client):
    """Return a test client logged in as a test user."""
    User.objects.create_user(username="testuser", password="testpass123")
    client.login(username="testuser", password="testpass123")
    return client
