# Build stage
FROM python:3.14-slim AS builder

WORKDIR /build

RUN pip install --no-cache-dir uv

COPY pyproject.toml ./
COPY app/ ./app/
RUN uv pip install --system --no-cache "."

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

RUN chown -R tacitus:tacitus ${APP_HOME}

USER tacitus

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
