"""Miscellaneous helpers live here"""
import collections
from contextlib import contextmanager
import getpass
import inspect
import json
import logging
import os
import pickle
import sys
import tarfile
import time
from collections import UserDict, namedtuple
from collections.abc import MutableMapping
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import yaml
from logzero import logger

from broker import exceptions, settings
from broker import logger as b_log

FilterTest = namedtuple("FilterTest", "haystack needle test")


def clean_dict(in_dict):
    """Remove entries from a dict where value is None"""
    return {k: v for k, v in in_dict.items() if v is not None}


def merge_dicts(dict1, dict2):
    """Merge two nested dictionaries together

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
    """Flatten a nested dictionary, keeping nested notation in key
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
    note that dictionaries nested in lists will be removed from the list

    :return: dictionary
    """

    flattened = []
    for key, value in nested_dict.items():
        new_key = f"{parent_key}{separator}{key}" if parent_key else key
        if isinstance(value, dict):
            flattened.extend(flatten_dict(value, new_key, separator).items())
        elif isinstance(value, list):
            to_remove = []
            value = value.copy()  # avoid mutating nested structures
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


def classify_filter(filter_string):
    """Given a filter string, determine the filter action and components"""
    tests = {
        "!<": "'{needle}' not in '{haystack}'",
        "<": "'{needle}' in '{haystack}'",
        "!=": "'{haystack}' != '{needle}'",
        "=": "'{haystack}' == '{needle}'",
        "!{": "not '{haystack}'.startswith('{needle}')",
        "{": "'{haystack}'.startswith('{needle}')",
        "!}": "not '{haystack}'.endswith('{needle}')",
        "}": "'{haystack}'.endswith('{needle}')",
    }
    if "," in filter_string:
        return [classify_filter(f) for f in filter_string.split(",")]
    for cond, test in tests.items():
        if cond in filter_string:
            k, v = filter_string.split(cond)
            return FilterTest(haystack=k, needle=v, test=test)


def inventory_filter(inventory, raw_filter):
    """Filter out inventory items depending on the filter provided"""
    resolved_filter = classify_filter(raw_filter)
    if not isinstance(resolved_filter, list):
        resolved_filter = [resolved_filter]
    matching = []
    for host in inventory:
        flattened_host = flatten_dict(host, separator=".")
        eval_list = [
            eval(rf.test.format(haystack=flattened_host[rf.haystack], needle=rf.needle))
            for rf in resolved_filter
            if rf.haystack in flattened_host
        ]
        if eval_list and all(eval_list):
            matching.append(host)
    return matching


def results_filter(results, raw_filter):
    """Filter out a list of results depending on the filter provided"""
    resolved_filter = classify_filter(raw_filter)
    if not isinstance(resolved_filter, list):
        resolved_filter = [resolved_filter]
    matching = []
    for res in results:
        eval_list = [
            eval(rf.test.format(haystack=res, needle=rf.needle))
            for rf in resolved_filter
        ]
        if eval_list and all(eval_list):
            matching.append(res)
    return matching


def resolve_nick(nick):
    """Checks if the nickname exists. Used to define broker arguments

    :param nick: String representing the name of a nick

    :return: a dictionary mapping argument names and values
    """
    nick_names = settings.settings.get("NICKS") or {}
    if nick in nick_names:
        return settings.settings.NICKS[nick].to_dict()


def load_file(file, warn=True):
    """Verifies existence and loads data from json and yaml files"""
    file = Path(file)
    if not file.exists() or file.suffix not in (".json", ".yaml", ".yml"):
        if warn:
            logger.warning(f"File {file.absolute()} is invalid or does not exist.")
        return []
    loader_args = {}
    if file.suffix == ".json":
        loader = json
    elif file.suffix in (".yaml", ".yml"):
        loader = yaml
        loader_args = {"Loader": yaml.FullLoader}
    with file.open() as f:
        data = loader.load(f, **loader_args) or []
    return data


def resolve_file_args(broker_args):
    """Check for files being passed in as values to arguments,
    then attempt to resolve them. If not resolved, keep arg/value pair intact.
    """
    final_args = {}
    # parse the eventual args_file first
    if val := broker_args.pop('args_file', None):
        if isinstance(val, Path) or (
            isinstance(val, str) and val[-4:] in ("json", "yaml", ".yml")
        ):
            if data := load_file(val):
                if isinstance(data, dict):
                    final_args.update(data)
                elif isinstance(data, list):
                    for d in data:
                        final_args.update(d)
            else:
                raise exceptions.BrokerError(f"No data loaded from {val}")

    for key, val in broker_args.items():
        if isinstance(val, Path) or (
            isinstance(val, str) and val[-4:] in ("json", "yaml", ".yml")
        ):
            if data := load_file(val):
                final_args.update({key: data})
            else:
                final_args.update({key: val})
        else:
            final_args.update({key: val})
    return final_args


def load_inventory(filter=None):
    """Loads all local hosts in inventory

    :return: list of dictionaries
    """
    inventory_file = settings.BROKER_DIRECTORY.joinpath(
        settings.settings.INVENTORY_FILE
    )
    inv_data = load_file(inventory_file, warn=False)
    return inv_data if not filter else inventory_filter(inv_data, filter)


def update_inventory(add=None, remove=None):
    """Updates list of local hosts in the checkout interface

    :param add: list of dictionaries representing new hosts

    :param remove: list of strings representing hostnames or names to be removed

    :return: no return value
    """
    inventory_file = settings.BROKER_DIRECTORY.joinpath(
        settings.settings.INVENTORY_FILE
    )
    if add and not isinstance(add, list):
        add = [add]
    if remove and not isinstance(remove, list):
        remove = [remove]
    with FileLock(inventory_file):
        inv_data = load_inventory()
        if inv_data:
            inventory_file.unlink()

        if remove:
            for host in inv_data[::-1]:
                if host["hostname"] in remove or host["name"] in remove:
                    inv_data.remove(host)
        if add:
            inv_data.extend(add)

        inventory_file.touch()
        with inventory_file.open("w") as inv_file:
            yaml.dump(inv_data, inv_file)


def yaml_format(in_struct):
    """Convert a yaml-compatible structure to a yaml dumped string

    :param in_struct: yaml-compatible structure or string containing structure

    :return: yaml-formatted string
    """
    if isinstance(in_struct, str):
        in_struct = yaml.load(in_struct, Loader=yaml.FullLoader)
    return yaml.dump(in_struct, default_flow_style=False, sort_keys=False)


class Emitter:
    """This class provides a simple interface to emit messages to a
    json-formatted file. This file also has an instance of this class
    called "emit" that should be used instead of this class directly.

    Usage examples:
        helpers.emit(key=value, another=5)
        helpers.emit({"key": "value", "another": 5})
    """

    def __init__(self, emit_file=None):
        """Can empty init and set the file later"""
        self.file = None
        if emit_file:
            self.file = self.set_file(emit_file)

    def set_file(self, file_path):
        if file_path:
            self.file = Path(file_path)
            self.file.parent.mkdir(exist_ok=True, parents=True)
            if self.file.exists():
                self.file.unlink()
            self.file.touch()

    def emit_to_file(self, *args, **kwargs):
        if not self.file:
            return
        for arg in args:
            if not isinstance(arg, dict):
                raise exceptions.BrokerError(f"Received an invalid data emission {arg}")
            kwargs.update(arg)
        for key in kwargs.keys():
            if getattr(kwargs[key], "json", None):
                kwargs[key] = kwargs[key].json
        with FileLock(self.file):
            curr_data = json.loads(self.file.read_text() or "{}")
            curr_data.update(kwargs)
            self.file.write_text(json.dumps(curr_data, indent=4, sort_keys=True))

    def __call__(self, *args, **kwargs):
        return self.emit_to_file(*args, **kwargs)


emit = Emitter()


class MockStub(UserDict):
    """Test helper class. Allows for both arbitrary mocking and stubbing"""

    def __init__(self, in_dict=None):
        """Initialize the class and all nested dictionaries"""
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
        return self

    def __getitem__(self, key):
        if isinstance(key, str):
            item = getattr(self, key, self)
        try:
            item = super().__getitem__(key)
        except KeyError:
            item = self
        return item

    def __call__(self, *args, **kwargs):
        return self

    def __hash__(self):
        return hash(
            tuple(
                (
                    kp
                    for kp in self.__dict__.items()
                    if isinstance(kp[1], collections.abc.Hashable)
                )
            )
        )


def update_log_level(ctx, param, value):
    silent = False
    if value == "silent":
        silent = True
        value = "info"
    if getattr(logging, value.upper()) is not logger.getEffectiveLevel() or silent:
        b_log.setup_logzero(level=value, silent=silent)
        if not silent:
            print(f"Log level changed to [{value}]")


def set_emit_file(ctx, param, value):
    global emit
    emit.set_file(value)


def fork_broker():
    pid = os.fork()
    if pid:
        logger.info(f"Running broker in the background with pid: {pid}")
        sys.exit(0)
    update_log_level(None, None, "silent")


def handle_keyboardinterrupt(*args):
    choice = input(
        "\nEnding Broker while running won't end processes being monitored.\n"
        "Would you like to switch Broker to run in the background?\n"
        "[y/n]: "
    )
    if choice.lower()[0] == "y":
        fork_broker()
    else:
        raise exceptions.BrokerError("Broker killed by user.")


def translate_timeout(timeout):
    """Allows for flexible timeout definitions, converts other units to ms

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
    """Re(Try) a function given its args and kwargs up until a max timeout"""
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
    """Basic file locking class that acquires and releases locks
    recommended usage is the context manager which will handle everythign for you

    with FileLock("basic_file.txt") as basic_file:
        basic_file.write("some text")

    basic_file is a Path object of the desired file
    If a lock is already in place, FileLock will wait up to <timeout> seconds
    """

    def __init__(self, file_name, timeout=10):
        self.file = Path(file_name)
        self.lock = Path(f"{self.file}.lock")
        self.timeout = timeout

    def wait_file(self):
        start = time.time()
        while self.lock.exists():
            if (time.time() - start) < self.timeout:
                time.sleep(1)
                continue
            else:
                raise exceptions.BrokerError(
                    f"Timeout while attempting to open {self.file.absolute()}"
                )
        self.lock.touch()
        return self.file

    def return_file(self):
        self.lock.unlink()

    def __enter__(self):
        return self.wait_file()

    def __exit__(self, *tb_info):
        self.return_file()


class Result:
    """Dummy result class for presenting results in dot access"""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __repr__(self):
        return f"stdout:\n{self.stdout}\nstderr:\n{self.stderr}\nstatus: {self.status}"

    @classmethod
    def from_ssh(cls, stdout, channel):
        return cls(
            stdout=stdout,
            status=channel.get_exit_status(),
            stderr=channel.read_stderr(),
        )

    @classmethod
    def from_duplexed_exec(cls, duplex_exec):
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
        if frame.function == "checkout" and frame.filename.endswith(
            "broker/commands.py"
        ):
            return f"broker_cli:{getpass.getuser()}", jenkins_url
        if frame.function.startswith("test_"):
            return f"{frame.function}:{frame.filename}", jenkins_url
        if frame.function == "call_fixture_func":
            # attempt to find the test name from the fixture's request object
            if request := _frame.frame.f_locals.get("request"):
                return f"{prev} for {request.node._nodeid}", jenkins_url
            # otherwise, return the fixture name and filename
            return prev or "Uknown fixture", jenkins_url
        prev, _frame = f"{frame.function}:{frame.filename}", frame
    return f"Unknown origin by {getpass.getuser()}", jenkins_url


@contextmanager
def data_to_tempfile(data, path=None, as_tar=False):
    """Write data to a temporary file and return the path"""
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
    """Create a temporary tar file and return the path"""
    temp_tar = Path(f"{uuid4().hex[-10]}.tar")
    with tarfile.open(temp_tar, mode="w") as tar:
        for path in paths:
            logger.debug(f"Adding {path.absolute()} to {temp_tar.absolute()}")
            tar.add(path, arcname=path.name)
    yield temp_tar.absolute()
    temp_tar.unlink()


class PickleSafe:
    """A class that helps with pickling and unpickling complex objects"""

    def _pre_pickle(self):
        """This method is called before pickling an object"""
        pass

    def _post_pickle(self, purified):
        """This method is called after pickling an object

        purified will be a list of names of attributes that were removed
        """
        pass

    def _purify(self):
        """Strip all unpickleable attributes from a Host before pickling"""
        self.purified = getattr(self, "purified", [])
        for name in list(self.__dict__):
            self._purify_target = name
            try:
                pickle.dumps(self.__dict__[name])
            except (pickle.PicklingError, AttributeError):
                self.__dict__[name] = None
                self.purified.append(name)

    def __getstate__(self):
        """If a session is active, remove it for pickle compatability"""
        self._pre_pickle()
        try:
            self._purify()
        except RecursionError:
            logger.warning(f"Recursion limit reached on {self._purify_target}")
            self.__dict__[self._purify_target] = None
            self.__getstate__()
        self.__dict__.pop("_purify_target", None)
        return self.__dict__

    def __setstate__(self, state):
        """Sometimes pickle strips things that we need. This should be used to restore them"""
        self.__dict__.update(state)
        self._post_pickle(purified=getattr(self, "purified", []))
