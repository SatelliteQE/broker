"""Module providing classes to establish ssh or ssh-like connections to hosts.

Classes:
    Session - Wrapper around ssh2-python's auth/connection system.
    InteractiveShell - Wrapper around ssh2-python's non-blocking channel system.
    ContainerSession - Wrapper around docker-py's exec system.

Note: You typically want to use a Host object instance to create sessions,
      not these classes directly.
"""

from contextlib import contextmanager
from pathlib import Path
import tempfile

from logzero import logger

from broker import helpers
from broker.exceptions import NotImplementedError
from broker.settings import settings

SSH_BACKENDS = ("ssh2-python", "ssh2-python312", "ansible-pylibssh", "hussh")
SSH_BACKEND = settings.SSH.BACKEND

logger.debug(f"{SSH_BACKEND=}")


SSH_NOT_SUPPORTED_MSG = (
    f"SSH backend {SSH_BACKEND!r} not supported.\nSupported ssh backends:\n{SSH_BACKENDS}"
)
SSH_NOT_INSTALLED_MSG = (
    f"{SSH_BACKEND} is not installed.\n"
    "ssh actions will not work.\n"
    f"To use ssh, run 'pip install broker[{SSH_BACKEND}]'."
)
SSH_IMPORT_MSG = ""

try:
    if SSH_BACKEND == "ansible-pylibssh":
        from broker.binds.pylibssh import InteractiveShell, Session
    elif SSH_BACKEND == "hussh":
        from broker.binds.hussh import Session
    elif SSH_BACKEND in ("ssh2-python", "ssh2-python312"):
        from broker.binds.ssh2 import InteractiveShell, Session  # noqa: F401
    else:
        SSH_IMPORT_MSG = SSH_NOT_SUPPORTED_MSG
except ImportError:
    SSH_IMPORT_MSG = SSH_IMPORT_MSG or SSH_NOT_INSTALLED_MSG
finally:
    if SSH_IMPORT_MSG:
        logger.warning(SSH_IMPORT_MSG)

        class Session:
            """Default wrapper around ssh backend's auth/connection system."""

            def __init__(self, **kwargs):
                """Initialize a Session object."""


class ContainerSession:
    """An approximation of ssh-based functionality from the Session class."""

    def __init__(self, cont_inst, runtime=None):
        self._cont_inst = cont_inst
        if not runtime:
            runtime = settings.CONTAINER.runtime
        self.runtime = runtime

    def run(self, command, demux=True, **kwargs):
        """Container approximation of Session.run."""
        kwargs.pop("timeout", None)  # Timeouts are set at the client level
        kwargs["demux"] = demux
        if "'" in command:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".sh") as tmp:
                tmp.write(command)
                tmp.seek(0)
                command = f"/bin/bash {tmp.name}"
                self.sftp_write(tmp.name)
        if any(s in command for s in "|&><"):
            # Containers don't handle pipes, redirects, etc well in a bare exec_run
            command = f"/bin/bash -c '{command}'"
        result = self._cont_inst._cont_inst.exec_run(command, **kwargs)
        if demux:
            result = helpers.Result.from_duplexed_exec(result, self.runtime)
        else:
            result = helpers.Result.from_nonduplexed_exec(result)
        return result

    def disconnect(self):
        """Needed for simple compatibility with Session."""

    @contextmanager
    def tail_file(self, filename):
        """Simulate tailing a file on the remote host."""
        initial_size = int(self.run(f"stat -c %s {filename}").stdout.strip())
        yield (res := helpers.Result())
        # get the contents of the file from the initial size to the end
        result = self.run(f"tail -c +{initial_size} {filename}")
        res.__dict__.update(result.__dict__)

    def sftp_write(self, source, destination=None, ensure_dir=True):
        """Add one of more files to the container."""
        # ensure source is a list of Path objects
        if not isinstance(source, list):
            source = [Path(source)]
        else:
            source = [Path(src) for src in source]
        # validate each source's existenence
        for src in source:
            if not Path(src).exists():
                raise FileNotFoundError(src)
        destination = destination or f"{source[0].parent}/"
        # Files need to be added to a tarfile
        with helpers.temporary_tar(source) as tar:
            logger.debug(f"{self._cont_inst.hostname} adding file(s) {source} to {destination}")
            if ensure_dir:
                if destination.endswith("/"):
                    self.run(f"mkdir -m 666 -p {destination}")
                else:
                    self.run(f"mkdir -m 666 -p {Path(destination).parent}")
            self._cont_inst._cont_inst.put_archive(str(destination), tar.read_bytes())

    def sftp_read(self, source, destination=None, return_data=False):
        """Get a file or directory from the container."""
        destination = Path(destination or source)
        logger.debug(f"{self._cont_inst.hostname} getting file {source}")
        data, status = self._cont_inst._cont_inst.get_archive(source)
        logger.debug(f"{self._cont_inst.hostname}: {status}")
        data = b"".join(d for d in data)
        if destination.name == "_raw":
            return data
        with helpers.data_to_tempfile(data, as_tar=True) as tar:
            del data
            if len(tar.getmembers()) == 1:
                f = tar.extractfile(tar.getmember(Path(source).name))
                if return_data:
                    logger.debug(f"Extracting {source}")
                    return f.read()
                else:
                    logger.debug(f"Extracting {source} to {destination}")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(f.read())
            else:
                logger.warning("More than one member was found in the tar file.")
                tar.extractall(destination.parent if destination.is_file() else destination)

    def shell(self, pty=False):
        """Create and return an interactive shell instance."""
        raise NotImplementedError("ContainerSession.shell has not been implemented")
