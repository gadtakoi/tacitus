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

from app import __version__

COMPOSE_FILE = Path("docker-compose.yml")
# The tag of a locally built image is the application version; keeping the
# literal here made this file one more place to remember on a version bump.
# ``tests/test_version_consistency.py`` guards the compose tags themselves.
APP_IMAGE = f"tacitus-app:{__version__}"

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


# Images built from this repository — their tag is the app version, not an
# upstream release, so they are checked separately from third-party images.
LOCAL_IMAGES = ("tacitus-app", "tacitus-nginx")

# A pinned upstream tag names an exact release: at least two numeric components
# (18.6-alpine, 3.14.5-slim, 1.27.5-alpine) or a dated release string
# (RELEASE.2025-04-22T22-12-26Z). A single component — 16-alpine, 7-alpine,
# 22-alpine — is a moving target that silently changes under `docker pull`.
_EXACT_VERSION = re.compile(r"\d+\.\d+|RELEASE\.\d{4}-\d{2}-\d{2}")


def _image_references() -> dict[str, str]:
    """Collect every image reference used by the stack.

    Returns:
        Mapping of source location to image reference.
    """
    references = {}
    for name, service in _compose_services().items():
        if "image" in service:
            references[f"compose:{name}"] = service["image"]

    for dockerfile in (Path("Dockerfile"), Path("nginx/Dockerfile")):
        for line in dockerfile.read_text(encoding="utf-8").splitlines():
            match = re.match(r"FROM\s+(\S+)", line.strip())
            if match:
                references[f"{dockerfile}:{match.group(1)}"] = match.group(1)
    return references


def test_no_image_uses_the_latest_tag() -> None:
    """Nothing runs on ``latest`` or on an implicit tag."""
    offenders = [
        f"{where} -> {image}"
        for where, image in _image_references().items()
        if image.endswith(":latest") or ":" not in image
    ]
    assert not offenders, f"images without an explicit tag: {offenders}"


def test_upstream_images_are_pinned_to_an_exact_release() -> None:
    """Third-party images name a full version, not a moving major tag.

    ``postgres:18-alpine`` is not ``latest``, but it still moves: a rebuild
    months later brings a different PostgreSQL patch release with no change in
    the repository.
    """
    offenders = []
    for where, image in _image_references().items():
        name, _, tag = image.partition(":")
        if name in LOCAL_IMAGES or name.startswith("tacitus"):
            continue
        if not _EXACT_VERSION.search(tag):
            offenders.append(f"{where} -> {image}")

    assert not offenders, f"images pinned only to a moving tag: {offenders}"
