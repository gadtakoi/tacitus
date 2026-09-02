"""Tests for trusted-proxy client IP resolution.

``get_client_ip`` feeds rate limiting, brute-force lockout and the security
audit trail, so a client must not be able to choose the address attributed to
it by sending its own ``X-Forwarded-For``.
"""

from unittest.mock import Mock

import pytest

from app.core import ip as ip_module
from app.core.ip import get_client_ip


def _request(forwarded: str | None, peer: str | None = "10.1.1.1") -> Mock:
    """Build a request stub with the given forwarded header and socket peer.

    Args:
        forwarded: Value of the ``X-Forwarded-For`` header, or ``None`` when
            the header is absent.
        peer: Address of the socket peer, or ``None`` for no client info.

    Returns:
        Mock request exposing ``headers`` and ``client``.
    """
    request = Mock()
    request.headers = {} if forwarded is None else {"x-forwarded-for": forwarded}
    request.client = None if peer is None else Mock(host=peer)
    return request


@pytest.fixture
def hops(monkeypatch):
    """Return a setter for ``settings.trusted_proxy_hops``.

    Args:
        monkeypatch: pytest monkeypatch fixture.

    Returns:
        Callable applying the given hop count for the duration of the test.
    """

    def _set(value: int) -> None:
        monkeypatch.setattr(ip_module.settings, "trusted_proxy_hops", value)

    return _set


def test_single_proxy_uses_address_nginx_appended(hops) -> None:
    """With one proxy the rightmost entry — written by nginx — is used."""
    hops(1)
    assert get_client_ip(_request("203.0.113.7")) == "203.0.113.7"


def test_client_cannot_spoof_its_address(hops) -> None:
    """A forged leftmost entry is ignored in favour of the proxy's entry.

    nginx uses ``$proxy_add_x_forwarded_for``, so anything the client sent is
    kept to the left of the address nginx actually observed.
    """
    hops(1)
    assert get_client_ip(_request("1.2.3.4, 203.0.113.7")) == "203.0.113.7"


def test_two_proxies_skip_the_inner_hop(hops) -> None:
    """With two proxies the second entry from the right is the real client."""
    hops(2)
    forwarded = "1.2.3.4, 203.0.113.7, 192.168.0.10"
    assert get_client_ip(_request(forwarded)) == "203.0.113.7"


def test_header_ignored_when_no_proxy_is_trusted(hops) -> None:
    """Without a proxy in front the header is untrusted and the peer wins."""
    hops(0)
    assert get_client_ip(_request("1.2.3.4", peer="10.1.1.1")) == "10.1.1.1"


def test_falls_back_to_peer_when_chain_is_too_short(hops) -> None:
    """A chain shorter than the hop count means the header was not set by us."""
    hops(2)
    assert get_client_ip(_request("1.2.3.4", peer="10.1.1.1")) == "10.1.1.1"


def test_non_ip_value_is_rejected(hops) -> None:
    """Garbage in the header must not reach audit columns or limiter keys."""
    hops(1)
    assert get_client_ip(_request("not-an-ip", peer="10.1.1.1")) == "10.1.1.1"


def test_ipv6_address_is_accepted(hops) -> None:
    """IPv6 clients are resolved as-is."""
    hops(1)
    assert get_client_ip(_request("2001:db8::1")) == "2001:db8::1"


def test_unknown_when_no_header_and_no_peer(hops) -> None:
    """Neither header nor socket peer available yields a sentinel value."""
    hops(1)
    assert get_client_ip(_request(None, peer=None)) == "unknown"
