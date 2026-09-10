"""Tests that the application version is declared once and copied faithfully.

``app.__version__`` is what the process reports — to Sentry as the release, to
the UI through ``GET /settings/version`` and to OpenAPI. Around it sit seven
copies: packaging metadata, the frontend package files, the image tags in
compose, the deployment variable and the documented tag examples. Nothing at
runtime reads those copies, so a forgotten one does not break anything
immediately: it just makes the deployment lie about which build it runs.

The declaration table lives in ``scripts/bump_version.py``; this test reads it
from there, so adding a new place to the table makes it both bumped and
checked.
"""

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

from app import __version__

_ROOT = Path(__file__).resolve().parent.parent


def _load_bump_version() -> ModuleType:
    """Load ``scripts/bump_version.py`` as a module.

    Returns:
        The imported module.
    """
    spec = importlib.util.spec_from_file_location(
        "bump_version", _ROOT / "scripts" / "bump_version.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bump_version = _load_bump_version()


@pytest.mark.parametrize("path", [path for path, _ in bump_version.DECLARATIONS])
def test_declared_version_matches_the_runtime_version(path: str) -> None:
    """Every file declaring the version declares the running one."""
    versions = bump_version.declared_versions(_ROOT)[path]
    assert versions == {__version__}, (
        f"{path} declares {sorted(versions)}, but the application is "
        f"{__version__}. Run: python scripts/bump_version.py {__version__}"
    )


def test_every_declaration_is_reachable() -> None:
    """No entry in the table points at a file that stopped declaring it.

    ``declared_versions`` raises when a pattern matches nothing; calling it
    over the whole table turns a silently stale entry into a failure.
    """
    assert set(bump_version.declared_versions(_ROOT)) == {
        path for path, _ in bump_version.DECLARATIONS
    }


def test_compose_declares_the_version_for_every_locally_built_image() -> None:
    """Each image built from this repository is tagged with the version.

    The declaration table matches image tags by name, so an image added to
    compose under a new ``tacitus-*`` name would not be covered by it. This
    reads the compose file directly instead.
    """
    text = (_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    tags = re.findall(r"(?m)^\s*image:\s*(tacitus-[\w-]+):(\S+)$", text)
    assert tags, "no locally built images found in docker-compose.yml"
    mistagged = {f"{name}:{tag}" for name, tag in tags if tag != __version__}
    assert not mistagged, (
        f"images tagged with a version other than {__version__}: {sorted(mistagged)}"
    )
