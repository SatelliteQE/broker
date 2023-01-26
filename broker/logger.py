# -*- encoding: utf-8 -*-
"""Module handling internal and dependency logging."""
from enum import IntEnum
import logging
import logzero
import urllib3
from broker.settings import BROKER_DIRECTORY, settings

import awxkit


class LOG_LEVEL(IntEnum):
    TRACE = 5
    DEBUG = logging.DEBUG
    INFO = logging.INFO
    VARNING = logging.WARNING
    ERROR = logging.ERROR


logging.addLevelName("TRACE", LOG_LEVEL.TRACE)


def patch_awx_for_verbosity(api):
    client = api.client
    awx_log = client.log

    awx_log.parent = logzero.logger

    def patch(cls, name):
        func = getattr(cls, name)

        def the_patch(self, *args, **kwargs):
            awx_log.log(LOG_LEVEL.TRACE.value, f"Calling  {self=} {func=}(*{args=}, **{kwargs=}")
            retval = func(self, *args, **kwargs)
            awx_log.log(LOG_LEVEL.TRACE.value, f"Finished {self=} {func=}(*{args=}, **{kwargs=}) {retval=}")
            return retval
        setattr(cls, name, the_patch)

    for method in "delete get head options patch post put".split():
        patch(api.Connection, method)


def resolve_log_level(level) -> LOG_LEVEL:
    try:
        log_level = LOG_LEVEL[level.upper()]
    except KeyError:
        log_level = LOG_LEVEL.INFO
    return log_level


def formatter_factory(log_level):
    log_fmt = "%(color)s[%(levelname)s %(asctime)s]%(end_color)s %(message)s"
    debug_fmt = (
        "%(color)s[%(levelname)1.1s %(asctime)s %(module)s:%(lineno)d]"
        "%(end_color)s %(message)s"
    )
    formatter = logzero.LogFormatter(
        fmt=debug_fmt if log_level <= LOG_LEVEL.DEBUG else log_fmt
    )
    return formatter


def set_log_level(level: str):
    silent = False
    if level == "silent":
        silent = True
        log_level = LOG_LEVEL.INFO

    log_level = resolve_log_level(level)
    logzero.setup_default_logger(level=log_level, formatter=formatter_factory(log_level), disableStderrLogger=silent)
    set_file_logging(log_level)
    if log_level is not logzero.logger.getEffectiveLevel() or silent:
        if not silent:
            print(f"Log level changed to [{log_level.name}]")


def set_file_logging(log_level=LOG_LEVEL.INFO, path="logs/broker.log"):
    path = BROKER_DIRECTORY.joinpath(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    logzero.logfile(
        path, loglevel=log_level.value, maxBytes=1e9, backupCount=3, formatter=formatter_factory(log_level), color=False
    )


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
patch_awx_for_verbosity(awxkit.api)
set_file_logging()
