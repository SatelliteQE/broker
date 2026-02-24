"""Miscellaneous helper functions and classes."""

import getpass
import inspect
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time

import click
from rich.table import Table

from broker import exceptions
from broker.settings import clone_global_settings

logger = logging.getLogger(__name__)


def resolve_nick(nick, broker_settings=None):
    """Check if the nickname exists. Used to define broker arguments.

    :param nick: String representing the name of a nick
    :param broker_settings: Optional settings object to use instead of global settings

    :return: a dictionary mapping argument names and values
    """
    _settings = broker_settings or clone_global_settings()
    nick_names = _settings.get("NICKS") or {}
    if nick in nick_names:
        return _settings.NICKS[nick].to_dict()
    else:
        raise exceptions.UserError(f"Unknown nick: {nick}")


def kwargs_from_click_ctx(ctx):
    """Convert a Click context object to a dictionary of keyword arguments."""
    # if users use `=` to note arg=value assignment, then we need to split it
    _args = []
    for arg in ctx.args:
        if "=" in arg:
            _args.extend(arg.split("="))
        else:
            _args.append(arg)
    ctx.args = _args
    # if additional arguments were passed, include them in the broker args
    # strip leading -- characters
    return {
        (key[2:] if key.startswith("--") else key): val
        for key, val in zip(ctx.args[::2], ctx.args[1::2])
    }


class Emitter:
    """Class that provides a simple interface to emit messages to a json-formatted file.

    This module also has an instance of this class called "emit" that should be used
    instead of this class directly.

    Usage examples:
        helpers.emit(key=value, another=5)
        helpers.emit({"key": "value", "another": 5})
    """

    EMIT_LOCK = threading.Lock()

    def __init__(self, emit_file=None):
        """Can empty init and set the file later."""
        self.file = None
        if emit_file:
            self.file = self.set_file(emit_file)

    def set_file(self, file_path):
        """Set the file to emit to."""
        if file_path:
            self.file = Path(file_path)
            self.file.parent.mkdir(exist_ok=True, parents=True)
            if self.file.exists():
                self.file.unlink()
            self.file.touch()

    def emit_to_file(self, *args, **kwargs):
        """Emit data to the file, keeping existing data in-place."""
        if not self.file:
            return
        for arg in args:
            if not isinstance(arg, dict):
                raise exceptions.BrokerError(f"Received an invalid data emission {arg}")
            kwargs.update(arg)
        for key, val in kwargs.items():
            if getattr(val, "json", None):
                kwargs[key] = val.json
        with self.EMIT_LOCK:
            curr_data = json.loads(self.file.read_text() or "{}")
            curr_data.update(kwargs)
            self.file.write_text(json.dumps(curr_data, indent=4, sort_keys=True))

    def __call__(self, *args, **kwargs):
        """Allow emit to be used like a function."""
        return self.emit_to_file(*args, **kwargs)


emit = Emitter()


def update_log_level(ctx, param, value):
    """Update the log level and file logging settings for the Broker.

    Args:
        ctx: The Click context object.
        param: The Click parameter object.
        value: The new log level value.
    """
    from broker.logging import setup_logging

    setup_logging(console_level=value)


def set_emit_file(ctx, param, value):
    """Update the file that the Broker emits data to."""
    emit.set_file(value)


def fork_broker():
    """Fork the Broker process to run in the background."""
    pid = os.fork()
    if pid:
        logger.info(f"Running broker in the background with pid: {pid}")
        sys.exit(0)
    from broker.logging import setup_logging

    setup_logging(console_level="silent", file_level="silent")


def handle_keyboardinterrupt(*args):
    """Handle keyboard interrupts gracefully.

    Offer the user a choice between keeping Broker alive in the background, killing it, or resuming execution.
    """
    choice = click.prompt(
        "\nEnding Broker while running may not end processes being monitored.\n"
        "Would you like to switch Broker to run in the Background, Kill it, or Resume execution?\n",
        type=click.Choice(["b", "k", "r"]),
        default="r",
    ).lower()
    if choice == "b":
        fork_broker()
    elif choice == "k":
        raise exceptions.BrokerError("Broker killed by user.")
    elif choice == "r":
        click.echo("Resuming execution...")


def translate_timeout(timeout):
    """Allow for flexible timeout definitions, converts other units to ms.

    acceptable units are (s)econds, (m)inutes, (h)ours, (d)ays
    """
    if isinstance(timeout, str):
        timeout, unit = int(timeout[:-1]), timeout[-1]
        if unit == "d":
            timeout *= 24
            unit = "h"
        if unit == "h":
            timeout *= 60
            unit = "m"
        if unit == "m":
            timeout *= 60
            unit = "s"
        if unit == "s":
            timeout *= 1000
    return timeout if isinstance(timeout, int) else 0


def simple_retry(
    cmd, cmd_args=None, cmd_kwargs=None, max_timeout=60, _cur_timeout=1, terminal_exceptions=None
):
    """Re(Try) a function given its args and kwargs up until a max timeout."""
    cmd_args = cmd_args if cmd_args else []
    cmd_kwargs = cmd_kwargs if cmd_kwargs else {}
    terminal_exceptions = terminal_exceptions or ()

    try:
        return cmd(*cmd_args, **cmd_kwargs)
    except terminal_exceptions:
        raise
    except Exception as err:
        new_wait = _cur_timeout * 2
        if new_wait > max_timeout:
            raise err
        logger.warning(
            f"Tried {cmd=} with {cmd_args=}, {cmd_kwargs=} but received {err=}"
            f"\nTrying again in {_cur_timeout} seconds."
        )
        time.sleep(_cur_timeout)
        return simple_retry(cmd, cmd_args, cmd_kwargs, max_timeout, new_wait, terminal_exceptions)


def find_origin():
    """Move up the call stack to find tests, fixtures, or cli invocations.

    Additionally, return the jenkins url, if it exists.
    """
    prev, _frame, jenkins_url = None, None, os.environ.get("BUILD_URL")
    for frame in inspect.stack():
        if frame.function == "checkout" and frame.filename.endswith("broker/commands.py"):
            return f"broker_cli:{getpass.getuser()}", jenkins_url
        if frame.function.startswith("test_"):
            return f"{frame.function}:{frame.filename}", jenkins_url
        if frame.function == "call_fixture_func":
            # attempt to find the test name from the fixture's request object
            if _frame and (request := _frame.frame.f_locals.get("request")):
                return f"{prev} for {request.node._nodeid}", jenkins_url
            # otherwise, return the fixture name and filename
            return prev or "Unknown fixture", jenkins_url
        prev, _frame = f"{frame.function}:{frame.filename}", frame
    return f"Unknown origin by {getpass.getuser()}", jenkins_url


def dictlist_to_table(dict_list, title=None, _id=False, headers=True):
    """Convert a list of dictionaries to a rich table."""
    # I like pretty colors, so let's cycle through them
    column_colors = ["cyan", "magenta", "green", "yellow", "blue", "red"]
    curr_color = 0
    table = Table(title=title)
    # construct the columns
    if _id:  # likely just for inventory tables
        table.add_column("Id", justify="left", style=column_colors[curr_color], no_wrap=True)
        curr_color += 1
    for key in dict_list[0]:  # assume all dicts have the same keys
        table.add_column(key, justify="left", style=column_colors[curr_color])
        curr_color += 1
        if curr_color >= len(column_colors):
            curr_color = 0
    # add the rows
    for id_num, data_dict in enumerate(dict_list):
        row = [str(id_num)] if _id else []
        row.extend([str(value) for value in data_dict.values()])
        table.add_row(*row)
    if not headers:
        table.show_header = False
    return table


def dict_to_table(in_dict, title=None, headers=None):
    """Convert a dictionary into a rich table."""
    # need to normalize the values first
    in_dict = {k: str(v) for k, v in in_dict.items()}
    table = Table(title=title)
    if isinstance(headers, tuple | list):
        table.add_column(headers[0], style="cyan")
        table.add_column(headers[1], style="magenta")
    else:
        table.add_column("key", style="cyan")
        table.add_column("value", style="magenta")
        table.show_header = False
    for key, val in in_dict.items():
        table.add_row(key, val)
    return table
