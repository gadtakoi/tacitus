"""Application configuration."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_prefix="TACITUS_")

    app_name: str = "tacitus"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000
    environment: str = "development"

    # S3 settings — dev defaults for local development, override in .env for production
    s3_endpoint: str = "http://minio:9000"
    s3_access_key: str = Field(default="minioadmin", min_length=8)
    s3_secret_key: str = Field(default="minioadmin", min_length=8)
    s3_bucket: str = "tacitus-files"
    presigned_url_expiration: int = Field(
        default=300,
        ge=60,
        le=86400,
        description="Lifetime of presigned S3/MinIO download URLs in seconds. "
        "Short-lived URLs reduce exposure if a link is leaked.",
    )

    jwt_secret: str = Field(default="dev-jwt-secret-change-me")
    jwt_algorithm: str = "HS256"
    jwt_expiration_minutes: int = 15
    jwt_refresh_expiration_days: int = 7

    cors_origins: str = "http://localhost:3000"

    # Number of reverse proxies (nginx, load balancers) in front of the app.
    # Each one appends the address it saw to ``X-Forwarded-For``, so this value
    # decides which entry of that header is trustworthy. Set to 0 when the app
    # is exposed directly — the header is then ignored entirely, because a
    # client can forge it. See ``app.core.ip.get_client_ip``.
    trusted_proxy_hops: int = Field(default=1, ge=0, le=8)

    # Logging settings
    log_level: str = "INFO"
    log_format: str = "json"
    log_destination: str = "stdout"
    log_file_path: str = "/var/log/tacitus/app.log"
    log_max_bytes: int = 10_485_760  # 10 MB
    log_backup_count: int = 5

    # Database settings
    database_url: str = "postgresql+asyncpg://tacitus:tacitus@postgres:5432/tacitus"

    # MediaMTX Control API settings
    mtx_api_url: str = "http://mediamtx:9997/v3"

    # Rate limiting storage URI.
    # Supported backends: "memory://" (default, per-process) or
    # "redis://host:port" (recommended for production and multi-instance).
    # The ``limits`` library is used under the hood by ``slowapi``.
    rate_limit_storage_uri: str = "memory://"

    # Sentry settings
    sentry_dsn: str = ""
    sentry_environment: str = "development"
    sentry_traces_sample_rate: float = 0.1


settings = Settings()


def validate_startup_settings() -> None:
    """Validate critical security settings on application startup.

    Raises:
        RuntimeError: If production settings are insecure.
    """
    if settings.environment == "production":
        if settings.debug:
            # Debug mode leaks internals twice over: ``generic_exception_handler``
            # returns ``str(exc)`` to the client, and ``create_async_engine`` is
            # created with ``echo=True``, which writes every statement — bind
            # parameters included — to the logs.
            msg = "Debug mode must be disabled in production"
            raise RuntimeError(msg)
        if len(settings.jwt_secret) < 32:
            msg = "JWT secret must be at least 32 characters in production"
            raise RuntimeError(msg)
        if settings.s3_access_key == "minioadmin" or settings.s3_secret_key == "minioadmin":
            msg = "Default MinIO credentials are not allowed in production"
            raise RuntimeError(msg)
        origins = [o.strip() for o in settings.cors_origins.split(",")]
        if "*" in origins:
            msg = "Wildcard CORS origin (*) is not allowed in production"
            raise RuntimeError(msg)
