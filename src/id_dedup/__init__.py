from .celery import app as celery_app

__author__ = "KTech"
__version__ = "0.1.0"
PACKAGE_NAME = "id-dedup"
VERSION = __version__
__all__ = ("celery_app",)
