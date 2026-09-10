#!/usr/bin/env python3
"""Set the application version everywhere it is declared.

The version lives in ``app/__init__.py`` — that is the value the running
process reports. Every other occurrence (packaging metadata, frontend package
files, image tags, deployment variables, documented examples) is a copy that
has to agree with it, and copies drift the moment one of them is updated by
hand.

Usage::

    python scripts/bump_version.py 0.2.0

The script only rewrites files; it commits nothing and is safe to re-run — a
second run on an already-bumped tree reports "no change" for every file.

``tests/test_version_consistency.py`` reads ``DECLARATIONS`` from here, so a
new place that declares the version is added once, in the table below, and is
then both bumped and verified.

Files are rewritten in text mode, so their line endings follow the platform's
checkout convention. ``.gitattributes`` pins only ``*.sh`` to LF, and no shell
script declares the version.
"""

import re
import sys
from pathlib import Path

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")

# Any version-looking literal, used inside the patterns below. Kept loose on
# purpose: it has to match the version currently in the file, whatever it is.
_V = r"\d+\.\d+\.\d+"

# Where the version is declared. Each pattern captures the version itself in
# group ``version``; every pattern must match at least once, so a file that
# changes shape fails loudly instead of being silently skipped.
DECLARATIONS: tuple[tuple[str, str], ...] = (
    # The source of truth.
    ("app/__init__.py", rf'(?m)^__version__ = "(?P<version>{_V})"$'),
    # Packaging metadata.
    ("pyproject.toml", rf'(?m)^version = "(?P<version>{_V})"$'),
    # Frontend package files. The two-space indent anchors the project's own
    # version and keeps dependency versions out of the match.
    ("frontend/package.json", rf'(?m)^  "version": "(?P<version>{_V})"'),
    (
        "frontend/package-lock.json",
        rf'"name": "tacitus-frontend",\s*\n\s*"version": "(?P<version>{_V})"',
    ),
    # Image tags: the tag of a locally built image *is* the application
    # version, which is why tests/test_compose_pinning.py skips them from the
    # upstream-pinning check.
    ("docker-compose.yml", rf"tacitus-(?:app|nginx|video-processor):(?P<version>{_V})"),
    # Deployment variables.
    ("ansible/group_vars/all.yml", rf'(?m)^tacitus_version: "(?P<version>{_V})"$'),
    # Documented examples of the image tags.
    ("docs/audit.md", rf"tacitus-(?:app|nginx|video-processor):(?P<version>{_V})"),
    ("docs/infrastructure.md", rf"tacitus-(?:app|nginx|video-processor):(?P<version>{_V})"),
)


def declared_versions(root: Path) -> dict[str, set[str]]:
    """Collect the versions each file declares.

    Args:
        root: Repository root.

    Returns:
        Mapping of file path to the distinct versions found in it.

    Raises:
        ValueError: If a pattern matches nothing, i.e. the file no longer
            declares the version the way this table expects.
    """
    found: dict[str, set[str]] = {}
    for path, pattern in DECLARATIONS:
        text = (root / path).read_text(encoding="utf-8")
        matches = list(re.finditer(pattern, text))
        if not matches:
            raise ValueError(f"{path}: no version declaration matched {pattern!r}")
        found[path] = {match.group("version") for match in matches}
    return found


def bump(root: Path, version: str) -> list[str]:
    """Rewrite every declaration to ``version``.

    Args:
        root: Repository root.
        version: Target version, e.g. ``0.2.0``.

    Returns:
        Human-readable report lines, one per file.
    """
    report = []
    for path, pattern in DECLARATIONS:
        file = root / path
        text = file.read_text(encoding="utf-8")
        matches = list(re.finditer(pattern, text))
        if not matches:
            raise ValueError(f"{path}: no version declaration matched {pattern!r}")
        # Replace only the captured version, not the whole match: the match
        # carries the surrounding text (an image name, a JSON key) that must
        # survive untouched. Right to left, so earlier spans stay valid.
        updated = text
        for match in reversed(matches):
            start, end = match.span("version")
            updated = updated[:start] + version + updated[end:]
        was = sorted({match.group("version") for match in matches})
        if updated == text:
            report.append(f"  {path}: no change ({version})")
        else:
            file.write_text(updated, encoding="utf-8")
            report.append(f"  {path}: {', '.join(was)} -> {version} ({len(matches)} place(s))")
    return report


def main(argv: list[str]) -> int:
    """Entry point.

    Args:
        argv: Command-line arguments without the program name.

    Returns:
        Process exit code.
    """
    if len(argv) != 1 or not _SEMVER.match(argv[0]):
        print("usage: python scripts/bump_version.py <major.minor.patch>", file=sys.stderr)
        return 2

    root = Path(__file__).resolve().parent.parent
    print(f"Setting version to {argv[0]}:")
    for line in bump(root, argv[0]):
        print(line)
    print("\nReview the diff, then commit. Nothing was committed or tagged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
