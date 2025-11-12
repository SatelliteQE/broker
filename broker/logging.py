"""Modern logging configuration for Broker.

This module provides logging setup that supports two distinct modes:
1. CLI Mode: Beautiful rich console output + structured JSON file logging
2. Library Mode: No handler configuration (consumers handle their own logging)
"""

import copy
from enum import IntEnum
import logging
from pathlib import Path

import click
from pythonjsonlogger import jsonlogger
from rich.logging import RichHandler

from broker.settings import BROKER_DIRECTORY


class _LoggingState:
    """Class to hold current logging state."""

    console_level = "info"
    file_level = "debug"
    log_path = "logs/broker.log"
    structured = False


class LOG_LEVEL(IntEnum):
    """Log levels with custom TRACE level."""

    TRACE = 5
    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARNING = logging.WARNING
    ERROR = logging.ERROR


class RedactingFilter(logging.Filter):
    """Custom logging.Filter to redact secrets from log records."""

    def __init__(self, sensitive):
        super().__init__()
        self._sensitive = sensitive

    def filter(self, record):
        """Filter the record and redact the sensitive keys."""
        if isinstance(record.args, dict):
            record.args = self.redact_sensitive(record.args)
        else:
            record.args = tuple(self.redact_sensitive(arg) for arg in record.args)
        return True

    def redact_sensitive(self, data):
        """Recursively redact sensitive data."""
        if isinstance(data, list | tuple):
            data_copy = [self.redact_sensitive(item) for item in data]
        elif isinstance(data, dict):
            data_copy = copy.deepcopy(data)
            for k, v in data_copy.items():
                if isinstance(v, dict | list):
                    data_copy[k] = self.redact_sensitive(v)
                elif k in self._sensitive and v:
                    data_copy[k] = "******"
        else:
            data_copy = data
        return data_copy


# Register custom TRACE level
logging.addLevelName(LOG_LEVEL.TRACE, "TRACE")

# Sensitive fields to redact
_SENSITIVE_FIELDS = ["password", "pword", "token", "host_password"]


def resolve_log_level(level):
    """Resolve log level from string or int to LOG_LEVEL enum."""
    if isinstance(level, int):
        # Map standard logging levels to our enum
        for log_level in LOG_LEVEL:
            if log_level.value == level:
                return log_level
        return LOG_LEVEL.INFO

    if isinstance(level, str):
        try:
            return LOG_LEVEL[level.upper()]
        except KeyError:
            return LOG_LEVEL.INFO

    return LOG_LEVEL.INFO


def setup_logging(
    console_level=None,
    file_level=None,
    log_path=None,
    structured=None,
):
    """Configure logging for Broker CLI mode.

    This function should ONLY be called by the CLI entry point.
    When broker is used as a library, logging should not be configured.

    Args:
        console_level: Logging level for console output (string or logging level)
        file_level: Logging level for file output (string or logging level)
        log_path: Path to log file (can be directory or full file path)
        structured: If True, create additional JSON log file alongside text logs
    """
    # Use existing settings if parameters are None
    if console_level is None:
        console_level = _LoggingState.console_level
    if file_level is None:
        file_level = _LoggingState.file_level
    if log_path is None:
        log_path = _LoggingState.log_path
    if structured is None:
        structured = _LoggingState.structured

    # Save current settings for future calls
    _LoggingState.console_level = console_level
    _LoggingState.file_level = file_level
    _LoggingState.log_path = log_path
    _LoggingState.structured = structured

    root_logger = logging.getLogger()
    # Clear any existing handlers to avoid duplicates on reconfiguration
    root_logger.handlers.clear()

    # Resolve log levels
    console_log_level = resolve_log_level(console_level)
    file_log_level = resolve_log_level(file_level)

    # Set root logger to lowest level so handlers can filter
    root_logger.setLevel(min(console_log_level.value, file_log_level.value))

    # Add redacting filter to root logger
    root_logger.addFilter(RedactingFilter(_SENSITIVE_FIELDS))

    # Skip handler setup if level is "silent"
    if console_level != "silent":
        # Console handler with rich formatting
        console_handler = RichHandler(
            rich_tracebacks=True,
            tracebacks_suppress=[click],
            show_time=True,
            show_path=console_log_level <= LOG_LEVEL.DEBUG,
            markup=True,
        )
        console_handler.setLevel(console_log_level.value)

        # Use more verbose format for debug/trace
        if console_log_level <= LOG_LEVEL.DEBUG:
            console_format = "%(message)s"
        else:
            console_format = "%(message)s"

        console_handler.setFormatter(logging.Formatter(console_format, datefmt="%d%b %H:%M:%S"))
        root_logger.addHandler(console_handler)

    # Text file handler (always created unless file_level is "silent")
    if file_level != "silent":
        # Resolve log path
        log_file_path = Path(log_path)
        if not log_file_path.is_absolute():
            log_file_path = BROKER_DIRECTORY / log_path

        # If path is a directory, append default filename
        if log_file_path.suffix == "":
            log_file_path = log_file_path / "broker.log"

        # Ensure parent directory exists
        log_file_path.parent.mkdir(parents=True, exist_ok=True)

        # Create rotating file handler for text logs
        from logging.handlers import RotatingFileHandler

        text_file_handler = RotatingFileHandler(
            log_file_path,
            maxBytes=int(1e9),  # 1GB
            backupCount=3,
        )
        text_file_handler.setLevel(file_log_level.value)

        # Standard text logging
        text_file_handler.setFormatter(
            logging.Formatter(
                "[%(levelname)s %(asctime)s %(name)s:%(lineno)d] %(message)s",
                datefmt="%d%b %H:%M:%S",
            )
        )
        root_logger.addHandler(text_file_handler)

        # JSON file handler (additional, if structured logging is enabled)
        if structured:
            # Create JSON log file path by replacing extension
            json_log_path = log_file_path.with_suffix(log_file_path.suffix + ".json")

            json_file_handler = RotatingFileHandler(
                json_log_path,
                maxBytes=int(1e9),  # 1GB
                backupCount=3,
            )
            json_file_handler.setLevel(file_log_level.value)

            # JSON structured logging
            json_file_handler.setFormatter(
                jsonlogger.JsonFormatter(
                    "%(asctime)s %(name)s %(levelname)s %(message)s",
                    rename_fields={"asctime": "timestamp", "levelname": "level", "name": "logger"},
                    datefmt="%d%b %H:%M:%S",
                )
            )
            root_logger.addHandler(json_file_handler)


def try_disable_urllib3_warnings():
    """Attempt to disable urllib3 InsecureRequestWarning if urllib3 is available."""
    try:
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except ImportError:
        pass


def try_patch_awx_for_verbosity():
    """Patch the awxkit API to enable trace-level logging of API calls."""
    try:
        from awxkit import api
    except ImportError:
        return

    awx_log = logging.getLogger("awxkit.api")

    def patch(cls, name):
        func = getattr(cls, name)

        def the_patch(self, *args, **kwargs):
            awx_log.log(LOG_LEVEL.TRACE.value, f"Calling {self=} {func=}(*{args=}, **{kwargs=}")
            retval = func(self, *args, **kwargs)
            awx_log.log(
                LOG_LEVEL.TRACE.value,
                f"Finished {self=} {func=}(*{args=}, **{kwargs=}) {retval=}",
            )
            return retval

        setattr(cls, name, the_patch)

    for method in "delete get head options patch post put".split():
        patch(api.Connection, method)


# Apply patches on module import (these are safe for library mode)
try_disable_urllib3_warnings()
try_patch_awx_for_verbosity()
