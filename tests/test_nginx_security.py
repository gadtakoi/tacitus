"""Tests for nginx security configuration."""

from pathlib import Path

import pytest

NGINX_CONF = Path("nginx/nginx.conf")
NGINX_SSL_CONF = Path("ansible/roles/ssl_cert/templates/nginx-ssl.conf.j2")


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


@pytest.mark.parametrize("conf_file", [NGINX_CONF, NGINX_SSL_CONF])
def test_env_file_denied(conf_file: Path) -> None:
    """Verify that .env files are blocked in nginx configs."""
    content = conf_file.read_text()
    assert "location /.env {" in content
    assert "deny all" in content
