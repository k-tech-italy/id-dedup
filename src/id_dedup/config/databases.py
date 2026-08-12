from urllib.parse import urlparse


def build_databases(database_url: str) -> dict:
    """Build the Django DATABASES setting from a postgres connection URL."""
    parsed = urlparse(database_url)
    return {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": parsed.path.lstrip("/"),
            "USER": parsed.username,
            "PASSWORD": parsed.password,
            "HOST": parsed.hostname,
            "PORT": parsed.port,
        },
    }
