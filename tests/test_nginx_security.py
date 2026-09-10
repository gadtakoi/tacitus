"""Tests for nginx security configuration."""

import re
from pathlib import Path

import pytest

NGINX_CONF = Path("nginx/nginx.conf")
NGINX_SSL_CONF = Path("ansible/roles/ssl_cert/templates/nginx-ssl.conf.j2")

# Headers that must reach the client on every response.
REQUIRED_HEADERS = (
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
    "Strict-Transport-Security",
    "Content-Security-Policy",
    "X-XSS-Protection",
    "Permissions-Policy",
)


@pytest.mark.parametrize("conf_file", [NGINX_CONF, NGINX_SSL_CONF])
def test_security_headers_present(conf_file: Path) -> None:
    """Verify that nginx configs contain essential security headers."""
    content = conf_file.read_text()
    assert "add_header Strict-Transport-Security" in content
    assert "add_header Content-Security-Policy" in content
    assert 'add_header X-Frame-Options "DENY"' in content
    assert 'add_header X-Content-Type-Options "nosniff"' in content
    assert "add_header X-XSS-Protection" in content
    assert "add_header Permissions-Policy" in content


def _locations_with_add_header(content: str) -> dict[str, str]:
    """Return the body of every ``location`` block that declares an add_header.

    Args:
        content: Full nginx configuration text.

    Returns:
        Mapping of location path to the text of its block.
    """
    blocks: dict[str, str] = {}
    for match in re.finditer(r"location\s+(\S+)\s*\{", content):
        path = match.group(1)
        depth, i = 1, match.end()
        while i < len(content) and depth:
            if content[i] == "{":
                depth += 1
            elif content[i] == "}":
                depth -= 1
            i += 1
        body = content[match.end() : i]
        if "add_header" in body:
            blocks[path] = body
    return blocks


@pytest.mark.parametrize("conf_file", [NGINX_CONF, NGINX_SSL_CONF])
def test_locations_adding_headers_repeat_the_security_set(conf_file: Path) -> None:
    """A location declaring any add_header must re-declare the security ones.

    nginx does not merge ``add_header`` across levels: a block with its own
    ``add_header`` discards everything inherited from ``http``. Adding a lone
    CORS header to a location therefore silently strips CSP, HSTS and the rest
    from that endpoint's responses.
    """
    content = conf_file.read_text()
    for path, body in _locations_with_add_header(content).items():
        missing = [h for h in REQUIRED_HEADERS if f"add_header {h}" not in body]
        assert not missing, f"location {path} drops inherited headers: {', '.join(missing)}"


@pytest.mark.parametrize("conf_file", [NGINX_CONF, NGINX_SSL_CONF])
def test_env_file_denied(conf_file: Path) -> None:
    """Verify that .env files are blocked in nginx configs."""
    content = conf_file.read_text()
    assert "location /.env {" in content
    assert "deny all" in content


@pytest.mark.parametrize("conf_file", [NGINX_CONF, NGINX_SSL_CONF])
def test_server_tokens_disabled(conf_file: Path) -> None:
    """Verify that nginx does not advertise its version.

    Without the directive nginx defaults to ``server_tokens on`` and puts the
    exact build into every ``Server`` header and into the footer of every error
    page it generates, which narrows down the published vulnerabilities worth
    trying against it.
    """
    content = conf_file.read_text()
    assert "server_tokens off;" in content


@pytest.mark.parametrize("conf_file", [NGINX_CONF, NGINX_SSL_CONF])
def test_nginx_errors_use_custom_page(conf_file: Path) -> None:
    """Verify that nginx serves its own error page instead of the stock one.

    ``server_tokens off`` strips the version from the stock page but leaves the
    server name in its footer, so the page itself is replaced. The location is
    ``internal`` because the page is a substitute for an error, not a route.
    """
    content = conf_file.read_text()
    assert re.search(r"error_page[^;]*\s/error\.html;", content)
    location = re.search(r"location = /error\.html \{(.*?)\}", content, re.DOTALL)
    assert location, "no location block serving the custom error page"
    assert "internal;" in location.group(1)
