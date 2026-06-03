"""Schemas for hello world endpoint."""

from pydantic import BaseModel


class HelloWorldResponse(BaseModel):
    """Response schema for hello world endpoint."""

    message: str
