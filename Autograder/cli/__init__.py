"""
Command-line interface components for the autograder.

This module provides argument parsing and logging configuration.
"""

from .args import parse_args, configure_logging
from .validators import (
    resolve_reveal_identity,
    resolve_idempotency_settings,
    resolve_records_dir,
)

__all__ = [
    "parse_args",
    "configure_logging",
    "resolve_reveal_identity",
    "resolve_idempotency_settings",
    "resolve_records_dir",
]
