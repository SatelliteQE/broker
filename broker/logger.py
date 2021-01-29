# -*- encoding: utf-8 -*-
"""Module handling internal and dependency logging."""
import logging
from pathlib import Path
import logzero
from broker.settings import BROKER_DIRECTORY


def setup_logzero(level="info", path="logs/broker.log", silent=False):
    path = BROKER_DIRECTORY.joinpath(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    log_fmt = "%(color)s[%(levelname)s %(asctime)s]%(end_color)s %(message)s"
    debug_fmt = (
        "%(color)s[%(levelname)1.1s %(asctime)s %(module)s:%(lineno)d]"
        "%(end_color)s %(message)s"
    )
    log_level = getattr(logging, level.upper(), logging.INFO)

    formatter = logzero.LogFormatter(
        fmt=debug_fmt if log_level is logging.DEBUG else log_fmt
    )
    logzero.setup_default_logger(formatter=formatter, disableStderrLogger=silent)
    logzero.loglevel(log_level)
    logzero.logfile(
        path, loglevel=log_level, maxBytes=1e9, backupCount=3, formatter=formatter
    )


setup_logzero()
