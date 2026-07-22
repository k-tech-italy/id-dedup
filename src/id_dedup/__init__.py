from .celery import app as celery_app

__author__ = "KTech"
__version__ = "0.1.0"
__name__ = "id-dedup"
VERSION = __version__
NAME = __name__
__all__ = ("celery_app",)
