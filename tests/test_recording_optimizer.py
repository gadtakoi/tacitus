"""Tests for the recording optimizer service and its script.

``recording-optimizer`` is the only component in the stack that rewrites a
recording. It decodes a closed segment, drops the frames that do not differ
from the one before (ffmpeg's ``mpdecimate``) and replaces the original in
place — an irreversible operation on the one copy of the video that exists
before it reaches S3.

That makes the properties worth pinning different from every other service
here. They are all about not destroying a recording: never touching a segment
MediaMTX is still writing, never replacing the original with something that was
not verified first, and never letting a half-finished transcode reach the
archive.

None of these tests need Docker or ffmpeg: they parse the compose file and read
the script as text, like ``test_stream_simulator.py`` and
``test_compose_pinning.py``. A test that skipped without Docker would add to the
silent-skip problem ``docs/testing.md`` already warns about.
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

COMPOSE_FILE = Path("docker-compose.yml")
SCRIPT = Path("scripts/optimize-recordings.sh")
ARCHIVE_MANAGER = Path("scripts/mediamtx-archive-manager.py")
HEALTHCHECK = Path("scripts/healthcheck.sh")
ENV_EXAMPLE = Path(".env.example")

SERVICE = "recording-optimizer"
CLEANUP_SERVICE = "mediamtx-cleanup"
MEDIAMTX_SERVICE = "mediamtx"

# Secrets the optimizer has no business seeing. It rewrites files on a bind
# mount; it does not sign tokens, talk to the database, or reach S3.
FOREIGN_SECRETS = ("TACITUS_JWT_SECRET", "POSTGRES_PASSWORD", "S3_SECRET_KEY")


def _services() -> dict[str, dict[str, Any]]:
    """Parse the compose file and return its services.

    Returns:
        Mapping of service name to its definition.
    """
    parsed = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    return parsed["services"]


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


def _environment(service: dict[str, Any]) -> dict[str, str]:
    """Split a compose ``environment`` list into a mapping.

    Args:
        service: A compose service definition.

    Returns:
        Mapping of variable name to its declared value.
    """
    entries = service.get("environment", [])
    return dict(entry.split("=", 1) for entry in entries)


@pytest.fixture
def optimizer() -> dict[str, Any]:
    """The optimizer service definition from docker-compose.yml.

    Returns:
        The service block.
    """
    services = _services()
    assert SERVICE in services, f"сервис {SERVICE} отсутствует в {COMPOSE_FILE}"
    return services[SERVICE]


# --- не разрушить запись ----------------------------------------------------


def test_script_never_deletes_a_recording() -> None:
    """Nothing in the script removes an original segment.

    The only path by which a recording may leave the directory is ``mv`` of a
    verified temporary file over it. A stray ``rm`` on the source — added later
    as an "obvious" cleanup — would delete footage that exists nowhere else
    yet, because the archiver has not uploaded it.
    """
    removals = [
        line for line in _code_lines(SCRIPT) if re.search(r"\brm\b", line) and '"$src"' in line
    ]
    assert not removals, f"скрипт удаляет оригинал записи: {removals}"


def test_replacement_happens_only_after_every_check() -> None:
    """``mv`` over the original is the last statement of the pipeline.

    Every guard above it (ffmpeg return code, frame count, duration drift, size
    gain, source still present) bails out with ``return`` before reaching the
    replacement. If ``mv`` moved above any of them, an unverified transcode
    would overwrite the recording.
    """
    lines = _code_lines(SCRIPT)
    move = next(i for i, line in enumerate(lines) if line.strip().startswith("mv "))
    guards = ("run_ffmpeg", "out_frames", "MAX_DURATION_DRIFT_SECONDS", "MIN_GAIN_PERCENT")
    for guard in guards:
        position = next(i for i, line in enumerate(lines) if guard in line)
        assert position < move, f"проверка {guard} стоит после подмены файла"

    # The "did the archiver take it?" check is the last thing before mv.
    presence = next(i for i, line in enumerate(lines) if '[ ! -f "$src" ]' in line)
    assert presence < move, "подмена не проверяет, что оригинал ещё на месте"


def test_gain_guard_cannot_be_switched_off() -> None:
    """The minimum-gain threshold is validated to at least one percent.

    On a noisy source the re-encode faithfully preserves the noise and the file
    grows — measured at 11 MB -> 19 MB. Allowing ``0`` would turn that into a
    silent archive inflation, so the guard has no "disabled" value.
    """
    validation = [line for line in _code_lines(SCRIPT) if "TACITUS_OPT_MIN_GAIN_PERCENT" in line]
    assert any("require_int" in line and " 1 99" in line for line in validation), (
        "порог экономии допускает значение 0"
    )


def test_original_mtime_is_carried_over() -> None:
    """The replacement keeps the age of the segment it replaces.

    ``mediamtx-archive-manager.py`` decides what to upload by mtime. A rewrite
    that stamped the file with the current time would reset the age of every
    recording it touched, and nothing would ever reach S3.
    """
    assert any('touch -r "$src" "$tmp"' in line for line in _code_lines(SCRIPT)), (
        "mtime оригинала не переносится на результат"
    )


def test_timeline_is_preserved_not_compressed() -> None:
    """Surviving frames keep their original timestamps.

    ``-fps_mode vfr`` keeps the PTS of the frames that survive, so an hour of
    recording stays an hour. ``setpts`` would renumber them and turn the archive
    into a time-lapse, silently moving every event to the wrong wall-clock time.
    """
    code = "\n".join(_code_lines(SCRIPT))
    assert "-fps_mode:v vfr" in code, "не задан -fps_mode vfr"
    assert "setpts" not in code, "setpts сдвинул бы таймлинию записи"


def test_custom_marker_tag_is_actually_written() -> None:
    """The "already processed" tag needs ``+use_metadata_tags`` in mp4.

    Without that flag ffmpeg silently drops an unknown ``-metadata`` key from an
    mp4 container: the tag never lands, ``is_optimized`` always answers no, and
    every segment is re-encoded on every sweep until quality is gone.
    """
    code = "\n".join(_code_lines(SCRIPT))
    assert "-metadata" in code and "MARKER_TAG" in code
    assert "use_metadata_tags" in code, "тег-маркер не переживёт запись в mp4"


# --- не тронуть файл, который ещё пишут -------------------------------------


def test_idle_threshold_agrees_with_the_archiver(optimizer: dict[str, Any]) -> None:
    """Both services read the same "segment is closed" threshold.

    MediaMTX appends to the open segment every second. If the optimizer and the
    archiver disagreed about when a file is finished, one of them would act on a
    file the other considers live — and the archiver's action is upload-then-
    delete.
    """
    cleanup = _services()[CLEANUP_SERVICE]
    optimizer_env = _environment(optimizer)
    cleanup_env = _environment(cleanup)

    assert "TACITUS_OPT_MIN_IDLE_SECONDS" in optimizer_env
    assert cleanup_env["MIN_IDLE_SECONDS"] == "${TACITUS_OPT_MIN_IDLE_SECONDS:-300}", (
        "mediamtx-cleanup читает свой порог простоя, а не общий с оптимизатором"
    )


def test_archiver_skips_the_optimizer_temporary_files() -> None:
    """A transcode in progress cannot be uploaded to S3.

    The optimizer writes ``.<name>.tacitus.tmp`` beside the segment it rewrites.
    The archiver walks the whole tree, so it has to recognise and skip that name
    — otherwise a truncated file is uploaded and the local copy deleted.
    """
    source = ARCHIVE_MANAGER.read_text(encoding="utf-8")
    assert 'name.startswith(".")' in source, "архиватор не пропускает скрытые файлы"

    suffix = re.search(r"TMP_SUFFIX=(\S+)", SCRIPT.read_text(encoding="utf-8"))
    assert suffix and suffix.group(1).endswith(".tmp")
    assert suffix.group(1).startswith("."), "временный файл не скрыт от архиватора"


def test_stale_temporary_files_are_cleaned_on_start() -> None:
    """A container killed mid-transcode leaves its temporary file behind.

    Nothing else removes it: the archiver now skips it by name, so without this
    sweep it would occupy disk until an operator noticed.
    """
    assert any("find" in line and "TMP_SUFFIX" in line for line in _code_lines(SCRIPT)), (
        "остатки прошлого запуска не убираются"
    )


# --- сервис в compose -------------------------------------------------------


def test_optimizer_is_not_profile_gated(optimizer: dict[str, Any]) -> None:
    """Unlike the simulator, this service runs in production.

    Ansible deploys with ``docker compose up -d --build`` and no ``--profile``
    (ansible/roles/app_deploy/tasks/main.yml), so a profile would mean the
    service never starts on a server. The switch is an environment flag instead.
    """
    assert "profiles" not in optimizer, (
        "сервис за профилем не поднимется в production: выключателем служит TACITUS_OPT_ENABLED"
    )


def test_optimizer_is_disabled_by_default(optimizer: dict[str, Any]) -> None:
    """An existing deployment does not start transcoding after an upgrade.

    Deployed hosts have no ``TACITUS_OPT_ENABLED`` in their ``.env``. Defaulting
    to on would make ``docker compose up`` begin re-encoding the whole archive on
    a machine whose CPU budget was sized without it.
    """
    assert _environment(optimizer)["TACITUS_OPT_ENABLED"] == "${TACITUS_OPT_ENABLED:-false}"
    assert "TACITUS_OPT_ENABLED=false" in ENV_EXAMPLE.read_text(encoding="utf-8")


def test_optimizer_shares_the_mediamtx_image(optimizer: dict[str, Any]) -> None:
    """No new image enters the stack for ffmpeg.

    ``bluenviron/mediamtx:1.15.1-ffmpeg`` already carries ffmpeg with avfilter
    and ffprobe, and it is already pinned and already deployed. Adding ffmpeg to
    ``tacitus-app`` instead would put a media stack into the image that serves
    the API.
    """
    assert optimizer["image"] == _services()[MEDIAMTX_SERVICE]["image"]


def test_optimizer_overrides_the_image_entrypoint(optimizer: dict[str, Any]) -> None:
    """The container runs the script, not a second RTSP server.

    The image's own entrypoint is ``/mediamtx``.
    """
    assert optimizer["entrypoint"] == ["/bin/sh", "/optimize-recordings.sh"]


def test_optimizer_does_not_read_the_env_file(optimizer: dict[str, Any]) -> None:
    """The service sees only its own settings.

    Every other service reads ``.env`` whole. This one rewrites video files and
    has no reason to hold the JWT secret, the database password or the S3 keys.
    """
    assert "env_file" not in optimizer
    declared = "\n".join(optimizer.get("environment", []))
    for secret in FOREIGN_SECRETS:
        assert secret not in declared, f"{secret} не нужен обработчику записей"


def test_optimizer_is_confined(optimizer: dict[str, Any]) -> None:
    """Container hardening matches the other ffmpeg service.

    Read-only root, no capabilities, the same unprivileged uid MediaMTX writes
    recordings with — otherwise the replacement file would be owned by root and
    MediaMTX could not manage its own directory.
    """
    assert optimizer["read_only"] is True
    assert optimizer["cap_drop"] == ["ALL"]
    assert optimizer["user"] == "1000:1000"
    assert "/tmp" in optimizer["tmpfs"]


def test_optimizer_mounts_recordings_writable_and_script_readonly(
    optimizer: dict[str, Any],
) -> None:
    """The script cannot be modified from inside the container.

    Recordings, in contrast to the simulator, are mounted writable: replacing a
    segment in place is the entire job.
    """
    volumes = optimizer["volumes"]
    assert "./recordings:/recordings" in volumes, "каталог записей примонтирован не на запись"
    assert "./scripts/optimize-recordings.sh:/optimize-recordings.sh:ro" in volumes


def test_optimizer_has_its_own_cpu_limit(optimizer: dict[str, Any]) -> None:
    """Transcoding cannot starve stream ingestion.

    The limit lives on this container precisely so it is separate from
    MediaMTX's own — the process that must never stutter is the one receiving
    RTSP.
    """
    limits = optimizer["deploy"]["resources"]["limits"]
    assert "cpus" in limits and "memory" in limits


def test_optimizer_env_vars_are_documented(optimizer: dict[str, Any]) -> None:
    """Every knob the script reads is declared in compose and in .env.example.

    A variable the script honours but nobody documents is a setting only its
    author knows about.
    """
    used = set(re.findall(r"\$\{(TACITUS_OPT_[A-Z_]+):-", SCRIPT.read_text(encoding="utf-8")))
    declared = set(_environment(optimizer))
    documented = set(
        re.findall(r"^(TACITUS_OPT_[A-Z_]+)=", ENV_EXAMPLE.read_text(encoding="utf-8"), re.M)
    )

    # RECORDINGS_PATH is a test seam, not an operator knob: the mount point is
    # fixed by the compose volume.
    used.discard("TACITUS_OPT_RECORDINGS_PATH")

    assert not used - declared, f"переменные скрипта отсутствуют в compose: {used - declared}"
    assert not used - documented, f"переменные не описаны в .env.example: {used - documented}"


def test_healthcheck_expects_the_optimizer_to_run() -> None:
    """The container is part of the production stack, so its absence is a fault.

    It stays up even when disabled — the flag makes it idle, not gone — which is
    what makes "not running" a meaningful signal.
    """
    content = HEALTHCHECK.read_text(encoding="utf-8")
    long_running = content.split("LONG_RUNNING_CONTAINERS=", 1)[1].split('"', 2)[1]
    assert "tacitus-recording-optimizer" in long_running


# --- скрипт -----------------------------------------------------------------


def test_script_targets_posix_sh() -> None:
    """The script is POSIX sh, because the image has no bash.

    ``bluenviron/mediamtx:1.15.1-ffmpeg`` ships busybox ash only. A bashism is
    not a style problem here — it is a runtime crash in a container nobody is
    looking at.
    """
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "#!/bin/sh"

    bashisms = re.compile(r"\[\[|\blocal\s|pipefail|<<<|\$\(\(.*\+\+")
    offenders = [line for line in _code_lines(SCRIPT) if bashisms.search(line)]
    assert not offenders, f"bash-измы в {SCRIPT}: {offenders}"


def test_script_declares_its_exit_codes() -> None:
    """The header says what a non-zero exit means for the operator."""
    assert "Коды выхода" in SCRIPT.read_text(encoding="utf-8")[:3000]


def test_script_pins_ffmpeg_loglevel() -> None:
    """ffmpeg output is fixed at ``error``.

    Per-frame chatter from an hour-long transcode would bury the one line that
    matters — the metric written when a segment is replaced.
    """
    assert any("-loglevel error" in line for line in _code_lines(SCRIPT))


def test_disabled_service_stays_alive() -> None:
    """Turning the optimizer off must not produce a restart loop.

    The container is declared without a profile and with
    ``restart: unless-stopped``, so exiting when disabled would make Docker
    restart it forever and fill the log of an operator who simply left it off.
    """
    code = "\n".join(_code_lines(SCRIPT))
    disabled = code.split('"$ENABLED" != "true"', 1)[1][:200]
    assert "sleep" in disabled and "exit" not in disabled
