from collections.abc import Callable
from typing import Any, NamedTuple, cast

import decouple
from django.core.exceptions import ImproperlyConfigured

type CastFn[T] = Callable[[Any], T]


class _Configuration[T](NamedTuple):
    """
    Configuration options for environment variable settings.

    :param cast: the `Callable` to apply for type conversion.
    :param default: the default value. Used if the variable is unset.
    """

    cast: CastFn[T] | decouple.Undefined = decouple.undefined
    default: T | decouple.Undefined = decouple.undefined


class _EnvProxy[T]:
    """
    An environment access proxy.

    Provides access to environment variables with automatic type
    conversion and default fallback values, so that the user does not
    have to always call `decouple.config()`.
    """

    def __init__(self, **defaults: _Configuration[T]) -> None:
        """Load the default configuration settings."""
        self._defaults = defaults

    def __call__(self, variable: str) -> T:
        """
        Return the given `variable`'s value, or the default if defined.

        Shortcut to `EnvProxy.get()`.
        """
        return self.get(variable)

    def get(self, variable: str) -> T:
        """Return the given `variable`'s value, or the default if defined."""
        item = self._defaults.get(variable) or _Configuration()
        try:
            return cast("T", decouple.config(variable, default=item.default, cast=item.cast))
        except decouple.UndefinedValueError as exc:
            raise ImproperlyConfigured(str(exc)) from exc


DEFAULTS = {
    # debug mode
    "DEBUG": (bool, False),
    # database
    "DATABASE_URL": (str, "postgresql://postgres:@127.0.0.1:5432/id_dedup"),
    # Redis
    "REDIS_URL": (str, "redis://localhost:6379/0"),
    "REDIS_RESULT_URL": (str, "redis://localhost:6379/1"),
    # security
    "SECRET_KEY": (str,),  # mandatory
    "ALLOWED_HOSTS": (decouple.Csv(), "localhost,127.0.0.1"),
    "SECURE_SSL_REDIRECT": (bool, False),
    "SECURE_HSTS_SECONDS": (int, 0),
    # outbox dispatch
    "OUTBOX_MAX_ATTEMPTS": (int, 5),
    "OUTBOX_SWEEP_SECONDS": (int, 60),
}


env = _EnvProxy(**{var: _Configuration(*settings) for var, settings in DEFAULTS.items()})
