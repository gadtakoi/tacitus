"""Application configuration."""

from pydantic import ConfigDict, Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = ConfigDict(env_prefix="TACITUS_")

    app_name: str = "tacitus"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000
    environment: str = "development"

    # S3 settings — no dev defaults in code, use .env
    s3_endpoint: str = "http://minio:9000"
    s3_access_key: str = Field(..., min_length=8)
    s3_secret_key: str = Field(..., min_length=8)
    s3_bucket: str = "tacitus-files"

    jwt_secret: str = Field(...)
    jwt_algorithm: str = "HS256"
    jwt_expiration_minutes: int = 15
    jwt_refresh_expiration_days: int = 7

    cors_origins: str = "http://localhost:3000"

    # Logging settings
    log_level: str = "INFO"
    log_format: str = "json"
    log_destination: str = "stdout"
    log_file_path: str = "/var/log/tacitus/app.log"
    log_max_bytes: int = 10_485_760  # 10 MB
    log_backup_count: int = 5

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
