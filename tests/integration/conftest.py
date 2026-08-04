import pytest


@pytest.fixture
def logged_in_client(client):
    """Return a test client logged in as a test user."""
    from django.contrib.auth.models import User

    User.objects.create_user(username="testuser", password="testpass123")
    client.login(username="testuser", password="testpass123")
    return client


@pytest.fixture
def batch():
    """Return an empty Batch"""
    from model_bakery import baker

    return baker.make_recipe("tests.batch")


@pytest.fixture
def open_ticket(batch):
    """Return an open ClusterReviewTicket in a fresh batch."""
    from model_bakery import baker

    return baker.make_recipe("tests.open_ticket", batch=batch)


@pytest.fixture
def closed_ticket(batch):
    """Return a closed ClusterReviewTicket in a fresh batch."""
    from model_bakery import baker

    return baker.make_recipe("tests.closed_ticket", batch=batch)


@pytest.fixture
def open_conversation():
    """Return a pending Conversation (no ended_at, no error_message)."""
    from model_bakery import baker

    return baker.make_recipe("tests.open_conversation")


@pytest.fixture
def completed_conversation():
    """Return a completed Conversation (ended_at set, no error_message)."""
    from model_bakery import baker

    return baker.make_recipe("tests.completed_conversation")


@pytest.fixture
def errored_conversation():
    """Return a Conversation with a non-empty error_message."""
    from model_bakery import baker

    return baker.make_recipe("tests.errored_conversation")
