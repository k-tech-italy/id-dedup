import pytest
from django.core.files import File
from django.utils import timezone


def _factory(model, **defaults):
    """Create and return a new ``model`` instance, merging ``defaults`` with per-call kwargs."""
    from model_bakery import baker  # noqa: PLC0415

    def _make(**kwargs):
        all_kwargs = defaults | kwargs
        return baker.make(model, **all_kwargs)

    return _make


@pytest.fixture
def batch_factory():
    from id_dedup.workflow.models import Batch  # noqa: PLC0415

    return _factory(Batch)


@pytest.fixture
def batch(batch_factory):
    return batch_factory()


@pytest.fixture
def image_factory(batch):
    from id_dedup.workflow.models import Image  # noqa: PLC0415

    return _factory(Image, batch=batch)


@pytest.fixture
def linked_image_factory(image_factory, cluster_review_ticket):
    def _make(tmp_path, name, ticket=cluster_review_ticket, **kwargs):
        path = tmp_path / name
        path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16)
        with path.open("rb") as f:
            return image_factory(batch=ticket.batch, cluster_ticket=ticket, source_image=File(f, name=name), **kwargs)

    return _make


@pytest.fixture
def cluster_review_ticket_factory(batch):
    from id_dedup.workflow.models import ClusterReviewTicket  # noqa: PLC0415

    return _factory(ClusterReviewTicket, cluster_label=0, batch=batch)


@pytest.fixture
def cluster_review_ticket(cluster_review_ticket_factory):
    return cluster_review_ticket_factory()


@pytest.fixture
def closed_cluster_review_ticket(cluster_review_ticket_factory):
    return cluster_review_ticket_factory(cluster_label=1, closed_at=timezone.now)


@pytest.fixture
def conversation_factory():
    from id_dedup.workflow.models import Conversation, Trigger  # noqa: PLC0415

    return _factory(Conversation, trigger=Trigger.UPLOAD)


@pytest.fixture
def user_factory():
    from django.contrib.auth.models import User  # noqa: PLC0415

    return _factory(User)


@pytest.fixture
def outbox_message_factory():
    from id_dedup.workflow.models import OutboxMessage  # noqa: PLC0415

    return _factory(
        OutboxMessage,
        task_name="id_dedup.workflow.tasks.process_batch",
        payload=dict,
    )
