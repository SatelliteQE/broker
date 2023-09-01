"""Module handling internal and dependency logging."""
import copy
from enum import IntEnum
import logging

import awxkit
import logzero
import urllib3

from broker.settings import BROKER_DIRECTORY, settings


class LOG_LEVEL(IntEnum):
    """Bare class for log levels. Trace is added for custom logging."""

    TRACE = 5
    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARNING = logging.WARNING
    ERROR = logging.ERROR


class RedactingFilter(logging.Filter):
    """Custom logging.Filter to redact secrets from the Dynaconf config."""

    def __init__(self, sensitive):
        super().__init__()
        self._sensitive = sensitive

    def filter(self, record):
        """Filter the record and redact the sensitive keys."""
        if isinstance(record.args, dict):
            record.args = self.redact_dynaconf(record.args)
        else:
            record.args = tuple(self.redact_dynaconf(arg) for arg in record.args)
        return True

    def redact_dynaconf(self, data):
        """Go over the data and redact all values of keys that match the sensitive ones."""
        if isinstance(data, list | tuple):
            data_copy = [self.redact_dynaconf(item) for item in data]
        elif isinstance(data, dict):
            data_copy = copy.deepcopy(data)
            for k, v in data_copy.items():
                if isinstance(v, dict | list):
                    data_copy[k] = self.redact_dynaconf(v)
                elif k in self._sensitive and v:
                    data_copy[k] = "******"
        else:
            data_copy = data
        return data_copy


_sensitive = ["password", "pword", "token", "host_password"]
logging.addLevelName("TRACE", LOG_LEVEL.TRACE)
logzero.DEFAULT_COLORS[LOG_LEVEL.TRACE.value] = logzero.colors.Fore.MAGENTA


def patch_awx_for_verbosity(api):
    """Patch the awxkit API to log when we're at trace level."""
    client = api.client
    awx_log = client.log

    awx_log.parent = logzero.logger

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


def resolve_log_level(level):
    """Resolve the log level from a string."""
    try:
        log_level = LOG_LEVEL[level.upper()]
    except KeyError:
        log_level = LOG_LEVEL.INFO
    return log_level


def formatter_factory(log_level, color=True):
    """Create a logzero formatter based on the log level."""
    log_fmt = "%(color)s[%(levelname)s %(asctime)s]%(end_color)s %(message)s"
    debug_fmt = (
        "%(color)s[%(levelname)1.1s %(asctime)s %(module)s:%(lineno)d]%(end_color)s %(message)s"
    )
    formatter = logzero.LogFormatter(
        fmt=debug_fmt if log_level <= LOG_LEVEL.DEBUG else log_fmt, color=color
    )
    return formatter


def set_log_level(level=settings.logging.console_level):
    """Set the log level for logzero."""
    log_level = LOG_LEVEL.INFO if level == "silent" else resolve_log_level(level)
    logzero.formatter(formatter=formatter_factory(log_level))
    logzero.loglevel(level=log_level)


def set_file_logging(level=settings.logging.file_level, path="logs/broker.log"):
    """Set the file logging for logzero."""
    silent = False
    if level == "silent":
        silent = True
        log_level = LOG_LEVEL.INFO
    else:
        log_level = resolve_log_level(level)
    path = BROKER_DIRECTORY.joinpath(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    logzero.logfile(
        path,
        loglevel=log_level.value,
        maxBytes=1e9,
        backupCount=3,
        formatter=formatter_factory(log_level, color=False),
        disableStderrLogger=silent,
    )


def setup_logzero(
    level=settings.logging.console_level,
    formatter=None,
    file_level=settings.logging.file_level,
    name=None,
    path="logs/broker.log",
):
    """Call logzero setup with the given settings."""
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    if isinstance(level, str) and level.lower() == "trace":
        patch_awx_for_verbosity(awxkit.api)
    set_log_level(level)
    set_file_logging(file_level, path)
    if formatter:
        logzero.formatter(formatter)
    logzero.logger.name = name or "broker"
    logzero.logger.addFilter(RedactingFilter(_sensitive))


setup_logzero()
