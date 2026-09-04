"""Tests that no compose service installs dependencies at container start.

``mediamtx-cleanup`` used to run ``pip install boto3`` on every start from a
bare ``python:3.14-slim``. That made the service unable to start without
reaching PyPI, and — while the version was unpinned — able to run different
code on two consecutive starts, in a loop that deletes local recordings after
uploading them.

It now runs on the application image, which already carries boto3 from the
build, so the SDK is pinned in exactly one place: the image.
"""

import re
from pathlib import Path

import yaml

COMPOSE_FILE = Path("docker-compose.yml")
APP_IMAGE = "tacitus-app:0.1.0"

# Any pip install, pinned or not. Nothing in compose should install at runtime.
_PIP_INSTALL = re.compile(r"pip\s+install\b")


def _compose_services() -> dict[str, dict]:
    """Parse the compose file and return its services.

    Returns:
        Mapping of service name to its definition.
    """
    parsed = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    return parsed["services"]


def test_no_pip_install_at_container_start() -> None:
    """No service installs Python packages when it starts.

    Dependencies belong in an image build, where they are resolved once and
    shipped as an artifact, not fetched again on every restart.

    Only ``command`` and ``entrypoint`` are inspected — searching the raw file
    would also match the comments that explain why this rule exists.
    """
    offenders = []
    for name, service in _compose_services().items():
        for key in ("command", "entrypoint"):
            value = service.get(key)
            if value is None:
                continue
            text = " ".join(value) if isinstance(value, list) else str(value)
            if _PIP_INSTALL.search(text):
                offenders.append(f"{name}.{key}")

    assert not offenders, (
        f"services install packages at container start: {offenders}; "
        "add them to the image build instead"
    )


def test_cleanup_runs_on_the_application_image() -> None:
    """The archive manager shares the application image.

    Sharing the image is what removes the runtime install: boto3 comes from the
    build, and the cleanup job talks to S3 through the same SDK as the app.
    """
    cleanup = _compose_services()["mediamtx-cleanup"]
    assert cleanup["image"] == APP_IMAGE, (
        f"mediamtx-cleanup runs on {cleanup['image']!r}; it needs {APP_IMAGE!r} "
        "to inherit boto3 from the image build"
    )
    assert "build" in cleanup, "mediamtx-cleanup must build the app image when it is missing"


def test_cleanup_disables_the_inherited_healthcheck() -> None:
    """The app image's HTTP healthcheck is switched off for the cleanup loop.

    That healthcheck probes uvicorn on port 8000. This container runs the
    archive loop instead, so an inherited probe could only ever fail and would
    park the service in ``unhealthy``.
    """
    cleanup = _compose_services()["mediamtx-cleanup"]
    healthcheck = cleanup.get("healthcheck", {})
    assert healthcheck.get("disable") is True, (
        "mediamtx-cleanup inherits the application HEALTHCHECK, which probes an "
        "HTTP server this container does not run"
    )
