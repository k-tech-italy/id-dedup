from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import numpy as np


def chainable_qs(rows: list) -> MagicMock:
    """A MagicMock queryset that chains ORM calls and iterates over `rows`."""
    qs = MagicMock()
    for method in ("filter", "annotate", "select_related", "order_by"):
        getattr(qs, method).return_value = qs
    qs.__iter__ = MagicMock(return_value=iter(rows))
    qs.__getitem__.side_effect = lambda s: rows[: s.stop] if isinstance(s, slice) else rows[s]
    return qs


def mock_identity_row(
    identity_id: uuid.UUID,
    display_name: str,
    distance: float,
    image_count: int = 1,
) -> MagicMock:
    """Minimal stand-in for an Identity ORM row annotated with CosineDistance."""
    row = MagicMock()
    row.id = identity_id
    row.display_name = display_name
    row.distance = distance
    row.image_count = image_count
    return row


def unit_vector(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.random(512).astype(np.float32)
    return v / np.linalg.norm(v)
