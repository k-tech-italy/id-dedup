import pytest
from django.utils import timezone


def _factory(model, **defaults):
    """Create and return a new ``model`` instance, merging ``defaults`` with per-call kwargs."""
    from model_bakery import baker

    return baker.make(model, **defaults)


@pytest.fixture
def batch():
    from id_dedup.workflow import models as workflow_models

    return _factory(workflow_models.Batch)


@pytest.fixture
def cluster_review_ticket(batch):
    from id_dedup.workflow import models as workflow_models

    return _factory(workflow_models.ClusterReviewTicket, cluster_label=0, batch=batch)


@pytest.fixture
def closed_cluster_review_ticket(batch):
    from id_dedup.workflow import models as workflow_models

    return _factory(workflow_models.ClusterReviewTicket, cluster_label=1, closed_at=timezone.now, batch=batch)
