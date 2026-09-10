"""Tests for the exposure of the interactive API documentation.

Swagger, ReDoc and ``/openapi.json`` are anonymous, and nginx proxies ``/api/``
onto the application root, so in a deployed stack they answer from outside. The
OpenAPI document carries ``info.version``, which ``GET /settings/version``
deliberately keeps behind a token — an open document would hand that version to
any caller and describe the whole API surface along with it.
"""

import pytest

from app.core import config as config_module
from app.core.config import api_docs_enabled
from app.main import app


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ("production", False),
        ("development", True),
        ("staging", True),
    ],
)
def test_docs_disabled_only_in_production(
    monkeypatch: pytest.MonkeyPatch, environment: str, expected: bool
) -> None:
    """Only the production environment switches the documentation off.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        environment: Value of ``TACITUS_ENVIRONMENT`` under test.
        expected: Whether the documentation is expected to be served.
    """
    monkeypatch.setattr(config_module.settings, "environment", environment)
    assert api_docs_enabled() is expected


def test_app_wires_every_documentation_url() -> None:
    """The application derives all three URLs from ``api_docs_enabled``.

    Tests run in the development environment, so all three must be set. Hiding
    the viewers while leaving ``openapi_url`` on would hide nothing: the
    document is what carries the version.
    """
    assert api_docs_enabled() is True
    assert app.docs_url == "/docs"
    assert app.redoc_url == "/redoc"
    assert app.openapi_url == "/openapi.json"
