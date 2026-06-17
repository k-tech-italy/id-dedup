from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np


def chainable_qs(rows: list) -> MagicMock:
    """A MagicMock queryset that chains ORM calls and iterates over `rows`."""
    qs = MagicMock()
    for method in ("filter", "annotate", "select_related", "order_by"):
        getattr(qs, method).return_value = qs
    qs.__iter__ = MagicMock(return_value=iter(rows))
    return qs


def mock_image_row(identity_id: int, display_name: str, distance: float) -> MagicMock:
    """Minimal stand-in for an Image ORM row annotated with CosineDistance."""
    img = MagicMock()
    img.identity_id = identity_id
    img.distance = distance
    img.identity.display_name = display_name
    return img


def unit_vector(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.random(512).astype(np.float32)
    return v / np.linalg.norm(v)