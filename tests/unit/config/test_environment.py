import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import decouple
import pytest
from django.core.exceptions import ImproperlyConfigured

from id_dedup.config import environment
from id_dedup.config.environment import DEFAULTS, _Configuration, _EnvProxy


def _config(overrides: dict[str, str] | None = None) -> Callable[..., Any]:
    """Stub decouple.config: apply cast to defaults, raise when a var is missing."""

    def fake_config(
        variable: str,
        default: Any = decouple.undefined,
        cast: Any = decouple.undefined,
    ) -> Any:
        if default is not decouple.undefined:
            value = default
        elif overrides is not None and variable in overrides:
            value = overrides[variable]
        else:
            raise decouple.UndefinedValueError(f"option {variable} not found")
        if cast is not decouple.undefined:
            value = cast(value)
        return value

    return fake_config


def test_call_shortcuts_get(monkeypatch):
    monkeypatch.setattr(environment.decouple, "config", _config({"FOO": "bar"}))
    env = _EnvProxy()
    assert env("FOO") == env.get("FOO") == "bar"


def test_get_applies_cast(monkeypatch):
    monkeypatch.setattr(environment.decouple, "config", _config({"PORT": "5432"}))
    env = _EnvProxy(PORT=_Configuration(cast=int))
    assert env.get("PORT") == 5432


def test_get_returns_cast_default_when_unset(monkeypatch):
    monkeypatch.setattr(environment.decouple, "config", _config())
    env = _EnvProxy(OUTBOX_MAX_ATTEMPTS=_Configuration(cast=int, default="5"))
    assert env.get("OUTBOX_MAX_ATTEMPTS") == 5


def test_allowed_hosts_default_is_csv_list(monkeypatch):
    monkeypatch.setattr(environment.decouple, "config", _config())
    env = _EnvProxy(ALLOWED_HOSTS=_Configuration(cast=environment.decouple.Csv(), default="localhost,127.0.0.1"))
    assert env.get("ALLOWED_HOSTS") == ["localhost", "127.0.0.1"]


def test_missing_mandatory_var_raises_improperly_configured(monkeypatch):
    monkeypatch.setattr(environment.decouple, "config", _config())
    env = _EnvProxy(SECRET_KEY=_Configuration(cast=str))
    with pytest.raises(ImproperlyConfigured, match="SECRET_KEY"):
        env("SECRET_KEY")


def test_undefined_default_still_raises_improperly_configured(monkeypatch):
    monkeypatch.setattr(environment.decouple, "config", _config())
    env = _EnvProxy()
    with pytest.raises(ImproperlyConfigured):
        env("UNKNOWN_VAR")


def test_defaults_cover_settings_usages():
    settings_source = (Path(__file__).resolve().parents[3] / "src" / "id_dedup" / "config" / "settings.py").read_text()
    used_vars = set(re.findall(r'env\(\s*"([A-Z_]+)"', settings_source))
    assert used_vars <= set(DEFAULTS)
