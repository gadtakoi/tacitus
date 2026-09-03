"""Tests for MediaMTX authentication configuration.

MediaMTX ships an anonymous default: if ``authInternalUsers`` is absent, the
built-in list grants ``publish``, ``read`` and ``playback`` on every path to
``user: any`` with an empty password. Anyone able to reach the RTSP port can
then push a stream into any path and pull anyone else's stream out — and
recording is enabled, so the stream also lands on disk and in S3.

These tests pin the property that matters: every config we ship declares its
own users, and no shipped entry is anonymous.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml

MEDIAMTX_CONF = Path("mediamtx/mediamtx.yml")
MEDIAMTX_TEMPLATE = Path("ansible/roles/mediamtx/templates/mediamtx.yml.j2")
ANSIBLE_PLAYBOOK = Path("ansible/playbook.yml")
COMPOSE_FILE = Path("docker-compose.yml")

# Development placeholders. They are fine locally; the production guardrails
# (ansible pre-check and validate_startup_settings) must reject them.
DEV_PASSWORDS = (
    "dev-publisher-change-me",
    "dev-reader-change-me",
    "dev-api-change-me",
)


def _load_users(conf_file: Path) -> list[dict[str, Any]]:
    """Parse ``authInternalUsers`` out of a MediaMTX config.

    The Ansible template is not valid YAML on its own (Jinja expressions sit
    where scalars belong), so placeholders are replaced by a marker before
    parsing. That keeps one parser for both files.

    Args:
        conf_file: Path to a mediamtx.yml or mediamtx.yml.j2 file.

    Returns:
        The declared user entries.
    """
    text = conf_file.read_text(encoding="utf-8")
    if conf_file.suffix == ".j2":
        # Drop the loop over mediamtx_paths; keep the auth section parseable.
        text = "\n".join(line for line in text.splitlines() if not line.strip().startswith("{%"))
        text = text.replace("{{ ", "RENDERED_").replace(" }}", "")
    parsed = yaml.safe_load(text)
    users = parsed.get("authInternalUsers")
    assert users is not None, f"{conf_file} does not declare authInternalUsers"
    return list(users)


@pytest.mark.parametrize("conf_file", [MEDIAMTX_CONF, MEDIAMTX_TEMPLATE])
def test_auth_internal_users_declared(conf_file: Path) -> None:
    """Every shipped config overrides the anonymous default list."""
    users = _load_users(conf_file)
    assert users, f"{conf_file} declares an empty authInternalUsers list"


@pytest.mark.parametrize("conf_file", [MEDIAMTX_CONF, MEDIAMTX_TEMPLATE])
def test_no_anonymous_user(conf_file: Path) -> None:
    """No entry may be the anonymous ``any``/empty-password pair."""
    for user in _load_users(conf_file):
        assert user.get("user") != "any", f"{conf_file} keeps an anonymous user entry"
        assert user.get("pass"), f"{conf_file} has an entry without a password"


@pytest.mark.parametrize("conf_file", [MEDIAMTX_CONF, MEDIAMTX_TEMPLATE])
def test_permissions_are_separated_by_action(conf_file: Path) -> None:
    """Publish, read and api rights are held by different users.

    A single all-powerful account would defeat the point: a leaked camera
    password would also grant playback of every recording and access to the
    Control API.
    """
    granted: dict[str, set[str]] = {}
    for user in _load_users(conf_file):
        actions = {perm["action"] for perm in user["permissions"]}
        granted[user["user"]] = actions

    all_actions = set().union(*granted.values())
    assert {"publish", "read", "api"} <= all_actions, f"{conf_file} misses a required action"

    for name, actions in granted.items():
        assert not ({"publish", "api"} <= actions), f"{conf_file}: {name} holds publish and api"
        assert not ({"read", "api"} <= actions), f"{conf_file}: {name} holds read and api"
        assert not ({"publish", "read"} <= actions), f"{conf_file}: {name} holds publish and read"


def test_production_precheck_rejects_dev_passwords() -> None:
    """The Ansible pre-check refuses production with the shipped placeholders."""
    content = ANSIBLE_PLAYBOOK.read_text(encoding="utf-8")
    for password in DEV_PASSWORDS:
        assert f"!= '{password}'" in content, f"playbook does not guard against {password}"


def test_rtsp_port_is_not_published_on_all_interfaces_by_default() -> None:
    """The compose default binds RTSP to loopback.

    RTSP credentials are sent with Basic auth over an unencrypted transport,
    so the port must not be world-reachable unless the operator opts in.
    """
    content = COMPOSE_FILE.read_text(encoding="utf-8")
    assert "${TACITUS_MTX_RTSP_BIND:-127.0.0.1}:" in content
