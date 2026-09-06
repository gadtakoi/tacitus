"""Tests for scripts/init_project.py.

The script creates the first application user during project initialization.
What is covered here:

* the generated password always satisfies the registration policy — otherwise
  ``--generate`` would be unusable;
* neither the validation output nor the error messages echo the password back;
* the documented precedence of password/username sources;
* against the database: the user is really created and the second run is a
  no-op that leaves the existing password alone (idempotency).
"""

import importlib.util
import io
from types import ModuleType

import pytest

from app.db.repositories.auth import UserRepository
from app.schemas.auth import RegisterRequest
from app.services.auth_service import verify_password


def _load_init_project() -> ModuleType:
    """Load the script as a module, the same way the archive-manager test does.

    Returns:
        Imported module object.
    """
    spec = importlib.util.spec_from_file_location("init_project", "scripts/init_project.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def init_project() -> ModuleType:
    """Provide the loaded script module to the tests."""
    return _load_init_project()


def test_generated_password_passes_the_registration_policy(init_project: ModuleType) -> None:
    """Every generated password is accepted by ``RegisterRequest``.

    The generator draws one character per required class, so this is a
    property, not a coincidence — but the policy and the generator live in
    different files, and this test is what ties them together.
    """
    for _ in range(50):
        password = init_project.generate_password()
        assert len(password) == init_project.GENERATED_PASSWORD_LENGTH
        # Raises ValidationError if any policy rule is violated.
        RegisterRequest(username="init_user", password=password)


def test_generated_passwords_are_not_repeated(init_project: ModuleType) -> None:
    """Two consecutive calls do not return the same secret."""
    assert init_project.generate_password() != init_project.generate_password()


def test_generate_password_rejects_impossible_length(init_project: ModuleType) -> None:
    """A length that cannot hold every required class is refused."""
    with pytest.raises(ValueError):
        init_project.generate_password(length=3)


def test_validation_error_does_not_leak_the_password(init_project: ModuleType) -> None:
    """A rejected password never appears in the operator-facing message.

    ``str(ValidationError)`` includes ``input_value``; printing it would put
    the password on the terminal and into whatever captures the script output.
    """
    weak_password = "weak-secret-value"

    with pytest.raises(init_project.InitError) as exc_info:
        init_project.validate_credentials("admin", weak_password)

    assert exc_info.value.exit_code == init_project.EXIT_USAGE
    assert weak_password not in exc_info.value.message
    # The reason is still reported.
    assert "password" in exc_info.value.message.lower()


def test_validate_credentials_accepts_a_policy_compliant_pair(init_project: ModuleType) -> None:
    """A valid username/password pair passes without raising."""
    init_project.validate_credentials("admin", "Str0ng-Password!")


def test_password_is_read_from_stdin(
    init_project: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--password-stdin`` takes the first line and keeps inner spaces."""
    monkeypatch.setattr(init_project.sys, "stdin", io.StringIO("Str0ng Pass!\nignored\n"))

    password, generated = init_project.resolve_password(
        from_stdin=True,
        generate=False,
        interactive=False,
    )

    assert password == "Str0ng Pass!"
    assert generated is False


def test_empty_stdin_password_is_a_usage_error(
    init_project: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty stdin line is rejected instead of creating a passwordless user."""
    monkeypatch.setattr(init_project.sys, "stdin", io.StringIO("\n"))

    with pytest.raises(init_project.InitError) as exc_info:
        init_project.resolve_password(from_stdin=True, generate=False, interactive=False)

    assert exc_info.value.exit_code == init_project.EXIT_USAGE


def test_environment_password_wins_over_generation(
    init_project: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The environment variable is preferred over ``--generate``."""
    monkeypatch.setenv(init_project.ENV_PASSWORD, "Env-Passw0rd!")

    password, generated = init_project.resolve_password(
        from_stdin=False,
        generate=True,
        interactive=False,
    )

    assert password == "Env-Passw0rd!"
    assert generated is False


def test_generated_password_is_marked_as_generated(
    init_project: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--generate`` reports the flag that allows printing the value once."""
    monkeypatch.delenv(init_project.ENV_PASSWORD, raising=False)

    password, generated = init_project.resolve_password(
        from_stdin=False,
        generate=True,
        interactive=False,
    )

    assert generated is True
    RegisterRequest(username="init_user", password=password)


def test_missing_password_source_is_a_usage_error(
    init_project: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a source and without a terminal the script refuses to guess."""
    monkeypatch.delenv(init_project.ENV_PASSWORD, raising=False)

    with pytest.raises(init_project.InitError) as exc_info:
        init_project.resolve_password(from_stdin=False, generate=False, interactive=False)

    assert exc_info.value.exit_code == init_project.EXIT_USAGE


def test_username_precedence(init_project: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """The command-line argument wins over the environment variable."""
    monkeypatch.setenv(init_project.ENV_USERNAME, "from-env")

    assert init_project.resolve_username(" cli-user ", interactive=False) == "cli-user"
    assert init_project.resolve_username(None, interactive=False) == "from-env"


def test_missing_username_is_a_usage_error(
    init_project: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-interactive run never falls back to a default account name."""
    monkeypatch.delenv(init_project.ENV_USERNAME, raising=False)

    with pytest.raises(init_project.InitError) as exc_info:
        init_project.resolve_username(None, interactive=False)

    assert exc_info.value.exit_code == init_project.EXIT_USAGE


@pytest.mark.anyio
async def test_create_initial_user_creates_a_usable_account(
    init_project: ModuleType,
    db_session,
) -> None:
    """The created account carries a verifiable hash of the given password."""
    created = await init_project.create_initial_user(db_session, "init_admin", "Str0ng-Password!")

    assert created is True
    user = await UserRepository(db_session).get_by_username("init_admin")
    assert user is not None
    assert user.is_active is True
    # The password is stored hashed and verifies through the login path.
    assert user.password_hash != "Str0ng-Password!"
    assert verify_password("Str0ng-Password!", user.password_hash) is True


@pytest.mark.anyio
async def test_second_run_is_a_no_op(init_project: ModuleType, db_session) -> None:
    """Re-running the script keeps the existing password instead of resetting it."""
    await init_project.create_initial_user(db_session, "init_admin", "Str0ng-Password!")
    original = await UserRepository(db_session).get_by_username("init_admin")
    assert original is not None
    original_hash = original.password_hash

    created = await init_project.create_initial_user(db_session, "init_admin", "An0ther-Password!")

    assert created is False
    user = await UserRepository(db_session).get_by_username("init_admin")
    assert user is not None
    assert user.password_hash == original_hash
    assert verify_password("Str0ng-Password!", user.password_hash) is True
