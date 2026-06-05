"""Security utilities for the application."""

from fastapi import Response


def set_secure_cookie(
    response: Response,
    name: str,
    value: str,
    max_age: int | None = None,
    path: str = "/",
    domain: str | None = None,
) -> None:
    """Set a cookie with secure flags enforced.

    All cookies set through this helper automatically receive:
    - Secure: transmitted only over HTTPS
    - HttpOnly: not accessible via JavaScript
    - SameSite=Strict: sent only with same-site requests

    Args:
        response: FastAPI response object.
        name: Cookie name.
        value: Cookie value.
        max_age: Cookie lifetime in seconds.
        path: URL path for which the cookie is valid.
        domain: Domain for which the cookie is valid.
    """
    response.set_cookie(
        key=name,
        value=value,
        max_age=max_age,
        path=path,
        domain=domain,
        secure=True,
        httponly=True,
        samesite="strict",
    )
