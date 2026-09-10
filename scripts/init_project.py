#!/usr/bin/env python3
"""Project initialization and account management script.

Right now the project needs exactly one initialization step that no container
performs on its own: creating the first application user, without which the UI
and the API cannot be logged into. Everything else is already provisioned by
docker compose — the database schema by the ``db-migrations`` service, the S3
bucket by ``minio-init``.

The same script disables and re-enables accounts (``--disable`` / ``--enable``).
With ``TACITUS_REGISTRATION_ENABLED=false`` — the default for a deployed stack —
this is the only way accounts are created, so it has to be the way they are shut
down as well.

The script is idempotent. A second run with the same username leaves the
existing account untouched — the password is never overwritten — and exits 0,
so calling it from a deploy pipeline is safe.

The password is deliberately NOT accepted as a command-line argument: argv is
readable by every process on the host through ``ps`` and is written to shell
history. Sources are tried in this order:

  1. ``--password-stdin``      — first line of stdin (pipe or file);
  2. ``TACITUS_INIT_PASSWORD`` — environment variable;
  3. ``--generate``            — random password, printed once;
  4. interactive prompt        — asked twice, with echo disabled.

Usage::

    python scripts/init_project.py --username admin
    python scripts/init_project.py --username admin --generate
    printf '%s' "$password" | python scripts/init_project.py -u admin --password-stdin
    python scripts/init_project.py --disable olduser
    python scripts/init_project.py --enable olduser

Normally invoked through ``scripts/init.sh``, which runs it inside the ``app``
container where ``TACITUS_DATABASE_URL`` already points at PostgreSQL.

Exit codes:
    0 — the user was created, already existed, or the account flag was flipped;
    1 — the database is unreachable, the schema is missing, or another failure;
    2 — invalid arguments, credentials rejected by the password policy, or the
        account named by ``--disable``/``--enable`` does not exist.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import string
import sys
from getpass import getpass
from pathlib import Path

# The script lives in ``scripts/`` but imports the application package, so the
# repository root has to be on sys.path regardless of the current directory:
# ``python scripts/init_project.py`` puts ``scripts/`` there, not the root.
# Doing it explicitly keeps a run from a checkout and a run inside the
# container (``docker compose exec app python scripts/init_project.py``)
# behaving identically.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.exceptions import AppError  # noqa: E402
from app.core.logging_config import setup_logging  # noqa: E402
from app.db.engine import AsyncSessionLocal, engine  # noqa: E402
from app.schemas.auth import RegisterRequest  # noqa: E402
from app.services.auth_service import AuthError, register_user, set_user_active  # noqa: E402

# Exit codes. Separating "bad input" from "the system is not ready" lets a
# caller retry the second and never the first.
EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2

# Offered as the default at the interactive prompt only. A non-interactive run
# must name the user explicitly, so a scripted invocation never silently
# creates an account nobody asked for.
DEFAULT_USERNAME = "admin"

# Length of a generated password. Well above the 8-character policy minimum:
# the operator stores it in a password manager, so extra length is free.
GENERATED_PASSWORD_LENGTH = 20

# Character classes required by the password policy in ``RegisterRequest``.
# The set is copied from that schema on purpose — the generator has to draw
# from characters the validator accepts, and a generated password that failed
# validation would make the script unusable.
SPECIAL_CHARACTERS = "!@#$%^&*()_+-=[]{}|;:,.<>?"

# Environment variables read by the script. Kept as constants so the names
# appear once and stay in sync with the documentation.
ENV_USERNAME = "TACITUS_INIT_USERNAME"
ENV_PASSWORD = "TACITUS_INIT_PASSWORD"


class InitError(Exception):
    """Fatal error carrying an operator-facing message and an exit code."""

    def __init__(self, message: str, exit_code: int = EXIT_FAILURE) -> None:
        self.message = message
        self.exit_code = exit_code
        super().__init__(message)


def generate_password(length: int = GENERATED_PASSWORD_LENGTH) -> str:
    """Generate a random password that satisfies the registration policy.

    Randomness comes from ``secrets`` (a CSPRNG), never from ``random``, whose
    Mersenne Twister state can be reconstructed from its own output.

    One character is drawn from each class the policy requires and the rest
    from the full alphabet, then the result is shuffled — otherwise the first
    four positions would always hold the same classes in the same order, which
    is exactly the structure an attacker would exploit first.

    Args:
        length: Number of characters to generate.

    Returns:
        Generated password.

    Raises:
        ValueError: If ``length`` cannot hold one character of each required
            class.
    """
    required_classes = (
        string.ascii_uppercase,
        string.ascii_lowercase,
        string.digits,
        SPECIAL_CHARACTERS,
    )
    if length < len(required_classes):
        msg = f"Password length must be at least {len(required_classes)}"
        raise ValueError(msg)

    alphabet = "".join(required_classes)
    characters = [secrets.choice(class_) for class_ in required_classes]
    characters += [secrets.choice(alphabet) for _ in range(length - len(required_classes))]
    secrets.SystemRandom().shuffle(characters)
    return "".join(characters)


def format_validation_errors(exc: ValidationError) -> str:
    """Render a pydantic validation error without echoing the input value.

    ``str(exc)`` embeds ``input_value`` in its output, which for the password
    field is the password itself — it would reach the terminal and any log
    capturing the script's stderr. Only the field location and the message are
    taken from each error.

    Args:
        exc: Validation error raised by ``RegisterRequest``.

    Returns:
        Multi-line human-readable description of the rejected fields.
    """
    lines = []
    for error in exc.errors():
        field = ".".join(str(part) for part in error["loc"]) or "value"
        lines.append(f"  {field}: {error['msg']}")
    return "\n".join(lines)


def validate_credentials(username: str, password: str) -> None:
    """Validate credentials against the same rules the API enforces.

    Reusing ``RegisterRequest`` guarantees an account created here could also
    have been created through ``POST /auth/register``: same username charset,
    same length bounds, same password strength policy. Checking before
    touching the database also means a policy violation costs no round trip.

    Args:
        username: Login name to validate.
        password: Plain text password to validate.

    Raises:
        InitError: If either value violates the registration schema.
    """
    try:
        RegisterRequest(username=username, password=password)
    except ValidationError as exc:
        msg = f"Учётные данные не соответствуют политике:\n{format_validation_errors(exc)}"
        raise InitError(msg, EXIT_USAGE) from exc


def resolve_username(cli_username: str | None, *, interactive: bool) -> str:
    """Determine the username to create.

    Order: command-line argument, environment variable, interactive prompt.

    Args:
        cli_username: Value of ``--username``, or ``None``.
        interactive: Whether a prompt may be shown (stdin is a terminal).

    Returns:
        Username, stripped of surrounding whitespace.

    Raises:
        InitError: If no username is available and prompting is impossible.
    """
    if cli_username:
        return cli_username.strip()

    env_username = os.environ.get(ENV_USERNAME, "").strip()
    if env_username:
        return env_username

    if not interactive:
        msg = f"Не задан логин: укажите --username или переменную {ENV_USERNAME}"
        raise InitError(msg, EXIT_USAGE)

    entered = input(f"Логин [{DEFAULT_USERNAME}]: ").strip()
    return entered or DEFAULT_USERNAME


def read_password_from_stdin() -> str:
    """Read a password from the first line of stdin.

    Only the line terminator is stripped: spaces can legitimately be part of a
    password, and silently trimming them would create an account whose
    password nobody can reproduce.

    Returns:
        Password read from stdin.

    Raises:
        InitError: If stdin is empty.
    """
    line = sys.stdin.readline()
    password = line.rstrip("\n").rstrip("\r")
    if not password:
        raise InitError("Пустой пароль на stdin", EXIT_USAGE)
    return password


def prompt_password() -> str:
    """Ask for a password twice with echo disabled.

    Returns:
        Password entered by the operator.

    Raises:
        InitError: If the input is empty or the two entries differ.
    """
    password = getpass("Пароль: ")
    if not password:
        raise InitError("Пустой пароль", EXIT_USAGE)
    if password != getpass("Повторите пароль: "):
        raise InitError("Пароли не совпадают", EXIT_USAGE)
    return password


def resolve_password(
    *,
    from_stdin: bool,
    generate: bool,
    interactive: bool,
) -> tuple[str, bool]:
    """Determine the password to set, following the documented source order.

    Args:
        from_stdin: Whether ``--password-stdin`` was passed.
        generate: Whether ``--generate`` was passed.
        interactive: Whether a prompt may be shown (stdin is a terminal).

    Returns:
        Tuple of ``(password, was_generated)``. ``was_generated`` decides
        whether the value may be printed: a password the operator supplied is
        never echoed back.

    Raises:
        InitError: If no source is available.
    """
    if from_stdin:
        return read_password_from_stdin(), False

    env_password = os.environ.get(ENV_PASSWORD)
    if env_password:
        return env_password, False

    if generate:
        return generate_password(), True

    if interactive:
        return prompt_password(), False

    msg = (
        "Не задан пароль. В неинтерактивном режиме используйте один из вариантов:\n"
        "  --password-stdin (передать пароль по конвейеру),\n"
        f"  переменную окружения {ENV_PASSWORD},\n"
        "  --generate (сгенерировать и напечатать пароль один раз)."
    )
    raise InitError(msg, EXIT_USAGE)


async def create_initial_user(session: AsyncSession, username: str, password: str) -> bool:
    """Create the application user if it does not exist yet.

    Delegates to the same service function ``POST /auth/register`` uses, so the
    account is indistinguishable from an API-registered one: identical hashing
    scheme and the same ``auth.register`` security event in the audit log.
    A hand-written INSERT here could drift from the login path, and a user
    whose hash scheme does not match simply could not sign in.

    Args:
        session: Async database session.
        username: Login name, already validated.
        password: Plain text password, already validated.

    Returns:
        ``True`` if the account was created, ``False`` if a user with this
        username already existed. The existing password is left untouched:
        overwriting it would turn a repeated deploy into a silent credential
        reset.

    Raises:
        InitError: If the database rejects the operation.
    """
    try:
        await register_user(session, username, password, None)
    except AuthError:
        # ``register_user`` raises AuthError only for a username that is taken.
        # This also settles the race between two concurrent runs: the unique
        # constraint picks the winner, and the loser reports "already exists"
        # instead of failing initialization.
        return False
    except AppError as exc:
        msg = (
            f"Не удалось создать пользователя: {exc.message}. "
            "Проверьте, что база доступна и миграции применены "
            "(docker compose up -d db-migrations)."
        )
        raise InitError(msg, EXIT_FAILURE) from exc
    return True


async def toggle_account(session: AsyncSession, username: str, active: bool) -> bool:
    """Enable or disable an existing account.

    Disabling is the counterpart to closing registration: with
    ``TACITUS_REGISTRATION_ENABLED=false`` accounts are created here, so they
    have to be revocable here too — otherwise the only way to shut one down is
    an UPDATE typed into psql by hand.

    Args:
        session: Async database session.
        username: Login name of the account.
        active: ``True`` to enable, ``False`` to disable.

    Returns:
        ``True`` if the account exists and was updated, ``False`` if there is
        no such user.

    Raises:
        InitError: If the database rejects the operation.
    """
    try:
        return await set_user_active(session, username, active)
    except AppError as exc:
        msg = (
            f"Не удалось изменить состояние учётной записи: {exc.message}. "
            "Проверьте, что база доступна и миграции применены."
        )
        raise InitError(msg, EXIT_FAILURE) from exc


async def run(username: str, password: str) -> bool:
    """Open a database session, create the user and release the engine.

    Args:
        username: Login name to create.
        password: Plain text password.

    Returns:
        ``True`` if the account was created, ``False`` if it already existed.

    Raises:
        InitError: If the database cannot be reached.
    """
    try:
        async with AsyncSessionLocal() as session:
            return await create_initial_user(session, username, password)
    except (SQLAlchemyError, OSError) as exc:
        # The connection string is never included in the message: it carries
        # the database password.
        msg = (
            f"Нет соединения с базой данных ({type(exc).__name__}). "
            "Проверьте, что PostgreSQL запущен и TACITUS_DATABASE_URL указывает на него."
        )
        raise InitError(msg, EXIT_FAILURE) from exc
    finally:
        # Without this the pooled asyncpg connections keep the event loop's
        # transports open and the interpreter exits with a warning.
        await engine.dispose()


async def run_toggle(username: str, active: bool) -> bool:
    """Open a database session, flip the account flag and release the engine.

    Args:
        username: Login name of the account.
        active: ``True`` to enable, ``False`` to disable.

    Returns:
        ``True`` if the account exists and was updated.

    Raises:
        InitError: If the database cannot be reached.
    """
    try:
        async with AsyncSessionLocal() as session:
            return await toggle_account(session, username, active)
    except (SQLAlchemyError, OSError) as exc:
        msg = (
            f"Нет соединения с базой данных ({type(exc).__name__}). "
            "Проверьте, что PostgreSQL запущен и TACITUS_DATABASE_URL указывает на него."
        )
        raise InitError(msg, EXIT_FAILURE) from exc
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Returns:
        Configured argument parser.
    """
    parser = argparse.ArgumentParser(
        prog="init_project.py",
        description="Инициализация проекта: создание пользователя для входа в приложение.",
        epilog=(
            "Пароль нельзя передать аргументом командной строки: argv виден в "
            "выводе ps и попадает в историю оболочки."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-u",
        "--username",
        help=f"Логин создаваемого пользователя (или переменная {ENV_USERNAME})",
    )
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="Прочитать пароль из первой строки stdin",
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Сгенерировать пароль и напечатать его один раз",
    )
    # Отключение и включение исключают друг друга и не сочетаются с созданием:
    # это операции над уже существующей записью, пароль для них не нужен.
    status_group = parser.add_mutually_exclusive_group()
    status_group.add_argument(
        "--disable",
        metavar="USERNAME",
        help="Отключить учётную запись и отозвать её refresh-токены",
    )
    status_group.add_argument(
        "--enable",
        metavar="USERNAME",
        help="Включить ранее отключённую учётную запись",
    )
    return parser


def handle_account_status(username: str, active: bool) -> int:
    """Run the enable/disable path and report the result.

    Args:
        username: Login name of the account.
        active: ``True`` to enable, ``False`` to disable.

    Returns:
        Process exit code.
    """
    updated = asyncio.run(run_toggle(username.strip(), active))
    if not updated:
        # Не идемпотентный случай, а отсутствие цели: молчаливый успех здесь
        # означал бы, что оператор считает отключённой запись, которой нет.
        print(f"Ошибка: пользователь '{username}' не найден.", file=sys.stderr)
        return EXIT_USAGE

    if active:
        print(f"Учётная запись '{username}' включена.")
    else:
        print(f"Учётная запись '{username}' отключена, активные сессии прекращены.")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """Entry point.

    Args:
        argv: Argument list, defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code.
    """
    args = build_parser().parse_args(argv)

    # Same logging setup as the application, so the ``auth.register`` security
    # event emitted by the service layer lands in the usual place instead of
    # being dropped by a root logger with no handlers.
    setup_logging()

    # A prompt is only possible when stdin is a terminal. ``--password-stdin``
    # consumes stdin itself, so it rules prompting out as well.
    interactive = sys.stdin.isatty() and not args.password_stdin

    if args.disable or args.enable:
        try:
            return handle_account_status(
                args.disable or args.enable,
                active=bool(args.enable),
            )
        except InitError as exc:
            print(f"Ошибка: {exc.message}", file=sys.stderr)
            return exc.exit_code

    try:
        username = resolve_username(args.username, interactive=interactive)
        password, generated = resolve_password(
            from_stdin=args.password_stdin,
            generate=args.generate,
            interactive=interactive,
        )
        validate_credentials(username, password)
        created = asyncio.run(run(username, password))
    except InitError as exc:
        print(f"Ошибка: {exc.message}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("Прервано пользователем.", file=sys.stderr)
        return EXIT_USAGE

    if not created:
        # Идемпотентность: повторный запуск ошибкой не считается.
        print(f"Пользователь '{username}' уже существует, пароль не изменён.")
        return EXIT_OK

    print(f"Пользователь '{username}' создан.")
    if generated:
        print("Сгенерированный пароль (показывается один раз, сохраните его):")
        print(f"  {password}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
