"""Central configuration. Every environment variable Jarvis reads is defined here.

Accessors read ``os.environ`` at CALL time, not import time, so tests can
``monkeypatch.setenv()`` and callers observe the change (82 tests depend on
this). The few genuinely frozen-at-import values are marked with a
``*_default()`` accessor and documented in their docstring; the importing
module captures the value once at import.

Variables are grouped below by subsystem.

LLM / Supervisor
----------------
- ``JARVIS_LLM_PROVIDER`` — which provider adapter to use.
- ``JARVIS_LLM_BASE_URL`` — provider API endpoint.
- ``JARVIS_LLM_MODEL`` — model identifier sent to the provider.
- ``JARVIS_LLM_API_KEY`` — provider API key.
- ``JARVIS_LLM_FREE_ONLY`` — when true, filter routes to free models only.
- ``JARVIS_SUPERVISOR_ENABLED`` — enable the conversational supervisor.
- ``JARVIS_TEST_MODE`` — set to ``1`` to enable test-only behavior.

OpenCode integration
--------------------
- ``JARVIS_OPENCODE_EXE`` — path to the opencode executable.
- ``JARVIS_OPENCODE_OWNER_MARKER`` — path to the ownership marker file.
- ``JARVIS_OPENCODE_PORT`` — port for the managed OpenCode server.
- ``JARVIS_OPENCODE_RUNTIME_DIR`` — isolated OpenCode runtime directory.
- ``JARVIS_OPENCODE_OPENROUTER_KEY`` — OpenRouter key for isolated auth.
- ``JARVIS_OPENCODE_MODEL_PROVIDER_ID`` — provider id for OpenCode delegation.
- ``JARVIS_OPENCODE_ALLOW_PAID`` — allow paid models in OpenCode delegation.
- ``JARVIS_OPENCODE_MODEL_ID`` — specific paid model id for delegation.
- ``JARVIS_OPENCODE_CREDIT_THRESHOLD_USD`` — minimum OpenRouter credit balance.
- ``OPENCODE_SERVER_USERNAME`` — HTTP Basic auth username for opencode serve.
- ``OPENCODE_SERVER_PASSWORD`` — HTTP Basic auth password for opencode serve.

WebSocket tokens (ADR-014)
--------------------------
- ``JARVIS_WS_TOKEN_SECRET`` — HS256 signing secret.
- ``JARVIS_WS_TOKEN_TTL_SECONDS`` — token lifetime in seconds.

Web Push (VAPID)
----------------
- ``JARVIS_VAPID_PUBLIC_KEY`` — VAPID public key exposed to browsers.
- ``JARVIS_VAPID_PRIVATE_KEY`` — VAPID private key (secret).
- ``JARVIS_VAPID_SUBJECT`` — VAPID subject claim.

Operations console (ADR-022/023)
--------------------------------
- ``JARVIS_OPERATIONS_TARGET_URL`` — Jarvis HTTPS endpoint the console controls.
- ``JARVIS_OPERATIONS_PORT`` — port the operations console HTTP server binds to.
- ``JARVIS_OPERATIONS_VERIFY_TLS`` — verify TLS when the console talks to Jarvis.
- ``JARVIS_OPERATIONS_CWD`` — working directory for the managed Jarvis process.
- ``JARVIS_OPERATIONS_JARVIS_OWNER_MARKER`` — Jarvis ownership marker file.
- ``JARVIS_OPERATIONS_DB`` — SQLite path for operation history.
- ``JARVIS_SSL_KEYFILE`` — TLS key file for HTTPS.
- ``JARVIS_SSL_CERTFILE`` — TLS certificate file for HTTPS.
- ``JARVIS_BIND_HOST`` — host the main Jarvis server binds to.
- ``JARVIS_VENV_PYTHON`` — Python executable used to spawn Jarvis.

API authentication
------------------
- ``JARVIS_API_TOKEN`` — bearer token required by protected HTTP endpoints.

Logging (ADR-021)
-----------------
- ``JARVIS_LOG_DIR`` — directory for rotated log files.
- ``JARVIS_LOG_LEVEL`` — logging level.
- ``JARVIS_LOG_RETENTION_DAYS`` — number of days to retain log files.

Attention / interruption policy
-------------------------------
- ``JARVIS_NOTIFY_ON_COMPLETION`` — notify when tasks complete.
- ``JARVIS_DEFAULT_SNOOZE_MINUTES`` — fallback snooze duration for deferrals.
- ``JARVIS_ATTENTION_RETRY_MINUTES`` — retry interval after SILENT/DEFER.
- ``JARVIS_QUIET_HOURS`` — local-time quiet-hours window.

Safe project aliases
--------------------
- ``JARVIS_PROJECTS_FILE`` — JSON file mapping aliases to project paths.

OS-provided paths
-----------------
- ``LOCALAPPDATA`` — Windows per-user application data directory.
- ``TEMP`` — Windows temporary directory.
"""

import os

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_TRUTHY_COMMON = ("true", "1", "yes")
_TRUTHY_ONE_ONLY = ("1",)
_TRUTHY_TRUE_ONLY = ("true",)


def _bool(
    name: str,
    default: bool,
    *,
    truthy: tuple[str, ...] = _TRUTHY_COMMON,
) -> bool:
    """Parse a boolean env var.

    The caller chooses the accepted truthy values so each call site keeps its
    original semantics (e.g. supervisor enabled only accepts ``"1"``).
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower().strip() in truthy


def _int(name: str, default: int, *, fallback_on_invalid: bool = False) -> int:
    """Parse an integer env var.

    By default an invalid value raises ``ValueError`` to preserve the
    behavior of bare ``int(os.environ.get(...))``. Callers that previously
    swallowed invalid input (e.g. WS token TTL) pass
    ``fallback_on_invalid=True``.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        if fallback_on_invalid:
            return default
        raise


def _str(name: str, default: str | None = None) -> str | None:
    """Read a string env var, returning ``default`` when unset."""
    return os.environ.get(name, default)


def _path(name: str, default: str | None = None) -> str | None:
    """Read a filesystem-path env var, returning ``default`` when unset."""
    return os.environ.get(name, default)


def _float(name: str, default: float) -> float:
    """Parse a float env var, returning ``default`` on invalid input."""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Repository / OS path helpers
# ---------------------------------------------------------------------------


def repo_root() -> str:
    """Absolute path to the repository root."""
    return _REPO_ROOT


def localappdata() -> str | None:
    """``LOCALAPPDATA`` — Windows per-user application data directory."""
    return _path("LOCALAPPDATA")


def temp_dir() -> str | None:
    """``TEMP`` — Windows temporary directory."""
    return _path("TEMP")


# ---------------------------------------------------------------------------
# LLM / Supervisor
# ---------------------------------------------------------------------------


def llm_provider() -> str:
    """JARVIS_LLM_PROVIDER — which provider adapter to use. Default: openai."""
    return _str("JARVIS_LLM_PROVIDER") or "openai"


def llm_base_url() -> str:
    """JARVIS_LLM_BASE_URL — provider API endpoint.

    Default: https://api.openai.com/v1
    """
    return _str("JARVIS_LLM_BASE_URL") or "https://api.openai.com/v1"


def llm_model() -> str:
    """JARVIS_LLM_MODEL — model identifier sent to the provider.

    Default: gpt-4o-mini
    """
    return _str("JARVIS_LLM_MODEL") or "gpt-4o-mini"


def llm_api_key() -> str:
    """JARVIS_LLM_API_KEY — provider API key. Default: empty string."""
    return _str("JARVIS_LLM_API_KEY") or ""


def groq_api_key() -> str:
    """GROQ_API_KEY — Groq API key for Whisper speech-to-text. Default: empty string."""
    return _str("GROQ_API_KEY") or ""


def llm_free_only() -> bool:
    """JARVIS_LLM_FREE_ONLY — filter routes to free models only.

    Default: false. Accepts "true", "1", "yes" (case-insensitive).
    """
    return _bool("JARVIS_LLM_FREE_ONLY", False)


def supervisor_enabled() -> bool:
    """JARVIS_SUPERVISOR_ENABLED — enable the conversational supervisor.

    Default: true. Accepts only "1" to match the original semantics.
    """
    return _bool("JARVIS_SUPERVISOR_ENABLED", True, truthy=_TRUTHY_ONE_ONLY)


def test_mode() -> bool:
    """JARVIS_TEST_MODE — set to "1" to enable test-only behavior."""
    return os.environ.get("JARVIS_TEST_MODE") == "1"


# ---------------------------------------------------------------------------
# OpenCode integration
# ---------------------------------------------------------------------------


def opencode_exe_default() -> str:
    """JARVIS_OPENCODE_EXE — path to the opencode executable.

    **FROZEN_AT_IMPORT**: captured once by ``app.integrations.opencode_server``.
    Default: C:\\Users\\Admin\\.bun\\bin\\opencode.exe
    """
    return _path("JARVIS_OPENCODE_EXE") or r"C:\Users\Admin\.bun\bin\opencode.exe"


def opencode_owner_marker_default() -> str:
    """JARVIS_OPENCODE_OWNER_MARKER — ownership marker file path.

    **FROZEN_AT_IMPORT**: captured once by ``app.integrations.opencode_server``.
    Default: .jarvis_opencode_owner.json in the repo root.
    """
    return _path("JARVIS_OPENCODE_OWNER_MARKER") or os.path.join(
        _REPO_ROOT, ".jarvis_opencode_owner.json"
    )


def opencode_port() -> int:
    """JARVIS_OPENCODE_PORT — port for the managed OpenCode server.

    Default: 4097
    """
    return _int("JARVIS_OPENCODE_PORT", 4097)


def opencode_runtime_dir() -> str:
    """JARVIS_OPENCODE_RUNTIME_DIR — isolated OpenCode runtime directory.

    Default: %LOCALAPPDATA%\\JarvisOpenCodeRuntime, falling back to %TEMP% or
    the user's home directory.
    """
    configured = _path("JARVIS_OPENCODE_RUNTIME_DIR")
    if configured:
        return configured
    base = localappdata() or temp_dir() or os.path.expanduser("~")
    return os.path.join(base, "JarvisOpenCodeRuntime")


def opencode_openrouter_key() -> str | None:
    """JARVIS_OPENCODE_OPENROUTER_KEY — OpenRouter key used to provision
    isolated OpenCode auth. Falls back to JARVIS_LLM_API_KEY at the call site.
    """
    return _str("JARVIS_OPENCODE_OPENROUTER_KEY")


def opencode_model_provider_id_default() -> str:
    """JARVIS_OPENCODE_MODEL_PROVIDER_ID — provider id for OpenCode delegation.

    **FROZEN_AT_IMPORT**: captured once by ``app.integrations.opencode_adapter``.
    Default: openrouter
    """
    return _str("JARVIS_OPENCODE_MODEL_PROVIDER_ID") or "openrouter"


def opencode_allow_paid() -> bool:
    """JARVIS_OPENCODE_ALLOW_PAID — allow paid models in OpenCode delegation.

    Default: false. Accepts "true", "1", "yes" (case-insensitive).
    """
    return _bool("JARVIS_OPENCODE_ALLOW_PAID", False)


def opencode_model_id() -> str | None:
    """JARVIS_OPENCODE_MODEL_ID — specific paid model id for OpenCode
    delegation. Default: unset.
    """
    return _str("JARVIS_OPENCODE_MODEL_ID")


def opencode_credit_threshold_usd() -> float:
    """JARVIS_OPENCODE_CREDIT_THRESHOLD_USD — minimum OpenRouter credit balance
    before paid delegation. Default: 1.0
    """
    return _float("JARVIS_OPENCODE_CREDIT_THRESHOLD_USD", 1.0)


# ---------------------------------------------------------------------------
# OpenCode server HTTP Basic auth
# ---------------------------------------------------------------------------


def opencode_server_username() -> str:
    """OPENCODE_SERVER_USERNAME — HTTP Basic auth username for opencode serve.

    Default: opencode
    """
    return _str("OPENCODE_SERVER_USERNAME") or "opencode"


def opencode_server_password() -> str:
    """OPENCODE_SERVER_PASSWORD — HTTP Basic auth password for opencode serve.

    Default: empty string.
    """
    return _str("OPENCODE_SERVER_PASSWORD") or ""


# ---------------------------------------------------------------------------
# WebSocket tokens (ADR-014)
# ---------------------------------------------------------------------------


def ws_token_secret() -> str | None:
    """JARVIS_WS_TOKEN_SECRET — HS256 signing secret. Default: unset
    (auto-generated per process by the consumer).
    """
    return _str("JARVIS_WS_TOKEN_SECRET")


def ws_token_ttl_seconds() -> int:
    """JARVIS_WS_TOKEN_TTL_SECONDS — token lifetime in seconds.

    Default: 900. Invalid values fall back to the default.
    """
    return _int("JARVIS_WS_TOKEN_TTL_SECONDS", 900, fallback_on_invalid=True)


# ---------------------------------------------------------------------------
# Web Push (VAPID)
# ---------------------------------------------------------------------------


def vapid_public_key() -> str | None:
    """JARVIS_VAPID_PUBLIC_KEY — VAPID public key exposed to browsers."""
    return _str("JARVIS_VAPID_PUBLIC_KEY")


def vapid_private_key() -> str | None:
    """JARVIS_VAPID_PRIVATE_KEY — VAPID private key (secret)."""
    return _str("JARVIS_VAPID_PRIVATE_KEY")


def vapid_subject() -> str:
    """JARVIS_VAPID_SUBJECT — VAPID subject claim.

    Default: mailto:admin@localhost
    """
    return _str("JARVIS_VAPID_SUBJECT") or "mailto:admin@localhost"


def vapid_configured() -> bool:
    """True when both VAPID keys are set."""
    return bool(vapid_private_key()) and bool(vapid_public_key())


# ---------------------------------------------------------------------------
# Operations console (ADR-022/023)
# ---------------------------------------------------------------------------


def operations_target_url_default() -> str:
    """JARVIS_OPERATIONS_TARGET_URL — Jarvis HTTPS endpoint the console controls.

    **FROZEN_AT_IMPORT**: captured once by ``app.operations_console``.
    Default: https://127.0.0.1:8443
    """
    return _str("JARVIS_OPERATIONS_TARGET_URL") or "https://127.0.0.1:8443"


def operations_port() -> int:
    """JARVIS_OPERATIONS_PORT — port the operations console HTTP server binds
    to. Default: 8500
    """
    return _int("JARVIS_OPERATIONS_PORT", 8500)


def operations_verify_tls_default() -> bool:
    """JARVIS_OPERATIONS_VERIFY_TLS — verify TLS when the console talks to
    Jarvis.

    **FROZEN_AT_IMPORT**: captured once by ``app.operations_console``.
    Default: false. Accepts only "true" (case-insensitive).
    """
    return _bool("JARVIS_OPERATIONS_VERIFY_TLS", False, truthy=_TRUTHY_TRUE_ONLY)


def operations_cwd() -> str:
    """JARVIS_OPERATIONS_CWD — working directory for the managed Jarvis
    process. Default: repo root.
    """
    return _path("JARVIS_OPERATIONS_CWD") or _REPO_ROOT


def operations_jarvis_owner_marker_default() -> str:
    """JARVIS_OPERATIONS_JARVIS_OWNER_MARKER — path to the Jarvis ownership
    marker file.

    **FROZEN_AT_IMPORT**: captured once by ``app.jarvis_process_manager``.
    Default: .jarvis_operations_jarvis_owner.json in the repo root.
    """
    return _path("JARVIS_OPERATIONS_JARVIS_OWNER_MARKER") or os.path.join(
        _REPO_ROOT, ".jarvis_operations_jarvis_owner.json"
    )


def operations_db() -> str:
    """JARVIS_OPERATIONS_DB — SQLite path for operation history.

    Default: %LOCALAPPDATA%\\JarvisOperationsConsole\\operations.db, falling
    back to the user's home directory.
    """
    configured = _path("JARVIS_OPERATIONS_DB")
    if configured:
        return configured
    base = localappdata() or os.path.expanduser("~")
    return os.path.join(base, "JarvisOperationsConsole", "operations.db")


def ssl_keyfile() -> str | None:
    """JARVIS_SSL_KEYFILE — TLS key file for HTTPS."""
    return _path("JARVIS_SSL_KEYFILE")


def ssl_certfile() -> str | None:
    """JARVIS_SSL_CERTFILE — TLS certificate file for HTTPS."""
    return _path("JARVIS_SSL_CERTFILE")


def bind_host() -> str:
    """JARVIS_BIND_HOST — host the main Jarvis server binds to.

    Default: 0.0.0.0
    """
    return _str("JARVIS_BIND_HOST") or "0.0.0.0"


def venv_python() -> str:
    """JARVIS_VENV_PYTHON — Python executable used to spawn Jarvis.

    Default: python
    """
    return _str("JARVIS_VENV_PYTHON") or "python"


# ---------------------------------------------------------------------------
# API authentication
# ---------------------------------------------------------------------------


def api_token() -> str | None:
    """JARVIS_API_TOKEN — bearer token required by protected HTTP endpoints.

    Default: unset (endpoints are unprotected).
    """
    return _str("JARVIS_API_TOKEN")


# ---------------------------------------------------------------------------
# Logging (ADR-021)
# ---------------------------------------------------------------------------


def log_dir() -> str:
    """JARVIS_LOG_DIR — directory for rotated log files. Default: logs."""
    return _str("JARVIS_LOG_DIR") or "logs"


def log_level() -> str:
    """JARVIS_LOG_LEVEL — logging level. Default: INFO."""
    return _str("JARVIS_LOG_LEVEL") or "INFO"


def log_retention_days() -> int:
    """JARVIS_LOG_RETENTION_DAYS — number of days to retain log files.

    Default: 14
    """
    return _int("JARVIS_LOG_RETENTION_DAYS", 14)


# ---------------------------------------------------------------------------
# Attention / interruption policy
# ---------------------------------------------------------------------------


def notify_on_completion() -> bool:
    """JARVIS_NOTIFY_ON_COMPLETION — notify when tasks complete.

    Default: true. Accepts "true", "1", "yes" (case-insensitive).
    """
    return _bool("JARVIS_NOTIFY_ON_COMPLETION", True)


def default_snooze_minutes() -> int | None:
    """JARVIS_DEFAULT_SNOOZE_MINUTES — fallback snooze duration for vague
    deferrals. Default: unset (asks for clarification). Invalid or
    non-positive values are treated as unset.
    """
    raw = _str("JARVIS_DEFAULT_SNOOZE_MINUTES")
    if raw is None:
        return None
    try:
        parsed = int(raw)
    except (ValueError, TypeError):
        return None
    return parsed if parsed > 0 else None


def attention_retry_minutes() -> int:
    """JARVIS_ATTENTION_RETRY_MINUTES — retry interval after SILENT/DEFER
    decisions. Default: 30
    """
    return _int("JARVIS_ATTENTION_RETRY_MINUTES", 30)


def quiet_hours() -> str | None:
    """JARVIS_QUIET_HOURS — local-time quiet-hours window "HH:MM-HH:MM".

    Default: unset (disabled).
    """
    return _str("JARVIS_QUIET_HOURS")


# ---------------------------------------------------------------------------
# Safe project aliases
# ---------------------------------------------------------------------------


def projects_file_default() -> str:
    """JARVIS_PROJECTS_FILE — JSON file mapping aliases to project paths.

    **FROZEN_AT_IMPORT**: captured once by ``app.supervisor.projects``;
    tests monkeypatch the module-level ``PROJECTS_FILE`` name.
    Default: projects.json in the repo root.
    """
    return _path("JARVIS_PROJECTS_FILE") or os.path.join(_REPO_ROOT, "projects.json")


# ---------------------------------------------------------------------------
# Subprocess environment builders
# ---------------------------------------------------------------------------

# OS-essential variables a Windows Bun/Node binary needs to run at all.
# Deliberately an allowlist: storage isolation (XDG_* above) isolates
# *where* the server reads/writes, but ``{**os.environ, ...}`` would still
# hand it every ambient credential on the machine (e.g. a machine-wide
# OPENAI_API_KEY). The isolated server's own OpenRouter credential comes
# from its isolated auth.json, never from the process environment at spawn
# time, so no provider credential needs to be in this list at all.
OS_ESSENTIAL_ENV_VARS = (
    "PATH",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "USERNAME",
    "APPDATA",
    "LOCALAPPDATA",
    "HOMEDRIVE",
    "HOMEPATH",
    "WINDIR",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_IDENTIFIER",
)


def os_essential_subprocess_env() -> dict[str, str]:
    """Build a minimal subprocess env dict from the OS-essential allowlist.

    Used when spawning an isolated opencode serve subprocess so it inherits
    only the variables it needs to run, not every ambient credential.
    """
    return {name: os.environ[name] for name in OS_ESSENTIAL_ENV_VARS if name in os.environ}


def subprocess_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Return a full inherited subprocess environment merged with extras.

    Used when spawning Jarvis from the Operations console, where the child
    process needs the console's complete environment.
    """
    return {**os.environ, **(extra or {})}
