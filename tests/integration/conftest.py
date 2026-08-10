import pytest


@pytest.fixture
def logged_in_client(client):
    """Return a test client logged in as a test user."""
    from django.contrib.auth.models import User
    from model_bakery import baker

    user = baker.make(User, username="testuser")
    user.set_password("testpass123")
    user.save()

    client.login(username="testuser", password="testpass123")
    return client
