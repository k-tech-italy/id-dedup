import os

# Test settings module — forced via `--ds tests.settings` (see pyproject.toml
# addopts) so tests never depend on `.env`/shell exports for these values.
# Seed SECRET_KEY before importing the app settings, which reads it via `env`.
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"

from id_dedup.config.settings import *  # noqa: F403

DEBUG = True
SECRET_KEY = "test-secret-key-not-for-production"
