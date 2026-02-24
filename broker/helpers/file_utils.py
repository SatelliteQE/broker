"""File handling utilities."""

import contextlib
from contextlib import contextmanager
from io import BytesIO
import json
import logging
from pathlib import Path
import tarfile
import time
from uuid import uuid4

from ruamel.yaml import YAML

from broker import exceptions

logger = logging.getLogger(__name__)
yaml = YAML()
yaml.default_flow_style = False
yaml.sort_keys = False


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


def save_file(file, data, mode="overwrite"):
    """Save data to a file, using appropriate format based on file extension.

    Args:
        file: Path to the file (string or Path object)
        data: The data to save. Can be dict, list, or string.
        mode: Write mode - "overwrite" (default) or "append"

    Returns:
        Path object to the saved file

    The format is determined by the file extension:
    - .json: Save as JSON with indentation
    - .yaml/.yml: Save as YAML
    - Other: Save as plain text (string conversion if needed)
    """
    file = Path(file)
    file.parent.mkdir(parents=True, exist_ok=True)

    # Determine format based on extension
    suffix = file.suffix.lower()

    if suffix == ".json":
        # For JSON, try to parse string data as JSON first
        if isinstance(data, str):
            with contextlib.suppress(json.JSONDecodeError):
                data = json.loads(data)
        content = json.dumps(data, indent=2, default=str)
    elif suffix in (".yaml", ".yml"):
        # For YAML, try to parse string data as structured data first
        if isinstance(data, str):
            data = _try_parse_structured_string(data)
        output = BytesIO()
        yaml.dump(data, output)
        content = output.getvalue().decode("utf-8")
    # Plain text - convert to string if needed
    elif isinstance(data, (dict, list)):
        content = yaml_format(data)
    else:
        content = str(data)

    # Write the content
    if mode == "append":
        with file.open("a") as f:
            f.write(content)
            if not content.endswith("\n"):
                f.write("\n")
    else:
        file.write_text(content)
        if not content.endswith("\n"):
            with file.open("a") as f:
                f.write("\n")

    logger.debug(f"Saved data to file: {file.absolute()}")
    return file


def _try_parse_structured_string(data):
    """Try to parse a string as JSON or YAML, returning original if parsing fails."""
    # Try JSON first
    with contextlib.suppress(json.JSONDecodeError):
        return json.loads(data)
    # Then try YAML
    with contextlib.suppress(Exception):
        return yaml.load(data)
    return data


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


@contextmanager
def data_to_tempfile(data, path=None, as_tar=False, suffix=""):
    """Write data to a temporary file and return the path."""
    if path:
        path = Path(path)
    else:
        path = Path(f"{uuid4().hex[-10]}{suffix}")

    logger.debug(f"Creating temporary file {path.absolute()}")
    try:
        if isinstance(data, bytes):
            path.write_bytes(data)
        elif isinstance(data, str):
            path.write_text(data)
        else:
            raise TypeError(f"data must be bytes or str, not {type(data)}")

        if as_tar:
            tar = tarfile.open(path)
            yield tar
            tar.close()
        else:
            yield path
    finally:
        if path.exists():
            path.unlink()


@contextmanager
def temporary_tar(paths):
    """Create a temporary tar file and return the path."""
    temp_tar = Path(f"{uuid4().hex[-10:]}.tar")
    with tarfile.open(temp_tar, mode="w") as tar:
        for path in paths:
            logger.debug(f"Adding {path.absolute()} to {temp_tar.absolute()}")
            tar.add(path, arcname=path.name)
    yield temp_tar.absolute()
    temp_tar.unlink()
