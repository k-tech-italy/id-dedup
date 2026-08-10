import pytest
from django.utils import timezone


def _factory(recipe, **defaults):
    """Return a callable that bakes an instance of ``recipe`` with overridable kwargs."""

    def _make(**kwargs):
        return recipe.make(**defaults, **kwargs)

    return _make


def _recipe(model, **defaults):
    from model_bakery.recipe import Recipe

    return Recipe(model, **defaults)


@pytest.fixture
def batch():
    from id_dedup.workflow import models as workflow_models

    return _factory(_recipe(workflow_models.Batch))


@pytest.fixture
def cluster_review_ticket(batch):
    from id_dedup.workflow import models as workflow_models

    return _factory(
        _recipe(workflow_models.ClusterReviewTicket, cluster_label=0),
        batch=batch(),
    )


@pytest.fixture
def closed_cluster_review_ticket(batch):
    from id_dedup.workflow import models as workflow_models

    return _factory(
        _recipe(workflow_models.ClusterReviewTicket, cluster_label=1, closed_at=timezone.now),
        batch=batch(),
    )
