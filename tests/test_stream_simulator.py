"""Tests for the stream simulator service and its entrypoint.

The simulator is a fake camera: it publishes a synthetic RTSP stream into
MediaMTX so the pipeline (publish -> path -> GET /streams -> recording ->
archive) can be exercised without hardware. That makes it a container holding
valid publisher credentials on the application network, and one that fills
``recordings/`` while it runs — so the properties worth pinning are the ones
that keep it out of production and away from anything it does not need.

None of these tests need Docker: they parse the compose file and read the
scripts as text, like ``test_compose_pinning.py`` and
``test_mediamtx_security.py``. A test that skipped without Docker would add to
the silent-skip problem ``docs/testing.md`` already warns about.
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

COMPOSE_FILE = Path("docker-compose.yml")
ENTRYPOINT = Path("scripts/simulate-stream-entrypoint.sh")
WRAPPER = Path("scripts/simulate-stream.sh")
E2E_CHECK = Path("scripts/e2e-stream-check.sh")
HEALTHCHECK = Path("scripts/healthcheck.sh")
ENV_EXAMPLE = Path(".env.example")
ANSIBLE_APP_DEPLOY = Path("ansible/roles/app_deploy/tasks/main.yml")
ANSIBLE_MEDIAMTX = Path("ansible/roles/mediamtx/tasks/main.yml")

SERVICE = "stream-simulator"
PROFILE = "simulator"
MEDIAMTX_SERVICE = "mediamtx"

# Development placeholders from mediamtx/mediamtx.yml. They are fine in that
# file, which is what declares the accounts; the simulator must take them from
# the environment instead of carrying its own copy.
DEV_PASSWORDS = ("dev-publisher-change-me", "dev-reader-change-me")

# Secrets the simulator has no business seeing. It publishes video; it does not
# sign tokens, talk to the database, or write to S3.
FOREIGN_SECRETS = ("TACITUS_JWT_SECRET", "POSTGRES_PASSWORD", "S3_SECRET_KEY")


def _services() -> dict[str, dict[str, Any]]:
    """Parse the compose file and return its services.

    Returns:
        Mapping of service name to its definition.
    """
    parsed = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    return parsed["services"]


@pytest.fixture
def simulator() -> dict[str, Any]:
    """The simulator service definition from docker-compose.yml.

    Returns:
        The service block.
    """
    services = _services()
    assert SERVICE in services, f"сервис {SERVICE} отсутствует в {COMPOSE_FILE}"
    return services[SERVICE]


def _code_lines(script: Path) -> list[str]:
    """Read a shell script without comment-only lines.

    Comments explain the very rules these tests enforce (and quote the tokens
    they forbid), so matching against them would produce failures that are
    about prose rather than behaviour.

    Args:
        script: Path to the shell script.

    Returns:
        Lines that carry code.
    """
    return [
        line
        for line in script.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_simulator_is_profile_gated(simulator: dict[str, Any]) -> None:
    """The simulator only starts when its profile is requested.

    A plain ``docker compose up`` must never start a fake camera — locally or
    on a server. Losing this key is a one-line regression with no other
    visible symptom.
    """
    assert simulator.get("profiles") == [PROFILE]


def test_only_the_simulator_is_profile_gated() -> None:
    """No other service hides behind a profile.

    Profiles are new to this compose file. A service that quietly acquired one
    would stop being deployed, and nothing else here would notice.
    """
    gated = {name for name, service in _services().items() if service.get("profiles")}
    assert gated == {SERVICE}


def test_simulator_shares_the_mediamtx_image(simulator: dict[str, Any]) -> None:
    """The simulator runs on the same image as MediaMTX itself.

    That image already carries the ffmpeg the simulator needs, so reusing it
    adds no second supply-chain entry and no second ffmpeg version to keep in
    step — and the pin is covered by ``test_compose_pinning.py`` for free.
    """
    assert simulator["image"] == _services()[MEDIAMTX_SERVICE]["image"]


def test_simulator_overrides_the_image_entrypoint(simulator: dict[str, Any]) -> None:
    """The image entrypoint is replaced by the simulator script.

    The image starts MediaMTX by default. Without the override the container
    would run a second RTSP server that publishes nothing while looking
    perfectly healthy in ``docker ps``.
    """
    entrypoint = simulator.get("entrypoint")
    assert isinstance(entrypoint, list), "entrypoint должен быть задан списком"
    assert any(ENTRYPOINT.name in part for part in entrypoint)


def test_simulator_does_not_read_the_env_file(simulator: dict[str, Any]) -> None:
    """The simulator is not handed the whole .env.

    Every other service loads ``.env`` wholesale. The publisher needs exactly
    one secret, so it gets exactly one — the JWT signing key, the database
    password and the S3 keys stay out of a container whose job is to push test
    video.
    """
    assert "env_file" not in simulator
    declared = " ".join(simulator.get("environment", []))
    for secret in FOREIGN_SECRETS:
        assert secret not in declared, f"{secret} не должен попадать в {SERVICE}"


def test_simulator_credentials_come_from_environment(simulator: dict[str, Any]) -> None:
    """No credential literal is baked into the service block.

    The password must come from the environment. Compose deliberately declares
    no fallback for it: on a host whose .env does not define the variable the
    simulator cannot publish at all, which is a second barrier independent of
    the profile.
    """
    block = yaml.safe_dump(simulator)
    for password in DEV_PASSWORDS:
        assert password not in block

    environment = simulator.get("environment", [])
    publish_password = next(
        entry for entry in environment if entry.startswith("TACITUS_SIM_PUBLISH_PASSWORD=")
    )
    assert publish_password.endswith("${TACITUS_SIM_PUBLISH_PASSWORD:-}")


def test_simulator_has_no_access_to_recordings(simulator: dict[str, Any]) -> None:
    """The simulator can neither read nor corrupt the recording archive.

    It publishes over the network; it has no reason to touch the directory
    MediaMTX writes into. The only mount is its own script, read-only.
    """
    volumes = simulator.get("volumes", [])
    assert not any(volume.startswith("./recordings") for volume in volumes)
    assert volumes == [f"./{ENTRYPOINT.as_posix()}:/{ENTRYPOINT.name}:ro"]
    assert simulator.get("read_only") is True
    assert simulator.get("cap_drop") == ["ALL"]


def test_simulator_does_not_restart_automatically(simulator: dict[str, Any]) -> None:
    """A dead simulator stays dead.

    ``unless-stopped`` would bring the fake camera back after a host reboot,
    together with the rest of the stack, long after anyone was watching.
    """
    assert simulator.get("restart") == "no"


def test_entrypoint_targets_posix_sh() -> None:
    """The entrypoint is POSIX sh, because the image has no bash.

    ``bluenviron/mediamtx:1.15.1-ffmpeg`` ships busybox ash only. A bashism is
    not a style problem here — it is a runtime crash in a container nobody is
    looking at.
    """
    lines = ENTRYPOINT.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "#!/bin/sh"

    bashisms = re.compile(r"\[\[|\blocal\s|pipefail|<<<|\$\(\(.*\+\+")
    offenders = [line for line in _code_lines(ENTRYPOINT) if bashisms.search(line)]
    assert not offenders, f"bash-измы в {ENTRYPOINT}: {offenders}"


def test_entrypoint_never_prints_the_password() -> None:
    """No log statement can emit the publisher password.

    ffmpeg needs the credential inside the RTSP URL, so the script keeps a
    redacted variant for logging. Printing the real one would put a secret
    into ``docker logs`` and into CI output (.ai/project/logging-rules.md).
    """
    printing = re.compile(r"\b(echo|printf|log)\b")
    offenders = [
        line
        for line in _code_lines(ENTRYPOINT)
        if printing.search(line) and "TACITUS_SIM_PUBLISH_PASSWORD" in line
    ]
    assert not offenders, f"пароль может попасть в лог: {offenders}"


def test_entrypoint_pins_ffmpeg_loglevel() -> None:
    """ffmpeg always runs at -loglevel error.

    At ``verbose`` and above ffmpeg echoes the RTSP URL it was given — password
    included — which is exactly what the redaction above exists to prevent.
    """
    code = "\n".join(_code_lines(ENTRYPOINT))
    assert "-loglevel error" in code
    assert not re.search(r"-loglevel\s+(verbose|debug|trace)", code)


def test_simulator_env_vars_are_documented(simulator: dict[str, Any]) -> None:
    """Every simulator knob compose reads is present in .env.example.

    An undocumented variable becomes folklore: it works for whoever added it
    and is invisible to everyone else.
    """
    documented = ENV_EXAMPLE.read_text(encoding="utf-8")
    referenced = set(re.findall(r"\$\{(TACITUS_SIM_[A-Z_]+)", " ".join(simulator["environment"])))
    assert referenced, "в сервисе нет ни одной переменной TACITUS_SIM_*"
    missing = {name for name in referenced if f"\n{name}=" not in f"\n{documented}"}
    assert not missing, f"не описаны в {ENV_EXAMPLE}: {sorted(missing)}"


def test_production_deploy_does_not_enable_the_profile() -> None:
    """The Ansible deploy never passes --profile.

    This is the property that actually keeps the simulator off servers, so it
    is worth asserting rather than trusting: a ``--profile simulator`` added to
    a deploy task would ship a fake camera to production.
    """
    for task_file in (ANSIBLE_APP_DEPLOY, ANSIBLE_MEDIAMTX):
        assert "--profile" not in task_file.read_text(encoding="utf-8"), (
            f"{task_file} включает профиль compose при деплое"
        )


def test_healthcheck_flags_the_simulator() -> None:
    """The production health check treats the simulator as a failure.

    It must not be listed among the containers expected to run, and its
    presence on a server has to be reported — the profile can always be
    enabled by hand.
    """
    content = HEALTHCHECK.read_text(encoding="utf-8")
    long_running = content.split("LONG_RUNNING_CONTAINERS=", 1)[1].split('"', 2)[1]
    assert "tacitus-stream-simulator" not in long_running

    guard = re.search(
        r'bad "tacitus-stream-simulator[^"]*profile|bad "tacitus-stream-simulator[^"]*профиль',
        content,
    )
    assert guard, "в healthcheck.sh нет проверки на присутствие имитатора"


def test_scripts_declare_their_exit_codes() -> None:
    """Both operator-facing scripts document what their exit codes mean.

    The wrapper and the E2E check distinguish "the scenario failed" (1) from
    "the check could not run" (2). That distinction is only useful if it is
    written down where the operator looks.
    """
    for script in (WRAPPER, E2E_CHECK):
        header = script.read_text(encoding="utf-8")[:3000]
        assert "Коды выхода" in header, f"{script} не описывает коды выхода"
