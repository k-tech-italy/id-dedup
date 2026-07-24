import pytest


@pytest.fixture
def logged_in_client(client):
    """Return a test client logged in as a test user."""
    from django.contrib.auth.models import User
    User.objects.create_user(username="testuser", password="testpass123")
    client.login(username="testuser", password="testpass123")
    return client
