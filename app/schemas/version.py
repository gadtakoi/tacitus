"""Schema for the running application version.

The version identifies the build the process was started from. It is served
only to authenticated callers: an exact build number tells an anonymous caller
which published vulnerabilities to try.
"""

from pydantic import BaseModel, Field


class VersionResponse(BaseModel):
    """Response schema for ``GET /settings/version``."""

    version: str = Field(description="Version of the running application, e.g. 0.1.0.")
