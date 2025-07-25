"""Miscellaneous helpers live here."""

import collections
from collections import UserDict, namedtuple
from collections.abc import MutableMapping
from contextlib import contextmanager
from copy import deepcopy
import getpass
import inspect
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import tarfile
import threading
import time
from uuid import uuid4

import click
from logzero import logger
from rich.table import Table
from ruamel.yaml import YAML

from broker import exceptions, logger as b_log, settings

FilterTest = namedtuple("FilterTest", "haystack needle test")
INVENTORY_LOCK = threading.Lock()

yaml = YAML()
yaml.default_flow_style = False
yaml.sort_keys = False

SPECIAL_INVENTORY_FIELDS = {}  # use the _special_inventory_field decorator to add new fields


def _special_inventory_field(action_name):
    """Register inventory field actions."""

    def decorator(func):
        SPECIAL_INVENTORY_FIELDS[action_name] = func
        return func

    return decorator


def clean_dict(in_dict):
    """Remove entries from a dict where value is None."""
    return {k: v for k, v in in_dict.items() if v is not None}


def merge_dicts(dict1, dict2):
    """Merge two nested dictionaries together.

    :return: merged dictionary
    """
    if not isinstance(dict1, MutableMapping) or not isinstance(dict2, MutableMapping):
        return dict1
    dict1 = clean_dict(dict1)
    dict2 = clean_dict(dict2)
    merged = {}
    dupe_keys = dict1.keys() & dict2.keys()
    for key in dupe_keys:
        merged[key] = merge_dicts(dict1[key], dict2[key])
    for key in dict1.keys() - dupe_keys:
        merged[key] = deepcopy(dict1[key])
    for key in dict2.keys() - dupe_keys:
        merged[key] = deepcopy(dict2[key])
    return merged


def flatten_dict(nested_dict, parent_key="", separator="_"):
    """Flatten a nested dictionary, keeping nested notation in key.

    {
        'key': 'value1',
        'another': {
            'nested': 'value2',
            'nested2': [1, 2, {'deep': 'value3'}]
        }
    }
    becomes
    {
        "key": "value",
        "another_nested": "value2",
        "another_nested2": [1, 2],
        "another_nested2_deep": "value3"
    }
    note that dictionaries nested in lists will be removed from the list.

    :return: dictionary
    """
    flattened = []
    for key, value in nested_dict.items():
        new_key = f"{parent_key}{separator}{key}" if parent_key else key
        if isinstance(value, dict):
            flattened.extend(flatten_dict(value, new_key, separator).items())
        elif isinstance(value, list):
            to_remove = []
            # avoid mutating nested structures
            value = value.copy()  # noqa: PLW2901
            for index, val in enumerate(value):
                if isinstance(val, dict):
                    flattened.extend(flatten_dict(val, new_key, separator).items())
                    to_remove.append(index)
            for index in to_remove[::-1]:  # remove from back to front
                del value[index]
            flattened.append((new_key, value))
        else:
            flattened.append((new_key, value))
    return dict(flattened)


def dict_from_paths(source_dict, paths, sep="/"):
    """Given a dictionary of desired keys and nested paths, return a new dictionary.

    Example:
        source_dict = {
            "key1": "value1",
            "key2": {
                "nested1": "value2",
                "nested2": {
                    "deep": "value3"
                }
            }
        }
        paths = {
            "key1": "key1",
            "key2": "key2/nested2/deep"
        }
        returns {
            "key1": "value1",
            "key2": "value3"
        }
    """
    result = {}
    for key, path in paths.items():
        if sep not in path:
            result[key] = source_dict.get(path)
        else:
            top, rem = path.split(sep, 1)
            result.update(dict_from_paths(source_dict[top], {key: rem}))
    return result


def eval_filter(filter_list, raw_filter, filter_key="inv"):
    """Run each filter through an eval to get the results."""
    filter_list = [MockStub(item) if isinstance(item, dict) else item for item in filter_list]
    for raw_f in raw_filter.split("|"):
        if f"@{filter_key}[" in raw_f:
            # perform a list filter on the inventory
            filter_list = eval(  # noqa: S307
                raw_f.replace(f"@{filter_key}", filter_key), {filter_key: filter_list}
            )
            filter_list = filter_list if isinstance(filter_list, list) else [filter_list]
        elif f"@{filter_key}" in raw_f:
            # perform an attribute filter on each host
            filter_list = list(
                filter(
                    lambda item: eval(  # noqa: S307
                        raw_f.replace(f"@{filter_key}", filter_key), {filter_key: item}
                    ),
                    filter_list,
                )
            )
    return [dict(item) if isinstance(item, MockStub) else item for item in filter_list]


def resolve_nick(nick):
    """Check if the nickname exists. Used to define broker arguments.

    :param nick: String representing the name of a nick

    :return: a dictionary mapping argument names and values
    """
    nick_names = settings.settings.get("NICKS") or {}
    if nick in nick_names:
        return settings.settings.NICKS[nick].to_dict()
    else:
        raise exceptions.UserError(f"Unknown nick: {nick}")


def load_file(file, warn=True):
    """Verify the existence of and load data from json and yaml files."""
    file = Path(file)
    if not file.exists() or file.suffix not in (".json", ".yaml", ".yml"):
        if warn:
            logger.warning(f"File {file.absolute()} is invalid or does not exist.")
        return []
    if file.suffix == ".json":
        return json.loads(file.read_text())
    elif file.suffix in (".yaml", ".yml"):
        return yaml.load(file)


def resolve_file_args(broker_args):
    """Check for files being passed in as values to arguments then attempt to resolve them.

    If not resolved, keep arg/value pair intact.
    """
    final_args = {}
    # parse the eventual args_file first
    if val := broker_args.pop("args_file", None):
        if isinstance(val, Path) or (isinstance(val, str) and val[-4:] in ("json", "yaml", ".yml")):
            if data := load_file(val):
                if isinstance(data, dict):
                    final_args.update(data)
                elif isinstance(data, list):
                    for d in data:
                        final_args.update(d)
            else:
                raise exceptions.BrokerError(f"No data loaded from {val}")

    for key, val in broker_args.items():
        if isinstance(val, Path) or (isinstance(val, str) and val[-4:] in ("json", "yaml", ".yml")):
            if data := load_file(val):
                final_args.update({key: data})
            else:
                final_args.update({key: val})
        else:
            final_args.update({key: val})
    return final_args


def load_inventory(filter=None):
    """Load all local hosts in inventory.

    :return: list of dictionaries
    """
    inv_data = load_file(settings.inventory_path, warn=False)
    if inv_data and filter:
        inv_data = eval_filter(inv_data, filter)
    return inv_data or []


def update_inventory(add=None, remove=None):
    """Update list of local hosts in the checkout interface.

    :param add: list of dictionaries representing new hosts

    :param remove: list of strings representing hostnames or names to be removed

    :return: no return value
    """
    if add and not isinstance(add, list):
        add = [add]
    elif not add:
        add = []
    if remove and not isinstance(remove, list):
        remove = [remove]
    with INVENTORY_LOCK:
        inv_data = load_inventory()
        if inv_data:
            settings.inventory_path.unlink()

        if remove:
            for host in inv_data[::-1]:
                if host["hostname"] in remove or host.get("name") in remove:
                    # iterate through new hosts and update with old host data if it would nullify
                    for new_host in add:
                        if host["hostname"] == new_host["hostname"] or host.get(
                            "name"
                        ) == new_host.get("name"):
                            # update missing data in the new_host with the old_host data
                            new_host.update(merge_dicts(new_host, host))
                    inv_data.remove(host)
        if add:
            inv_data.extend(add)

        settings.inventory_path.touch()
        yaml.dump(inv_data, settings.inventory_path)


def yaml_format(in_struct, force_yaml_dict=False):
    """Convert a yaml-compatible structure to a yaml dumped string.

    :param in_struct: yaml-compatible structure or string containing structure
    :param force_yaml_dict: force the in_struct to be converted to a dictionary before dumping

    :return: yaml-formatted string
    """
    if isinstance(in_struct, str):
        # first try to load is as json
        try:
            in_struct = json.loads(in_struct)
        except json.JSONDecodeError:
            # then try yaml
            in_struct = yaml.load(in_struct)
            if force_yaml_dict:
                in_struct = dict(in_struct)
    output = BytesIO()  # ruamel doesn't natively allow for string output
    yaml.dump(in_struct, output)
    return output.getvalue().decode("utf-8")


def flip_provider_actions(provider_actions):
    """Flip the mapping of actions->provider to provider->actions."""
    flipped = {}
    for action, (provider, _) in provider_actions.items():
        provider_name = provider.__name__
        if provider_name not in flipped:
            flipped[provider_name] = []
        flipped[provider_name].append(action)
    return flipped


def inventory_fields_to_dict(inventory_fields, host_dict, **extras):
    """Convert a dicionary-like representation of inventory fields to a resolved dictionary.

    inventory fields, as set in the config look like this, in yaml:
    inventory_fields:
        Host: hostname | name
        Provider: _broker_provider
        Action: $action
        OS: os_distribution os_distribution_version

    We then process that into a dictionary with inventory values like this:
    {
        "Host": "some.test.host",
        "Provider": "AnsibleTower",
        "Action": "deploy-rhel",
        "OS": "RHEL 8.4"
    }

    Notes: The special syntax use in Host and Action fields <$action> is a special keyword that
    represents a more complex field resolved by Broker.
    Also, the Host field represents a priority  order of single values,
    so if hostname is not present, name will be used.
    Finally, spaces between values are preserved. This lets us combine multiple values in a single field.
    """
    return {
        name: _resolve_inv_field(field, host_dict, **extras)
        for name, field in inventory_fields.items()
    }


def _resolve_inv_field(field, host_dict, **extras):
    """Real functionality for inventory_fields_to_dict, allows recursive evaluation."""
    # Users can specify multiple values to try in order of priority, so evaluate each
    if "|" in field:
        resolved = [_resolve_inv_field(f.strip(), host_dict, **extras) for f in field.split("|")]
        for val in resolved:
            if val and val != "Unknown":
                return val
        return "Unknown"
    # Users can combine multiple values in a single field, so evaluate each
    if " " in field:
        return " ".join(_resolve_inv_field(f, host_dict, **extras) for f in field.split())
    # Some field values require special handling beyond what the existing syntax allows
    if special_field_func := SPECIAL_INVENTORY_FIELDS.get(field):
        return special_field_func(host_dict, **extras)
    # Otherwise, try to get the value from the host dictionary
    return dict_from_paths(host_dict, {"_": field}, sep=".")["_"] or "Unknown"


@_special_inventory_field("$action")
def get_host_action(host_dict, provider_actions=None, **_):
    """Get a more focused set of fields from the host inventory."""
    if not provider_actions:
        return "$actionError"
    # Flip the mapping of actions->provider to provider->actions
    flipped_actions = {}
    for action, (provider, _) in provider_actions.items():
        provider_name = provider.__name__
        if provider_name not in flipped_actions:
            flipped_actions[provider_name] = []
        flipped_actions[provider_name].append(action)
    # Get the host's action, based on its provider
    provider = host_dict["_broker_provider"]
    for opt in flipped_actions[provider]:
        if action := host_dict["_broker_args"].get(opt):
            return action
    return "Unknown"


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


class MockStub(UserDict):
    """Test helper class. Allows for both arbitrary mocking and stubbing."""

    def __init__(self, in_dict=None):
        """Initialize the class and all nested dictionaries."""
        if in_dict is None:
            in_dict = {}
        for key, value in in_dict.items():
            if isinstance(value, dict):
                setattr(self, key, MockStub(value))
            elif type(value) in (list, tuple):
                setattr(
                    self,
                    key,
                    [MockStub(x) if isinstance(x, dict) else x for x in value],
                )
            else:
                setattr(self, key, value)
        super().__init__(in_dict)

    def __getattr__(self, name):
        """Fallback to returning self if attribute doesn't exist."""
        return self

    def __getitem__(self, key):
        """Get an item from the dictionary-like object.

        If the key is a string, this method will attempt to get an attribute with that name.
        If the key is not found, this method will return the object itself.
        """
        if isinstance(key, str):
            item = getattr(self, key, self)
        try:
            item = super().__getitem__(key)
        except KeyError:
            item = self
        return item

    def __call__(self, *args, **kwargs):
        """Allow MockStub to be used like a function."""
        return self

    def __hash__(self):
        """Return a hash value for the object.

        The hash value is computed using the hash value of all hashable attributes of the object.
        """
        return hash(
            tuple(kp for kp in self.__dict__.items() if isinstance(kp[1], collections.abc.Hashable))
        )


def update_log_level(ctx, param, value):
    """Update the log level and file logging settings for the Broker.

    Args:
        ctx: The Click context object.
        param: The Click parameter object.
        value: The new log level value.
    """
    b_log.set_log_level(value)
    b_log.set_file_logging(value)


def set_emit_file(ctx, param, value):
    """Update the file that the Broker emits data to."""
    emit.set_file(value)


def fork_broker():
    """Fork the Broker process to run in the background."""
    pid = os.fork()
    if pid:
        logger.info(f"Running broker in the background with pid: {pid}")
        sys.exit(0)
    b_log.set_log_level("silent")
    b_log.set_file_logging("silent")


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


def simple_retry(cmd, cmd_args=None, cmd_kwargs=None, max_timeout=60, _cur_timeout=1):
    """Re(Try) a function given its args and kwargs up until a max timeout."""
    cmd_args = cmd_args if cmd_args else []
    cmd_kwargs = cmd_kwargs if cmd_kwargs else {}
    try:
        return cmd(*cmd_args, **cmd_kwargs)
    except Exception as err:
        new_wait = _cur_timeout * 2
        if new_wait > max_timeout:
            raise err
        logger.warning(
            f"Tried {cmd=} with {cmd_args=}, {cmd_kwargs=} but received {err=}"
            f"\nTrying again in {_cur_timeout} seconds."
        )
        time.sleep(_cur_timeout)
        simple_retry(cmd, cmd_args, cmd_kwargs, max_timeout, new_wait)


class FileLock:
    """Basic file locking class that acquires and releases locks.

    Recommended usage is the context manager which will handle everything for you

    with FileLock("basic_file.txt"):
        Path("basic_file.txt").write_text("some text")

    If a lock is already in place, FileLock will wait up to <timeout> seconds
    """

    def __init__(self, file_name, timeout=10):
        self.lock = Path(f"{file_name}.lock")
        self.timeout = timeout

    def wait_file(self):
        """Wait for the lock file to be released, then acquire it."""
        timeout_after = time.time() + self.timeout
        while self.lock.exists():
            if time.time() <= timeout_after:
                time.sleep(1)
            else:
                raise exceptions.BrokerError(
                    f"Timeout while waiting for lock release: {self.lock.absolute()}"
                )
        self.lock.touch()

    def return_file(self):
        """Release the lock file."""
        self.lock.unlink()

    def __enter__(self):  # noqa: D105
        self.wait_file()

    def __exit__(self, *tb_info):  # noqa: D105
        self.return_file()


class Result:
    """Dummy result class for presenting results in dot access."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __repr__(self):
        """Return a string representation of the object."""
        return f"stdout:\n{self.stdout}\nstderr:\n{self.stderr}\nstatus: {self.status}"

    @classmethod
    def from_ssh(cls, stdout, channel):
        """Create a Result object from an SSH channel."""
        return cls(
            stdout=stdout,
            status=channel.get_exit_status(),
            stderr=channel.read_stderr()[1].decode("utf-8"),
        )

    @classmethod
    def from_duplexed_exec(cls, duplex_exec, runtime=None):
        """Create a Result object from a duplexed exec object from podman or docker."""
        if runtime == "podman":
            status, (stdout, stderr) = duplex_exec
            return cls(
                status=status,
                stdout=stdout.decode("utf-8") if stdout else "",
                stderr=stderr.decode("utf-8") if stderr else "",
            )

        if duplex_exec.output[0]:
            stdout = duplex_exec.output[0].decode("utf-8")
        else:
            stdout = ""
        if duplex_exec.output[1]:
            stderr = duplex_exec.output[1].decode("utf-8")
        else:
            stderr = ""
        return cls(
            status=duplex_exec.exit_code,
            stdout=stdout,
            stderr=stderr,
        )

    @classmethod
    def from_nonduplexed_exec(cls, nonduplex_exec):
        """Create a Result object from a nonduplexed exec object from the docker library."""
        return cls(
            status=nonduplex_exec.exit_code,
            stdout=nonduplex_exec.output.decode("utf-8"),
            stderr="",
        )


def find_origin():
    """Move up the call stack to find tests, fixtures, or cli invocations.

    Additionally, return the jenkins url, if it exists.
    """
    prev, jenkins_url = None, os.environ.get("BUILD_URL")
    for frame in inspect.stack():
        if frame.function == "checkout" and frame.filename.endswith("broker/commands.py"):
            return f"broker_cli:{getpass.getuser()}", jenkins_url
        if frame.function.startswith("test_"):
            return f"{frame.function}:{frame.filename}", jenkins_url
        if frame.function == "call_fixture_func":
            # attempt to find the test name from the fixture's request object
            if request := _frame.frame.f_locals.get("request"):  # noqa: F821
                return f"{prev} for {request.node._nodeid}", jenkins_url
            # otherwise, return the fixture name and filename
            return prev or "Uknown fixture", jenkins_url
        prev, _frame = f"{frame.function}:{frame.filename}", frame
    return f"Unknown origin by {getpass.getuser()}", jenkins_url


@contextmanager
def data_to_tempfile(data, path=None, as_tar=False):
    """Write data to a temporary file and return the path."""
    path = Path(path or uuid4().hex[-10])
    logger.debug(f"Creating temporary file {path.absolute()}")
    if isinstance(data, bytes):
        path.write_bytes(data)
    elif isinstance(data, str):
        path.write_text(data)
    else:
        raise TypeError(f"data must be bytes or str, not {type(data)}")
    if as_tar:
        tar = tarfile.open(path)
        yield tarfile.open(path)
        tar.close()
    else:
        yield path
    path.unlink()


@contextmanager
def temporary_tar(paths):
    """Create a temporary tar file and return the path."""
    temp_tar = Path(f"{uuid4().hex[-10]}.tar")
    with tarfile.open(temp_tar, mode="w") as tar:
        for path in paths:
            logger.debug(f"Adding {path.absolute()} to {temp_tar.absolute()}")
            tar.add(path, arcname=path.name)
    yield temp_tar.absolute()
    temp_tar.unlink()


def dictlist_to_table(dict_list, title=None, _id=False):
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
    return table
