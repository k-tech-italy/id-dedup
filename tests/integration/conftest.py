import pytest


@pytest.fixture
def user():
    """Return a test user with a known password."""
    from django.contrib.auth.models import User  # noqa: PLC0415
    from model_bakery import baker  # noqa: PLC0415

    user = baker.make(User, username="testuser")
    user.set_password("testpass123")
    user.save()
    return user


@pytest.fixture
def logged_in_client(client, user):
    """Return a test client logged in as `user`."""
    client.login(username=user.username, password="testpass123")
    return client
