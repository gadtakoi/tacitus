"""Application configuration."""

from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = ConfigDict(env_prefix="TACITUS_")

    app_name: str = "tacitus"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000
    environment: str = "development"

    # Dev defaults — override via env vars in production
    s3_endpoint: str = "http://minio:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "tacitus-files"

    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expiration_minutes: int = 15
    jwt_refresh_expiration_days: int = 7

    cors_origins: str = "http://localhost:3000"


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
        if settings.jwt_secret == "change-me-in-production":
            msg = "Default JWT secret is not allowed in production"
            raise RuntimeError(msg)
        if settings.s3_access_key == "minioadmin" or settings.s3_secret_key == "minioadmin":
            msg = "Default MinIO credentials are not allowed in production"
            raise RuntimeError(msg)
        origins = [o.strip() for o in settings.cors_origins.split(",")]
        if "*" in origins:
            msg = "Wildcard CORS origin (*) is not allowed in production"
            raise RuntimeError(msg)
