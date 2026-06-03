"""Application configuration."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    app_name: str = "tacitus"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000

    class Config:
        env_prefix = "TACITUS_"


settings = Settings()
