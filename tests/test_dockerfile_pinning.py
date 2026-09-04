"""Tests that the image build installs only hash-pinned dependencies.

The build used to run ``uv pip install "."`` from ``pyproject.toml``, whose
constraints are all ``>=``. The hash-pinned ``requirements.txt`` took no part
in it, drifted out of sync with the project, and ended up missing four runtime
dependencies outright — so the file that ``pip-audit`` checked described
neither what the image contained nor a working install.

These tests pin the property that closes that gap: dependencies come from the
lockfile, with hashes enforced.
"""

import json
import re
import tomllib
from pathlib import Path

DOCKERFILE = Path("Dockerfile")
REQUIREMENTS_FILE = Path("requirements.txt")
PYPROJECT = Path("pyproject.toml")
NGINX_DOCKERFILE = Path("nginx/Dockerfile")
FRONTEND_PACKAGE = Path("frontend/package.json")
FRONTEND_LOCK = Path("frontend/package-lock.json")


def test_build_installs_from_the_lockfile_with_hashes() -> None:
    """The image installs dependencies from requirements.txt with hashes."""
    content = DOCKERFILE.read_text(encoding="utf-8")
    assert "--require-hashes" in content, "Dockerfile does not enforce dependency hashes"
    assert "-r requirements.txt" in content, "Dockerfile does not install from the lockfile"


def test_build_does_not_resolve_dependencies_from_pyproject() -> None:
    """Nothing in the build resolves dependencies from the ``>=`` ranges.

    Installing the project (``pip install .`` / ``uv pip install .``) would
    resolve ``[project.dependencies]`` afresh, which is exactly the drift this
    change removed.
    """
    content = DOCKERFILE.read_text(encoding="utf-8")
    install_lines = [
        line.strip()
        for line in content.splitlines()
        if re.search(r"(pip|uv pip)\s+install", line) and not line.strip().startswith("#")
    ]
    assert install_lines, "Dockerfile has no install step at all"
    for line in install_lines:
        assert "-r requirements.txt" in line, f"build installs outside the lockfile: {line!r}"


def test_every_lockfile_entry_carries_hashes() -> None:
    """No entry in the lockfile is pinned by version alone.

    ``--require-hashes`` rejects such a file outright, so this catches a
    hand-edited or wrongly exported lockfile before a build does.
    """
    content = REQUIREMENTS_FILE.read_text(encoding="utf-8")
    unhashed = [
        block.splitlines()[0]
        for block in re.split(r"\n(?=[A-Za-z0-9])", content)
        if re.match(r"[A-Za-z0-9._-]+==", block) and "--hash=sha256:" not in block
    ]
    assert not unhashed, f"lockfile entries without hashes: {unhashed}"


def test_lockfile_covers_every_declared_runtime_dependency() -> None:
    """Every dependency in pyproject appears in the lockfile.

    The previous lockfile was missing sqlalchemy, uvicorn, python-jose and
    sentry-sdk. Building from it produced an image that could not start, and
    the omission was invisible because the build never used the file.
    """
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    declared = {
        re.split(r"[><=\[!;]", dep)[0].strip().lower().replace("_", "-")
        for dep in project["project"]["dependencies"]
    }

    content = REQUIREMENTS_FILE.read_text(encoding="utf-8")
    locked = {
        match.group(1).lower().replace("_", "-")
        for match in re.finditer(r"^([A-Za-z0-9._-]+)==", content, re.MULTILINE)
    }

    missing = sorted(declared - locked)
    assert not missing, f"declared in pyproject but absent from requirements.txt: {missing}"


def test_frontend_build_installs_from_the_lockfile() -> None:
    """The frontend image builds with ``npm ci``, not ``npm install``.

    ``npm install`` re-resolves the dependency ranges and, with only
    package.json copied into the image, ignored package-lock.json entirely —
    so two builds of the same commit could ship different versions of React,
    Vite or Mantine.
    """
    content = NGINX_DOCKERFILE.read_text(encoding="utf-8")
    commands = [
        line.strip()
        for line in content.splitlines()
        if line.strip().startswith("RUN") and "npm" in line
    ]

    assert any("npm ci" in line for line in commands), "frontend build does not use npm ci"
    assert not any(re.search(r"npm\s+install", line) for line in commands), (
        "frontend build still uses npm install, which ignores the lockfile"
    )
    assert "package-lock.json" in content, "package-lock.json is never copied into the image"


def test_frontend_lockfile_agrees_with_package_json() -> None:
    """Every declared dependency is recorded in the lockfile with the same range.

    ``npm ci`` aborts when the two disagree, so a package.json edited without
    regenerating the lock breaks the image build. Catching it here turns a
    failed deploy into a failed test.
    """
    package = json.loads(FRONTEND_PACKAGE.read_text(encoding="utf-8"))
    lock = json.loads(FRONTEND_LOCK.read_text(encoding="utf-8"))
    root = lock["packages"][""]

    mismatched = []
    for section in ("dependencies", "devDependencies"):
        for name, declared in package.get(section, {}).items():
            locked = root.get(section, {}).get(name)
            if locked != declared:
                mismatched.append(f"{name}: package.json={declared} lock={locked}")

    assert not mismatched, f"package-lock.json is out of date: {mismatched}"
