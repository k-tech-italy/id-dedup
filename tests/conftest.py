from __future__ import annotations

import os
import pathlib
from urllib.parse import urlparse

import django
import numpy as np
import pytest
from django.conf import settings

from id_dedup.ml.pipeline import ClusterMember, ClusterResult, process_images

FACES_DIR = pathlib.Path(__file__).parent / "examples"
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def pytest_configure(config):
    if not settings.configured:
        parsed = urlparse(os.environ["DATABASE_URL"])
        settings.configure(
            DATABASES={
                "default": {
                    "ENGINE": "django.db.backends.postgresql",
                    "NAME": parsed.path.lstrip("/"),
                    "USER": parsed.username,
                    "PASSWORD": parsed.password,
                    "HOST": parsed.hostname,
                    "PORT": parsed.port,
                },
            },
            SECRET_KEY="test-secret-key-not-for-production",
            STATIC_URL="/static/",
            INSTALLED_APPS=[
                "django.contrib.admin",
                "django.contrib.contenttypes",
                "django.contrib.auth",
                "django.contrib.sessions",
                "django.contrib.messages",
                "django.contrib.staticfiles",
                "id_dedup.dedup",
                "id_dedup.workflow",
            ],
            MIDDLEWARE=[
                "django.contrib.sessions.middleware.SessionMiddleware",
                "django.contrib.auth.middleware.AuthenticationMiddleware",
                "django.contrib.messages.middleware.MessageMiddleware",
            ],
            DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
            ROOT_URLCONF="id_dedup.urls",
            TEMPLATES=[
                {
                    "BACKEND": "django.template.backends.django.DjangoTemplates",
                    "DIRS": [pathlib.Path(__file__).parent.parent / "src" / "id_dedup" / "templates"],
                    "APP_DIRS": True,
                    "OPTIONS": {
                        "context_processors": [
                            "django.template.context_processors.request",
                            "django.contrib.auth.context_processors.auth",
                            "django.contrib.messages.context_processors.messages",
                        ],
                    },
                },
            ],
            SESSION_ENGINE="django.contrib.sessions.backends.db",
            LOGIN_URL="/accounts/login/",
            LOGIN_REDIRECT_URL="/",
            LOGOUT_REDIRECT_URL="/",
        )
        django.setup()


def _all_image_paths() -> list[pathlib.Path]:
    if not FACES_DIR.exists():
        return []
    return sorted(p for p in FACES_DIR.rglob("*") if p.suffix.lower() in _IMAGE_SUFFIXES)


def _person_dirs() -> list[pathlib.Path]:
    if not FACES_DIR.exists():
        return []
    return sorted(p for p in FACES_DIR.iterdir() if p.is_dir())


# ---------------------------------------------------------------------------
# Image-level fixture — one test invocation per image file
# ---------------------------------------------------------------------------


@pytest.fixture(
    params=_all_image_paths(),
    ids=lambda p: p.relative_to(FACES_DIR).as_posix(),
)
def image_path(request) -> pathlib.Path:
    return request.param


# ---------------------------------------------------------------------------
# Person-level fixtures — one test invocation per person directory
# ---------------------------------------------------------------------------


@pytest.fixture(
    params=_person_dirs(),
    ids=lambda p: p.name,
)
def person_dir(request) -> pathlib.Path:
    return request.param


@pytest.fixture
def person_images(person_dir) -> list[pathlib.Path]:
    return sorted(p for p in person_dir.iterdir() if p.suffix.lower() in _IMAGE_SUFFIXES)


# ---------------------------------------------------------------------------
# Session-scoped cluster result — computed once across the whole test run
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def all_image_paths() -> list[pathlib.Path]:
    return _all_image_paths()


@pytest.fixture(scope="session")
def cluster_result(all_image_paths) -> ClusterResult:
    """Full pipeline run over all example images. Expensive — session-scoped."""
    return process_images(all_image_paths)


# ---------------------------------------------------------------------------
# Synthetic ClusterResult for split() unit tests (no real images needed)
# ---------------------------------------------------------------------------


@pytest.fixture
def splittable_result() -> ClusterResult:
    """Two synthetic groups of known size for exercising ClusterResult.split()."""
    rng = np.random.default_rng(42)

    def _members(person: int, count: int) -> list[ClusterMember]:
        return [
            ClusterMember(
                file=pathlib.Path(f"synthetic/person{person}/photo{i}.jpg"),
                embedding=rng.random(512).astype(np.float32),
            )
            for i in range(count)
        ]

    result = ClusterResult()
    result.clusters[0] = _members(person=0, count=3)
    result.clusters[1] = _members(person=1, count=2)
    return result
