"""Tacitus backend application."""

# Runtime source of the application version.
#
# The Docker image copies ``app/`` instead of installing the project as a
# distribution (see Dockerfile), so ``importlib.metadata.version("tacitus")``
# raises ``PackageNotFoundError`` there — exactly where Sentry needs a release
# string. Keeping the value here makes it available without the metadata.
#
# ``pyproject.toml`` declares the same number for packaging; the two are kept
# in step by ``test_version_matches_pyproject``.
__version__ = "0.1.0"
