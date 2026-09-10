"""Read-only projection of the application configuration for the web UI.

The service answers one question: which environment variables is this process
actually running with? It reads the already-loaded ``Settings`` instance rather
than the environment or ``.env``, so what the UI shows is the effective
configuration, including defaults for variables that were never set.

Classification is allow-list based, as required by ``.ai/system/security-rules``:

* ``_FIELD_GROUPS`` names every known setting and the group it is displayed in.
* ``_SECRET_FIELDS`` names the settings whose value must never be returned.
* ``_URL_CREDENTIAL_FIELDS`` names settings that are URLs which may embed a
  password; those are returned with the password removed.

A field that is not listed in ``_FIELD_GROUPS`` — a newly added setting nobody
classified yet — is treated as a secret and shown masked. Failing closed means
a new credential cannot leak through this endpoint by omission;
``tests/test_settings_service.py`` fails on such a field so the omission is
noticed rather than silently tolerated.
"""

from urllib.parse import urlsplit

from app.core.config import Settings, settings
from app.schemas.settings import SettingItem

_ENV_PREFIX = "TACITUS_"

_GROUP_APP = "Приложение"
_GROUP_S3 = "Хранилище S3"
_GROUP_AUTH = "Аутентификация"
_GROUP_NETWORK = "Сеть и безопасность"
_GROUP_LOGGING = "Логирование"
_GROUP_DATABASE = "База данных"
_GROUP_MEDIAMTX = "MediaMTX"
_GROUP_RATE_LIMIT = "Ограничение частоты запросов"
_GROUP_SENTRY = "Sentry"
_GROUP_OTHER = "Прочее"

# Every field of ``Settings`` and the group it is displayed in. Groups are
# emitted in the order of first appearance here, so this mapping also fixes the
# order of the sections in the UI.
_FIELD_GROUPS: dict[str, str] = {
    "app_name": _GROUP_APP,
    "environment": _GROUP_APP,
    "debug": _GROUP_APP,
    "host": _GROUP_APP,
    "port": _GROUP_APP,
    "cors_origins": _GROUP_NETWORK,
    "trusted_proxy_hops": _GROUP_NETWORK,
    "jwt_secret": _GROUP_AUTH,
    "jwt_algorithm": _GROUP_AUTH,
    "jwt_expiration_minutes": _GROUP_AUTH,
    "jwt_refresh_expiration_days": _GROUP_AUTH,
    "database_url": _GROUP_DATABASE,
    "s3_endpoint": _GROUP_S3,
    "s3_bucket": _GROUP_S3,
    "s3_access_key": _GROUP_S3,
    "s3_secret_key": _GROUP_S3,
    "presigned_url_expiration": _GROUP_S3,
    "mtx_api_url": _GROUP_MEDIAMTX,
    "mtx_api_user": _GROUP_MEDIAMTX,
    "mtx_api_password": _GROUP_MEDIAMTX,
    "rate_limit_storage_uri": _GROUP_RATE_LIMIT,
    "log_level": _GROUP_LOGGING,
    "log_format": _GROUP_LOGGING,
    "log_destination": _GROUP_LOGGING,
    "log_file_path": _GROUP_LOGGING,
    "log_max_bytes": _GROUP_LOGGING,
    "log_backup_count": _GROUP_LOGGING,
    "sentry_dsn": _GROUP_SENTRY,
    "sentry_environment": _GROUP_SENTRY,
    "sentry_traces_sample_rate": _GROUP_SENTRY,
}

# Values that are credentials in their own right. Never returned, not even
# partially: a prefix or a length is still information about a secret.
_SECRET_FIELDS = frozenset(
    {
        "jwt_secret",
        "s3_access_key",
        "s3_secret_key",
        "mtx_api_password",
        # The Sentry DSN embeds the project's ingest key.
        "sentry_dsn",
    }
)

# Connection URLs: host and database name are operationally useful, the
# password inside them is not shown.
_URL_CREDENTIAL_FIELDS = frozenset({"database_url", "rate_limit_storage_uri"})

_MASK = "***"


def _redact_url_credentials(value: str) -> str:
    """Strip the password (and query string) out of a connection URL.

    The user name is kept — it identifies the account without granting access —
    while the password is replaced by a mask. The query string is dropped
    wholesale because some drivers accept credentials there
    (``?password=...``), and this function cannot know every such spelling.

    Args:
        value: Connection URL, possibly without credentials.

    Returns:
        The URL with credentials removed, or a full mask if it cannot be parsed.
    """
    if not value:
        return value
    try:
        parts = urlsplit(value)
        username, password, hostname, port = (
            parts.username,
            parts.password,
            parts.hostname,
            parts.port,
        )
    except ValueError:
        # Unparseable URL: withhold it entirely rather than guess where the
        # credentials end.
        return _MASK
    if password is None and not parts.query:
        return value
    host = hostname or ""
    if port:
        host = f"{host}:{port}"
    if username:
        host = f"{username}:{_MASK}@{host}"
    elif password is not None:
        host = f"{_MASK}@{host}"
    return f"{parts.scheme}://{host}{parts.path}"


def _describe(field: str) -> SettingItem:
    """Build the UI representation of a single setting.

    Args:
        field: Attribute name on ``Settings``.

    Returns:
        The setting with its value masked or redacted as its classification
        requires.
    """
    raw = getattr(settings, field)
    text = str(raw)
    # Unclassified fields fall through to the secret branch on purpose; see the
    # module docstring.
    sensitive = field in _SECRET_FIELDS or field not in _FIELD_GROUPS
    if sensitive:
        value = None
    elif field in _URL_CREDENTIAL_FIELDS:
        value = _redact_url_credentials(text)
    else:
        value = text
    return SettingItem(
        name=f"{_ENV_PREFIX}{field.upper()}",
        group=_FIELD_GROUPS.get(field, _GROUP_OTHER),
        value=value,
        sensitive=sensitive,
        is_set=bool(text),
    )


def list_settings() -> list[SettingItem]:
    """Return every application setting, grouped and with secrets withheld.

    Returns:
        Settings ordered by group, following the order of ``_FIELD_GROUPS``;
        unclassified fields come last.
    """
    known = [field for field in _FIELD_GROUPS if field in Settings.model_fields]
    unknown = [field for field in Settings.model_fields if field not in _FIELD_GROUPS]
    return [_describe(field) for field in (*known, *unknown)]
