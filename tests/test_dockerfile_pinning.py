"""Tests that the image build installs only hash-pinned dependencies.

The build used to run ``uv pip install "."`` from ``pyproject.toml``, whose
constraints are all ``>=``. The hash-pinned ``requirements.txt`` took no part
in it, drifted out of sync with the project, and ended up missing four runtime
dependencies outright — so the file that ``pip-audit`` checked described
neither what the image contained nor a working install.

These tests pin the property that closes that gap: dependencies come from the
lockfile, with hashes enforced.
"""

import re
import tomllib
from pathlib import Path

DOCKERFILE = Path("Dockerfile")
REQUIREMENTS_FILE = Path("requirements.txt")
PYPROJECT = Path("pyproject.toml")


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
