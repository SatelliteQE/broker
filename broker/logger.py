# -*- encoding: utf-8 -*-
"""Module handling internal and dependency logging."""
import copy
from enum import IntEnum
import logging
import logzero
import urllib3
from broker.settings import BROKER_DIRECTORY, settings
from dynaconf.vendor.box.box_list import BoxList
from dynaconf.vendor.box.box import Box

import awxkit


class LOG_LEVEL(IntEnum):
    TRACE = 5
    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARNING = logging.WARNING
    ERROR = logging.ERROR


_sensitive = ["password", "pword", "token", "host_password"]
_old_factory = None
logging.addLevelName("TRACE", LOG_LEVEL.TRACE)
logzero.DEFAULT_COLORS[LOG_LEVEL.TRACE.value] = logzero.colors.Fore.MAGENTA


def patch_awx_for_verbosity(api):
    client = api.client
    awx_log = client.log

    awx_log.parent = logzero.logger

    def patch(cls, name):
        func = getattr(cls, name)

        def the_patch(self, *args, **kwargs):
            awx_log.log(
                LOG_LEVEL.TRACE.value, f"Calling {self=} {func=}(*{args=}, **{kwargs=}"
            )
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
    try:
        log_level = LOG_LEVEL[level.upper()]
    except KeyError:
        log_level = LOG_LEVEL.INFO
    return log_level


def formatter_factory(log_level, color=True):
    log_fmt = "%(color)s[%(levelname)s %(asctime)s]%(end_color)s %(message)s"
    debug_fmt = (
        "%(color)s[%(levelname)1.1s %(asctime)s %(module)s:%(lineno)d]"
        "%(end_color)s %(message)s"
    )
    formatter = logzero.LogFormatter(
        fmt=debug_fmt if log_level <= LOG_LEVEL.DEBUG else log_fmt, color=color
    )
    return formatter


def broker_record_factory(*args, **kwargs):
    """Factory to create a redacted logging.LogRecord"""
    record = _old_factory(*args, **kwargs)
    args_new = []
    for arg in record.args:
        if isinstance(arg, (tuple, Box, BoxList)):
            args_new.append(redact_dynaconf(arg))
        else:
            args_new.append(arg)
    record.args = tuple(args_new)
    return record


def redact_dynaconf(data):
    if isinstance(data, (list, tuple)):
        data_copy = [redact_dynaconf(item) for item in data]
    elif isinstance(data, dict):
        data_copy = copy.deepcopy(data)
        for k, v in data_copy.items():
            if isinstance(v, (dict, list)):
                data_copy[k] = redact_dynaconf(v)
            elif k in _sensitive and v:
                data_copy[k] = "*" * 6
    else:
        data_copy = data
    return data_copy


def set_log_level(level=settings.logging.console_level):
    if level == "silent":
        log_level = LOG_LEVEL.INFO
    else:
        log_level = resolve_log_level(level)
    logzero.formatter(formatter=formatter_factory(log_level))
    logzero.loglevel(level=log_level)


def set_file_logging(level=settings.logging.file_level, path="logs/broker.log"):
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
    file_level=settings.logging.file_level,
    path="logs/broker.log",
):
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    patch_awx_for_verbosity(awxkit.api)
    set_log_level(level)
    set_file_logging(file_level, path)
    global _old_factory
    lrf = logging.getLogRecordFactory()
    if lrf.__name__ is not broker_record_factory.__name__:
        _old_factory = lrf
        logging.setLogRecordFactory(broker_record_factory)


setup_logzero()
