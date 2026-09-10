"""Schemas for the read-only view of the runtime configuration.

The view is deliberately lossy: values that are credentials never leave the
process, and URLs that embed credentials are handed out with the password
removed. See ``app.services.settings_service`` for the classification.
"""

from pydantic import BaseModel, Field


class SettingItem(BaseModel):
    """One application setting as exposed to the UI."""

    name: str = Field(description="Environment variable name, e.g. TACITUS_LOG_LEVEL.")
    group: str = Field(description="Display group the setting belongs to.")
    value: str | None = Field(
        default=None,
        description="Effective value, or null when the setting is a secret.",
    )
    sensitive: bool = Field(description="True when the value is withheld as a secret.")
    is_set: bool = Field(
        description="True when a non-empty value is configured. Lets the UI tell "
        "an unset secret from a configured one without revealing it.",
    )


class SettingsResponse(BaseModel):
    """Response schema for ``GET /settings``."""

    items: list[SettingItem]
