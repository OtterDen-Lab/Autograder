"""
Configuration validators and resolvers for the autograder CLI.
"""

import argparse
import getpass
import json
import logging
import os
from datetime import datetime, timezone

from Autograder.config_models import RunConfig

log = logging.getLogger(__name__)


def resolve_reveal_identity(args: argparse.Namespace,
                            config: RunConfig) -> bool:
    """
    Resolve whether identity reveal mode should be enabled.

    This is a break-glass feature that requires explicit environment
    variable authorization.

    Args:
        args: Command line arguments
        config: Loaded run configuration

    Returns:
        True if identity reveal is enabled and authorized, False otherwise

    Raises:
        SystemExit: If reveal is requested but AUTOGRADER_BREAK_GLASS=1 is not set
    """
    requested = bool(getattr(args, "reveal_identity", False)
                     or config.reveal_identity)
    if not requested:
        return False

    if os.getenv("AUTOGRADER_BREAK_GLASS") != "1":
        raise SystemExit(
            "Identity reveal requested, but AUTOGRADER_BREAK_GLASS=1 is not set. "
            "Set AUTOGRADER_BREAK_GLASS=1 for this run, or disable --reveal-identity/reveal_identity."
        )

    log.warning(
        "Break-glass identity reveal is enabled; Canvas numeric IDs may appear in logs."
    )
    _record_reveal_identity_event(args, config)
    return True


def _record_reveal_identity_event(args: argparse.Namespace,
                                  config: RunConfig) -> None:
    """
    Write an audit record whenever break-glass identity reveal is used.

    Args:
        args: Command line arguments
        config: Loaded run configuration
    """
    path = os.getenv("AUTOGRADER_REVEAL_AUDIT_LOG",
                     "~/.autograder/privacy/reveal_identity_audit.log")
    audit_path = os.path.abspath(os.path.expanduser(path))
    try:
        os.makedirs(os.path.dirname(audit_path), exist_ok=True)
        payload = {
            "timestamp_utc":
            datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
                "+00:00", "Z"),
            "user": getpass.getuser(),
            "pid": os.getpid(),
            "yaml_path": getattr(args, "yaml", None),
            "privacy_mode": getattr(config, "privacy_mode", None),
            "prod": bool(getattr(config, "prod", False)),
        }
        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")
        try:
            os.chmod(audit_path, 0o600)
        except Exception:
            pass
    except Exception as e:
        log.warning(f"Failed to write reveal-identity audit event: {e}")


def resolve_idempotency_settings(
        args: argparse.Namespace, config: RunConfig) -> tuple[str | None, str]:
    """
    Resolve idempotency key and state directory from CLI args and config.

    CLI arguments take precedence over config file settings.

    Args:
        args: Command line arguments
        config: Loaded run configuration

    Returns:
        Tuple of (idempotency_key, state_dir)
    """
    idempotency_key = getattr(args, "idempotency_key", None)
    if idempotency_key is None:
        idempotency_key = config.idempotency_key
    if isinstance(idempotency_key, str):
        idempotency_key = idempotency_key.strip() or None

    state_dir = (getattr(args, "idempotency_state_dir", None)
                 or config.idempotency_state_dir
                 or "~/.autograder/idempotency")
    state_dir = os.path.abspath(os.path.expanduser(state_dir))
    return idempotency_key, state_dir


def _repo_root_dir() -> str:
    """Return the root directory of the repository."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _is_subpath(path: str, parent: str) -> bool:
    """Check if path is a subpath of parent."""
    try:
        return os.path.commonpath([path, parent]) == parent
    except ValueError:
        return False


def resolve_records_dir(records_dir: str | None) -> str:
    """
    Validate and resolve records directory path.

    Args:
        records_dir: Raw records directory path from config

    Returns:
        Resolved absolute path to records directory

    Raises:
        ValueError: If records_dir is invalid or inside the repository
    """
    if not isinstance(records_dir, str) or not records_dir.strip():
        raise ValueError(
            "Config error: record_retention=true requires an explicit records_dir in config "
            "(use an absolute path or ~/...).")

    raw = records_dir.strip()
    expanded = os.path.expanduser(raw)
    if not os.path.isabs(expanded):
        raise ValueError(
            "Config error: records_dir must be an absolute path (or use ~/...) when record_retention=true."
        )

    resolved = os.path.realpath(os.path.abspath(expanded))
    repo_root = _repo_root_dir()

    if os.getenv("AUTOGRADER_ALLOW_IN_REPO_RECORDS") != "1" and _is_subpath(
            resolved, repo_root):
        raise ValueError(
            f"Config error: records_dir must be outside the repository root ({repo_root}) to avoid accidental git history leakage. "
            "Set AUTOGRADER_ALLOW_IN_REPO_RECORDS=1 only for local debugging.")

    return resolved


def resolve_learning_logs_dir(learning_logs_dir: str | None) -> str:
    """Validate and resolve the directory used for per-student learning logs."""
    if not isinstance(learning_logs_dir, str) or not learning_logs_dir.strip():
        raise ValueError(
            "Config error: learning_logs_dir must be an explicit absolute path "
            "(or use ~/...).")

    raw = learning_logs_dir.strip()
    expanded = os.path.expanduser(raw)
    if not os.path.isabs(expanded):
        raise ValueError(
            "Config error: learning_logs_dir must be an absolute path (or use ~/...).")

    resolved = os.path.realpath(os.path.abspath(expanded))
    repo_root = _repo_root_dir()
    if os.getenv("AUTOGRADER_ALLOW_IN_REPO_RECORDS") != "1" and _is_subpath(
            resolved, repo_root):
        raise ValueError(
            f"Config error: learning_logs_dir must be outside the repository root ({repo_root}) "
            "to avoid accidental git history leakage. Set "
            "AUTOGRADER_ALLOW_IN_REPO_RECORDS=1 only for local debugging.")

    return resolved
