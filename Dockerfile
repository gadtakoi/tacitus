# Build stage
FROM python:3.14-slim AS builder

WORKDIR /build

# Every dependency comes from the hash-pinned lockfile, so the exact set of
# packages in the image is determined by requirements.txt and nothing else.
# ``--require-hashes`` makes pip refuse any artifact whose digest does not
# match, and it also forbids unpinned or transitively-resolved additions: a
# dependency missing from the lockfile fails the build instead of being
# silently resolved from the network.
#
# Regenerate the lockfile with (see docs/uv.md):
#   uv lock
#   uv export --frozen --no-dev --no-emit-project \
#     --format requirements-txt --output-file requirements.txt
COPY requirements.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

# Runtime stage
FROM python:3.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    APP_HOME=/app

WORKDIR ${APP_HOME}

# Create non-privileged user and log directory
RUN groupadd --gid 1000 tacitus && \
    useradd --uid 1000 --gid tacitus --shell /bin/false tacitus && \
    mkdir -p /var/log/tacitus && \
    chown tacitus:tacitus /var/log/tacitus

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=builder /usr/local/bin/uvicorn /usr/local/bin/uvicorn
COPY --from=builder /usr/local/bin/alembic /usr/local/bin/alembic

# The application is imported from the working directory rather than installed
# as a distribution. Installing it would run a PEP 517 source build, and that
# build fetches setuptools from PyPI outside the hash-pinned set — the one hole
# left in an otherwise fully pinned image.
COPY app/ ./app/

RUN chown -R tacitus:tacitus ${APP_HOME}

USER tacitus

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
