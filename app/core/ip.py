"""Client IP utilities.

The client IP drives rate limiting, brute-force lockout and the security
audit trail, so it must not be attacker-controlled. ``X-Forwarded-For`` is
appended to by every proxy in the chain, which means the *leftmost* entry is
whatever the client sent — including a forged address. Only the entries our
own reverse proxies added are trustworthy, and they sit on the right.
"""

import ipaddress

from fastapi import Request

from app.core.config import settings


def _valid_ip(value: str) -> str | None:
    """Return ``value`` if it parses as an IP address, otherwise ``None``.

    Rejecting non-IP values keeps forged headers out of the ``VARCHAR(45)``
    audit columns and out of rate-limit keys.

    Args:
        value: Candidate address taken from a proxy header.

    Returns:
        The address string, or ``None`` when it is not a valid IP.
    """
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return None
    return value


def get_client_ip(request: Request) -> str:
    """Extract the client IP, trusting only proxies we actually run.

    ``settings.trusted_proxy_hops`` states how many reverse proxies sit in
    front of the application. Each of them appends the address it saw to
    ``X-Forwarded-For``, so with ``N`` hops the ``N``-th entry from the right
    is the address the outermost proxy observed — the real client. Everything
    to the left of it was supplied by the client and is ignored.

    With ``trusted_proxy_hops = 0`` the header is not consulted at all and the
    socket peer address is used, which is correct when the app is exposed
    directly.

    Args:
        request: Incoming request.

    Returns:
        Client IP address, or ``"unknown"`` when it cannot be determined.
    """
    hops = settings.trusted_proxy_hops
    if hops > 0:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            parts = [p.strip() for p in forwarded.split(",") if p.strip()]
            if len(parts) >= hops:
                candidate = _valid_ip(parts[-hops])
                if candidate is not None:
                    return candidate

    if request.client:
        return request.client.host
    return "unknown"
